from __future__ import annotations

import ast
import re
from pathlib import Path

from codegraph_mcp.analyzers.base import AnalysisResult, LanguageAnalyzer, SourceFile
from codegraph_mcp.graph.models import (
    EdgeType,
    FunctionDescriptor,
    FunctionEdge,
    FunctionKind,
    FunctionNode,
    RuntimeBinding,
)
from codegraph_mcp.utils import normalize_ws, sha256_text, stable_id

CALL_NAME_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_\.]{1,120})\s*\(")

# ── Framework binding patterns ────────────────────────────────────────────────
_FASTAPI_DECO = re.compile(r"@(?:app|router)\.(get|post|put|patch|delete|head|options|websocket)\((['\"]([^'\"]+)['\"])?")
_FLASK_DECO   = re.compile(r"@(?:app|bp|blueprint)\.(route|get|post|put|patch|delete)\((['\"]([^'\"]+)['\"])?")
_DJANGO_URL   = re.compile(r"path\(['\"]([^'\"]*)['\"]")
_CELERY_DECO  = re.compile(r"@(?:app|celery)\.task|@shared_task")
_CLICK_DECO   = re.compile(r"@(?:cli|app|main|cmd)\.command\b|@click\.command")
_TYPER_DECO   = re.compile(r"@(?:app|cli)\.command\b|@typer\.command")
_PYTEST_MARK  = re.compile(r"@pytest\.mark\.")


def _line_range_text(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[max(0, start - 1): end])


def _leading_comment(lines: list[str], start_line: int) -> str | None:
    comments: list[str] = []
    idx = start_line - 2
    while idx >= 0:
        raw = lines[idx]
        stripped = raw.strip()
        if not stripped:
            if comments:
                break
            idx -= 1
            continue
        if stripped.startswith(("#", "//", "///", "*", "/*", "<!--")):
            cleaned = stripped.strip("/* ").lstrip("#/").strip()
            if cleaned:
                comments.append(cleaned)
            idx -= 1
            continue
        break
    if not comments:
        return None
    return "\n".join(reversed(comments)).strip()


def _parse_python_docstring(raw: str) -> FunctionDescriptor:
    """Parse NumPy, Google, and reStructuredText docstring styles."""
    if not raw:
        return FunctionDescriptor(source="none", quality_score=0.0, function_id="")
    lines = raw.strip().splitlines()
    summary_lines = []
    params: dict[str, str] = {}
    returns: str | None = None
    raises: list[str] = []
    side_effects: list[str] = []
    section = "summary"
    current_param = None

    def _flush():
        nonlocal current_param
        current_param = None

    for line in lines:
        stripped = line.strip()
        # NumPy sections
        if re.match(r"^(Parameters|Args|Arguments)\s*$", stripped, re.I) or stripped == "Parameters\n----------":
            section = "params"
            _flush()
            continue
        if re.match(r"^(Returns?)\s*$", stripped, re.I):
            section = "returns"
            _flush()
            continue
        if re.match(r"^(Raises?)\s*$", stripped, re.I):
            section = "raises"
            _flush()
            continue
        if re.match(r"^(Side[\s_]?[Ee]ffects?|Notes?|Examples?|Attributes?|See [Aa]lso)\s*$", stripped, re.I):
            section = "other"
            _flush()
            continue
        if stripped in ("---", "---", "------", "----------"):
            continue
        # Google-style "Args:" prefix
        m_google = re.match(r"^(Parameters|Args|Arguments|Returns?|Raises?)\s*:", stripped, re.I)
        if m_google:
            kw = m_google.group(1).lower()
            section = "params" if "arg" in kw or "param" in kw else ("returns" if "return" in kw else "raises")
            _flush()
            continue
        if section == "summary":
            if stripped:
                summary_lines.append(stripped)
            elif summary_lines:
                section = "body"
        elif section == "params":
            m = re.match(r"^\s*([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*[:\-]\s*(.*)", line)
            if m and (m.start() <= 4 or current_param is None):
                current_param = m.group(1)
                params[current_param] = m.group(2).strip()
            elif current_param and line.startswith(("    ", "\t")):
                params[current_param] = (params.get(current_param, "") + " " + stripped).strip()
        elif section == "returns":
            if stripped and returns is None:
                returns = stripped
        elif section == "raises":
            m = re.match(r"^\s*([A-Za-z_]\w*)\s*[:\-]\s*(.*)", line)
            if m:
                raises.append(f"{m.group(1)}: {m.group(2).strip()}")
            elif stripped:
                raises.append(stripped)

    summary = " ".join(summary_lines)[:500]
    has_params = bool(params)
    has_returns = returns is not None
    if has_params and has_returns:
        quality = 1.0
    elif has_params or has_returns:
        quality = 0.85
    elif summary:
        quality = 0.7
    else:
        quality = 0.3
    return FunctionDescriptor(raw=raw, summary=summary, params=params, returns=returns, raises=raises, side_effects=side_effects, source="docstring", quality_score=quality, function_id="")


def _descriptor(raw: str | None, source: str) -> FunctionDescriptor | None:
    if not raw:
        return None
    if source == "docstring":
        return _parse_python_docstring(raw)
    summary = normalize_ws(raw.split("\n\n")[0])[:500]
    quality = 0.65 if source.endswith("comment") else 0.8
    return FunctionDescriptor(raw=raw, summary=summary, source=source, quality_score=quality, function_id="")


def infer_descriptor(name: str, signature: str | None, body: str) -> FunctionDescriptor:
    name_clean = name.lstrip("_")
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", name_clean).replace("_", " ").strip()
    if name_clean.startswith(("handle", "on_")):
        summary = f"Handles {words}."
    elif name.startswith(("process", "execute", "run")):
        summary = f"Executes {words}."
    elif name_clean.startswith(("get", "fetch", "load", "read")):
        summary = f"Retrieves {words}."
    elif name_clean.startswith(("create", "build", "make", "new")):
        summary = f"Creates {words}."
    elif name_clean.startswith(("update", "set", "write", "save", "store")):
        summary = f"Updates {words}."
    elif name_clean.startswith(("delete", "remove", "destroy")):
        summary = f"Deletes {words}."
    elif name_clean.startswith(("validate", "check", "verify", "assert")):
        summary = f"Validates {words}."
    elif name_clean.startswith(("parse", "decode", "deserialize")):
        summary = f"Parses {words}."
    elif name_clean.startswith(("format", "encode", "serialize", "render")):
        summary = f"Formats {words}."
    elif name_clean.startswith(("is_", "has_", "can_", "should_")):
        summary = f"Returns whether {words}."
    else:
        summary = f"Performs {words}."
    side_effects = []
    if re.search(r"\b(insert|update|delete|save|commit|publish|send|write|emit|post|put)\b", body, re.I):
        side_effects.append("possible external state change")
    if re.search(r"\b(requests\.|httpx\.|aiohttp\.|urllib)\b", body):
        side_effects.append("possible external HTTP call")
    if re.search(r"\b(open\(|Path.*write|os\.path)\b", body):
        side_effects.append("possible file system access")
    return FunctionDescriptor(summary=summary, source="inferred_static", quality_score=0.45, side_effects=side_effects, function_id="")


class PythonAnalyzer(LanguageAnalyzer):
    language = "python"

    # Builtins and noise to skip when recording CALLS edges
    _SKIP_CALLS = frozenset({
        "print", "len", "str", "int", "float", "dict", "list", "set", "tuple",
        "range", "enumerate", "zip", "map", "filter", "sorted", "reversed",
        "type", "isinstance", "issubclass", "hasattr", "getattr", "setattr",
        "super", "object", "bool", "bytes", "open", "repr", "vars", "dir",
        "append", "extend", "update", "items", "keys", "values", "get",
        "format", "join", "split", "strip", "lower", "upper", "replace",
    })

    def analyze(self, source: SourceFile) -> AnalysisResult:
        result = AnalysisResult()
        clean_text = source.text.replace('\x00', '')
        lines = clean_text.splitlines()
        try:
            tree = ast.parse(clean_text)
        except SyntaxError as exc:
            result.diagnostics.append(f"Python syntax error: {exc}")
            return result

        # Phase 5: Build import map for better call resolution
        import_map: dict[str, str] = {}  # local_name -> module.symbol
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    import_map[local] = alias.name
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    local = alias.asname or alias.name
                    import_map[local] = f"{module}.{alias.name}" if module else alias.name

        parent_stack: list[str] = []
        function_ids_by_name: dict[str, str] = {}
        is_test_file = source.relative_path.lower().startswith(("test", "tests/", "tests\\")) or \
                       "test_" in source.relative_path.lower() or \
                       source.relative_path.lower().endswith(("_test.py",))

        analyzer = self


        class Visitor(ast.NodeVisitor):
            def visit_ClassDef(self, node: ast.ClassDef):
                cls_name = node.name
                cls_qname = ".".join(parent_stack + [cls_name]) if parent_stack else cls_name
                bases = []
                for b in node.bases:
                    try:
                        bases.append(ast.unparse(b))
                    except Exception:
                        pass
                doc = ast.get_docstring(node) or ""
                decs = []
                for d in getattr(node, "decorator_list", []):
                    try:
                        decs.append(ast.unparse(d))
                    except Exception:
                        pass
                is_abstract = any("ABC" in b or "abstract" in b.lower() for b in bases) or                               any("abstractmethod" in d for d in decs)
                end = getattr(node, "end_lineno", node.lineno)
                method_count = sum(
                    1 for n in ast.walk(node)
                    if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
                )
                from codegraph_mcp.graph.models import ClassNode
                from codegraph_mcp.utils import stable_id
                cls_node = ClassNode(
                    id=stable_id(source.repo_id, source.relative_path, "class", cls_qname),
                    repo_id=source.repo_id,
                    file_id=source.file_id,
                    name=cls_name,
                    qualified_name=cls_qname,
                    start_line=node.lineno,
                    end_line=end,
                    bases=bases,
                    docstring=doc[:2000] if doc else None,
                    is_abstract=is_abstract,
                    decorators=decs,
                    loc=max(1, end - node.lineno + 1),
                    method_count=method_count,
                )
                result.classes.append(cls_node)
                parent_stack.append(cls_name)
                self.generic_visit(node)
                parent_stack.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef):
                self._function(node, False)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
                self._function(node, True)

            def _function(self, node, is_async: bool):
                name = getattr(node, "name", "<lambda>")
                qname = ".".join(parent_stack + [name]) if parent_stack else name
                start = getattr(node, "lineno", 1)
                end = getattr(node, "end_lineno", start)
                body = _line_range_text(lines, start, end)
                args = getattr(node, "args", None)
                params = []
                if args:
                    all_args = args.posonlyargs + args.args
                    defaults_offset = len(all_args) - len(args.defaults)
                    for i, arg in enumerate(all_args):
                        ann = ast.unparse(arg.annotation) if getattr(arg, "annotation", None) else None
                        default = None
                        if i >= defaults_offset:
                            try:
                                default = ast.unparse(args.defaults[i - defaults_offset])
                            except Exception:
                                pass
                        params.append({"name": arg.arg, "type_hint": ann, "default": default, "position": i})
                    if args.vararg:
                        params.append({"name": f"*{args.vararg.arg}", "type_hint": None, "position": len(params)})
                    for kwarg in args.kwonlyargs:
                        ann = ast.unparse(kwarg.annotation) if getattr(kwarg, "annotation", None) else None
                        params.append({"name": kwarg.arg, "type_hint": ann, "position": len(params), "keyword_only": True})
                    if args.kwarg:
                        params.append({"name": f"**{args.kwarg.arg}", "type_hint": None, "position": len(params)})

                returns = ast.unparse(node.returns) if getattr(node, "returns", None) else None
                prefix = "async def" if is_async else "def"
                sig_params = ", ".join(
                    p["name"] + (f": {p['type_hint']}" if p.get("type_hint") else "") for p in params
                )
                signature = f"{prefix} {name}({sig_params})" + (f" -> {returns}" if returns else "")
                fid = stable_id(source.repo_id, source.relative_path, qname, name)
                raw_doc = ast.get_docstring(node)
                desc = _descriptor(raw_doc, "docstring") or infer_descriptor(name, signature, body)
                desc.function_id = fid

                decorator_strs = [ast.unparse(d) for d in getattr(node, "decorator_list", [])]
                kind = FunctionKind.method if parent_stack else FunctionKind.function
                is_test = name.startswith("test_") or is_test_file
                if name in ("__init__", "__new__"):
                    kind = FunctionKind.constructor

                # Detect framework-specific kinds
                for d in decorator_strs:
                    if re.match(r"(app|router)\.(get|post|put|patch|delete|websocket)\b", d):
                        kind = FunctionKind.endpoint_handler
                    elif re.match(r"(app|celery|shared_task)\.task\b|@shared_task\b", d):
                        kind = FunctionKind.procedure

                fn = FunctionNode(
                    id=fid, repo_id=source.repo_id, file_id=source.file_id, language="python",
                    kind=kind, name=name, qualified_name=qname, display_name=qname,
                    start_line=start, end_line=end, signature=signature, return_type=returns,
                    parameters_json=params, decorators=decorator_strs, descriptor=desc,
                    body_hash=sha256_text(body), signature_hash=sha256_text(signature),
                    descriptor_hash=sha256_text(desc.raw or desc.summary or ""),
                    loc=max(1, end - start + 1), is_async=is_async, is_test=is_test,
                    enclosing_class=parent_stack[-1] if parent_stack else None,
                    complexity=1 + sum(
                        isinstance(n, ast.If | ast.For | ast.While | ast.Try | ast.BoolOp | ast.ExceptHandler)
                        for n in ast.walk(node)
                    ),
                )
                result.functions.append(fn)
                function_ids_by_name[name] = fid
                function_ids_by_name[qname] = fid

                # CALLS edges
                for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
                    target = None
                    call_line = getattr(call, "lineno", start)
                    if isinstance(call.func, ast.Name):
                        target = call.func.id
                    elif isinstance(call.func, ast.Attribute):
                        # Try to resolve owner via import map: obj.method -> module.method
                        target = call.func.attr
                        if isinstance(call.func.value, ast.Name):
                            owner = call.func.value.id
                            if owner in import_map:
                                resolved = f"{import_map[owner]}.{target}"
                                result.edges.append(FunctionEdge(
                                    id=stable_id(source.repo_id, fid, "CALLS", resolved, call_line),
                                    repo_id=source.repo_id, source_function_id=fid,
                                    target_symbol_name=resolved, edge_type=EdgeType.calls,
                                    confidence=0.78,
                                    evidence={"file": source.relative_path, "line": call_line, "extractor": "python_ast_import_aware"},
                                ))
                                target = None  # avoid duplicate bare-name edge
                    if target and target not in analyzer._SKIP_CALLS:
                        # Boost confidence if target is imported symbol
                        conf = 0.88 if target in import_map else 0.65
                        result.edges.append(FunctionEdge(
                            id=stable_id(source.repo_id, fid, "CALLS", target, call_line),
                            repo_id=source.repo_id, source_function_id=fid,
                            target_symbol_name=target, edge_type=EdgeType.calls, confidence=conf,
                            evidence={"file": source.relative_path, "line": call_line, "extractor": "python_ast"},
                        ))

                # Framework runtime bindings
                for d in decorator_strs:
                    rb = analyzer._framework_binding(source, fn, d)
                    if rb:
                        result.runtime_bindings.append(rb)

        Visitor().visit(tree)

        # Phase 6b: Module-level call detection.
        #
        # Calls made at the top level of a module (outside any function body)
        # are missed by the FunctionDef visitor.  A common pattern is:
        #
        #   app = FastAPI()
        #   register_invoke_routes(app, svc)   # <-- module-level
        #
        # We emit a synthetic __module_init__ function node that represents the
        # module's initialization code and attach CALLS edges from it for every
        # imported symbol that is called at module level.  This makes
        # register_*_routes functions show the correct direct_callers count.
        _module_level_calls: list[tuple[str, int]] = []  # (target_name, line)
        for stmt in ast.iter_child_nodes(tree):
            if not isinstance(stmt, ast.Expr):
                continue
            call = stmt.value
            if not isinstance(call, ast.Call):
                continue
            call_line = getattr(call, "lineno", 1)
            if isinstance(call.func, ast.Name):
                tgt = call.func.id
                if tgt not in self._SKIP_CALLS:
                    _module_level_calls.append((tgt, call_line))
            elif isinstance(call.func, ast.Attribute):
                tgt = call.func.attr
                if tgt not in self._SKIP_CALLS:
                    if isinstance(call.func.value, ast.Name):
                        owner = call.func.value.id
                        if owner in import_map:
                            tgt = f"{import_map[owner]}.{tgt}"
                    _module_level_calls.append((tgt, call_line))

        if _module_level_calls:
            mod_name = "__module_init__"
            mod_sig = f"def {mod_name}()"
            mod_fid = stable_id(source.repo_id, source.relative_path, mod_name, mod_sig)
            mod_desc = infer_descriptor(mod_name, mod_sig, "")
            mod_desc.function_id = mod_fid
            mod_fn = FunctionNode(
                id=mod_fid, repo_id=source.repo_id, file_id=source.file_id, language="python",
                kind=FunctionKind.function, name=mod_name, qualified_name=mod_name,
                display_name=mod_name, start_line=1, end_line=len(lines),
                signature=mod_sig, descriptor=mod_desc,
                body_hash=sha256_text(""), signature_hash=sha256_text(mod_sig),
                descriptor_hash=sha256_text(""), loc=len(lines), is_test=False,
                complexity=1,
            )
            result.functions.append(mod_fn)
            for tgt, call_line in _module_level_calls:
                conf = 0.88 if tgt.split(".")[0] in import_map or tgt in import_map else 0.65
                result.edges.append(FunctionEdge(
                    id=stable_id(source.repo_id, mod_fid, "CALLS", tgt, call_line),
                    repo_id=source.repo_id, source_function_id=mod_fid,
                    target_symbol_name=tgt, edge_type=EdgeType.calls, confidence=conf,
                    evidence={"file": source.relative_path, "line": call_line, "extractor": "python_ast_module_level"},
                ))

        # Phase 7: TESTED_BY / TESTS edges for test files
        if is_test_file:
            for fn in result.functions:
                if fn.is_test:
                    # Find functions being tested by name convention: test_foo tests foo
                    stripped = re.sub(r"^test_", "", fn.name)
                    if stripped and stripped in function_ids_by_name:
                        target_id = function_ids_by_name[stripped]
                        result.edges.append(FunctionEdge(
                            id=stable_id(source.repo_id, fn.id, "TESTS", target_id),
                            repo_id=source.repo_id, source_function_id=fn.id,
                            target_function_id=target_id, edge_type=EdgeType.tests, confidence=0.75,
                            evidence={"reason": "naming_convention", "extractor": "python_ast"},
                        ))

        return result

    def _framework_binding(self, source: SourceFile, fn: FunctionNode, decorator: str) -> RuntimeBinding | None:
        """Detect FastAPI/Flask/Celery/Click/Typer/pytest framework bindings."""
        rb_id = stable_id(source.repo_id, source.file_id, fn.id, decorator)
        m = _FASTAPI_DECO.match(decorator)
        if m:
            method = m.group(1).upper()
            path = m.group(3) or "/"
            return RuntimeBinding(id=rb_id, repo_id=source.repo_id, file_id=source.file_id,
                                  kind="http_route", name=f"{method} {path}", target=fn.qualified_name,
                                  details={"method": method, "path": path, "framework": "fastapi"}, confidence=0.9)
        m = _FLASK_DECO.match(decorator)
        if m:
            path = m.group(3) or "/"
            return RuntimeBinding(id=rb_id, repo_id=source.repo_id, file_id=source.file_id,
                                  kind="http_route", name=f"ROUTE {path}", target=fn.qualified_name,
                                  details={"path": path, "framework": "flask"}, confidence=0.9)
        if _CELERY_DECO.match(decorator):
            return RuntimeBinding(id=rb_id, repo_id=source.repo_id, file_id=source.file_id,
                                  kind="celery_task", name=fn.qualified_name, target=fn.qualified_name,
                                  details={"framework": "celery"}, confidence=0.85)
        if _CLICK_DECO.match(decorator) or _TYPER_DECO.match(decorator):
            return RuntimeBinding(id=rb_id, repo_id=source.repo_id, file_id=source.file_id,
                                  kind="cli_command", name=fn.name, target=fn.qualified_name,
                                  details={"framework": "click/typer"}, confidence=0.85)
        if _PYTEST_MARK.match(decorator):
            return RuntimeBinding(id=rb_id, repo_id=source.repo_id, file_id=source.file_id,
                                  kind="pytest_mark", name=fn.qualified_name, target=fn.qualified_name,
                                  details={"decorator": decorator}, confidence=0.9)
        return None




class RegexFunctionAnalyzer(LanguageAnalyzer):
    language = "generic"
    FUNCTION_PATTERNS: dict[str, list[re.Pattern]] = {
        "javascript": [
            re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)", re.M),
            re.compile(r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>", re.M),
            re.compile(r"^\s*([A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*\{", re.M),
        ],
        "typescript": [],
        "go": [re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_][\w]*)\s*\(([^)]*)\)", re.M)],
        "java": [re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\], ?]+\s+([A-Za-z_][\w]*)\s*\(([^)]*)\)\s*(?:throws [^{]+)?\{", re.M)],
        "csharp": [re.compile(r"^\s*(?:public|private|protected|internal)?\s*(?:static\s+|async\s+)?[\w<>\[\], ?]+\s+([A-Za-z_][\w]*)\s*\(([^)]*)\)\s*\{", re.M)],
        "rust": [re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_][\w]*)\s*\(([^)]*)\)", re.M)],
        "c": [re.compile(r"^\s*[\w\*\s]+\s+([A-Za-z_][\w]*)\s*\(([^;{}]*)\)\s*\{", re.M)],
        "cpp": [re.compile(r"^\s*[\w:\*<>&\s]+\s+([A-Za-z_][\w:~]*)\s*\(([^;{}]*)\)\s*(?:const\s*)?\{", re.M)],
        "ruby": [re.compile(r"^\s*def\s+(?:self\.)?([A-Za-z_][\w!?=]*)\s*(?:\(([^)]*)\))?", re.M)],
        "php": [re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?function\s+([A-Za-z_][\w]*)\s*\(([^)]*)\)", re.M)],
        "kotlin": [re.compile(r"^\s*(?:public|private|protected|internal)?\s*fun\s+([A-Za-z_][\w]*)\s*\(([^)]*)\)", re.M)],
        "scala": [re.compile(r"^\s*def\s+([A-Za-z_][\w]*)\s*\(([^)]*)\)", re.M)],
        "shell": [re.compile(r"^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*(?:\(\))\s*\{", re.M)],
    }
    FUNCTION_PATTERNS["typescript"] = FUNCTION_PATTERNS["javascript"]

    def supports(self, language: str, path: Path) -> bool:
        return language in self.FUNCTION_PATTERNS

    def analyze(self, source: SourceFile) -> AnalysisResult:
        result = AnalysisResult()
        lines = source.text.splitlines()
        patterns = self.FUNCTION_PATTERNS.get(source.language, [])
        seen: set[tuple[str, int]] = set()
        for pat in patterns:
            for m in pat.finditer(source.text):
                name = m.group(1)
                if name in {"if", "for", "while", "switch", "catch", "return", "new"}:
                    continue
                start = source.text.count("\n", 0, m.start()) + 1
                if (name, start) in seen:
                    continue
                seen.add((name, start))
                end = min(len(lines), self._find_block_end(lines, start))
                body = _line_range_text(lines, start, end)
                params_raw = m.group(2) if m.lastindex and m.lastindex >= 2 and m.group(2) else ""
                params = [{"name": p.strip().split(":")[0].split()[-1], "position": i} for i, p in enumerate(params_raw.split(",")) if p.strip()]
                signature = normalize_ws(lines[start-1])[:300]
                fid = stable_id(source.repo_id, source.relative_path, name, signature)
                raw_desc = _leading_comment(lines, start)
                desc = _descriptor(raw_desc, f"{source.language}_comment") or infer_descriptor(name, signature, body)
                desc.function_id = fid
                fn = FunctionNode(
                    id=fid, repo_id=source.repo_id, file_id=source.file_id, language=source.language,
                    kind=FunctionKind.test if self._is_test(name, source.relative_path, source.language) else FunctionKind.function,
                    name=name, qualified_name=name, display_name=name, start_line=start, end_line=end,
                    signature=signature, parameters_json=params, descriptor=desc,
                    body_hash=sha256_text(body), signature_hash=sha256_text(signature),
                    descriptor_hash=sha256_text(desc.raw or desc.summary or ""), loc=max(1, end-start+1),
                    is_async="async" in signature, is_test=self._is_test(name, source.relative_path, source.language),
                    complexity=1 + len(re.findall(r"\b(if|for|while|case|catch|&&|\|\|)\b", body)), confidence=0.75,
                )
                result.functions.append(fn)
                for cm in CALL_NAME_RE.finditer(body):
                    target = cm.group(1).split(".")[-1]
                    if target == name or target in {"if", "for", "while", "switch", "catch", "return", "function", "console.log"}:
                        continue
                    line = start + body[:cm.start()].count("\n")
                    result.edges.append(FunctionEdge(
                        id=stable_id(source.repo_id, fid, "CALLS", target, line), repo_id=source.repo_id,
                        source_function_id=fid, target_symbol_name=target, edge_type=EdgeType.calls,
                        confidence=0.45, evidence={"file": source.relative_path, "line": line, "extractor": "regex"}
                    ))
        return result

    def _find_block_end(self, lines: list[str], start: int) -> int:
        depth = 0
        saw_open = False
        for idx in range(start-1, len(lines)):
            line = lines[idx]
            depth += line.count("{")
            if line.count("{"):
                saw_open = True
            depth -= line.count("}")
            if saw_open and depth <= 0:
                return idx + 1
        if start < len(lines):
            return min(len(lines), start + 80)
        return start

    def _is_test(self, name: str, path: str, lang: str) -> bool:
        lower = path.lower()
        return name.lower().startswith(("test", "should")) or (
            "test" in lower or "spec" in lower
        )
