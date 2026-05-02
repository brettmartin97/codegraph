"""Tree-sitter powered analyzers for JavaScript, TypeScript, and Go.
Falls back gracefully if tree-sitter packages are not installed.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from codegraph_mcp.analyzers.base import AnalysisResult, LanguageAnalyzer, SourceFile
from codegraph_mcp.analyzers.generic import _descriptor, _leading_comment, infer_descriptor
from codegraph_mcp.graph.models import (
    ClassNode,
    EdgeType,
    FunctionEdge,
    FunctionKind,
    FunctionNode,
    RuntimeBinding,
)
from codegraph_mcp.utils import normalize_ws, sha256_text, stable_id

try:
    import tree_sitter_go as _tsgo
    import tree_sitter_javascript as _tsjs
    import tree_sitter_typescript as _tsts
    from tree_sitter import Language, Node, Parser
    _JS_LANG = Language(_tsjs.language())
    _TS_LANG = Language(_tsts.language_typescript())
    _TSX_LANG = Language(_tsts.language_tsx())
    _GO_LANG = Language(_tsgo.language())
    TREE_SITTER_AVAILABLE = True
except Exception:
    TREE_SITTER_AVAILABLE = False
    Language = Parser = Node = None  # type: ignore
    _JS_LANG = _TS_LANG = _TSX_LANG = _GO_LANG = None

_JS_SKIP_CALLS = frozenset({
    "console", "log", "warn", "error", "info", "debug",
    "require", "import", "exports", "module",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "Promise", "Array", "Object", "String", "Number", "Boolean",
    "JSON", "Math", "Date", "Error", "Map", "Set",
    "parseInt", "parseFloat", "isNaN", "isFinite",
    "toString", "valueOf", "hasOwnProperty",
    "push", "pop", "shift", "unshift", "slice", "splice",
    "map", "filter", "reduce", "forEach", "find", "findIndex",
    "keys", "values", "entries", "assign", "create", "freeze",
})

_GO_SKIP_CALLS = frozenset({
    "println", "print", "printf", "sprintf", "fprintf", "errorf",
    "Println", "Printf", "Sprintf", "Fprintf", "Errorf",
    "make", "new", "len", "cap", "append", "copy", "delete", "close",
    "panic", "recover", "string", "int", "int64", "float64", "bool", "byte",
})

_NESTJS_DECO = re.compile(
    r"@(Get|Post|Put|Patch|Delete|Controller|Injectable|Module)\s*\("
)
_EXPRESS_RE = re.compile(
    r"(?:app|router|server)\s*\.\s*(get|post|put|patch|delete|use|all)"
    r"\s*\(\s*['\"`]([^'\"` ]*)['\"`]",
    re.I,
)
_JEST_RE = re.compile(
    r"^(describe|it|test|beforeEach|afterEach|beforeAll|afterAll)$"
)


def _node_text(node: Node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _line_of(node: Node) -> int:
    return node.start_point[0] + 1


def _end_line(node: Node) -> int:
    return node.end_point[0] + 1


def _find_child(node: Node, *types: str) -> Node | None:
    for child in node.children:
        if child.type in types:
            return child
    return None


def _find_all(node: Node, *types: str) -> list[Node]:
    return [c for c in node.children if c.type in types]


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _jsdoc_comment(src_text: str, start_line: int) -> str | None:
    lines = src_text.splitlines()
    idx = start_line - 2
    if idx < 0:
        return None
    result_lines: list[str] = []
    while idx >= 0:
        raw = lines[idx].strip()
        if raw.startswith("*") or raw.startswith("/*") or raw.startswith("/**"):
            result_lines.append(raw.lstrip("/* ").rstrip("*/").strip())
            if raw.startswith("/**") or raw.startswith("/*"):
                break
        elif raw.startswith("//"):
            result_lines.append(raw.lstrip("/ ").strip())
        elif not raw:
            if result_lines:
                break
        else:
            break
        idx -= 1
    if not result_lines:
        return _leading_comment(lines, start_line)
    return "\n".join(reversed(result_lines)).strip() or None


def _js_params(params_node: Node, src: bytes) -> list[dict]:
    params = []
    pos = 0
    for child in params_node.children:
        if child.type == "identifier":
            params.append({"name": _node_text(child, src), "position": pos})
            pos += 1
        elif child.type in ("required_parameter", "optional_parameter",
                             "rest_parameter", "assignment_pattern"):
            name_node = _find_child(child, "identifier", "rest_pattern",
                                     "object_pattern", "array_pattern")
            name = _node_text(name_node, src) if name_node else f"arg{pos}"
            if child.type == "rest_parameter":
                name = f"...{name}"
            type_node = _find_child(child, "type_annotation")
            type_hint = None
            if type_node:
                type_hint = _node_text(type_node, src).lstrip(": ").strip()
            params.append({
                "name": name, "type_hint": type_hint, "position": pos,
                "optional": child.type == "optional_parameter",
            })
            pos += 1
    return params


def _go_params(params_node: Node, src: bytes) -> list[dict]:
    params = []
    pos = 0
    for child in params_node.children:
        if child.type == "parameter_declaration":
            names = [c for c in child.children if c.type == "identifier"]
            type_nodes = [c for c in child.children
                          if c.type not in ("identifier", ",", "(", ")")]
            type_hint = _node_text(type_nodes[0], src) if type_nodes else None
            if names:
                for n in names:
                    params.append({
                        "name": _node_text(n, src),
                        "type_hint": type_hint, "position": pos,
                    })
                    pos += 1
            else:
                params.append({"name": f"arg{pos}", "type_hint": type_hint,
                                "position": pos})
                pos += 1
        elif child.type == "variadic_parameter_declaration":
            names = [c for c in child.children if c.type == "identifier"]
            name = _node_text(names[0], src) if names else f"arg{pos}"
            params.append({"name": f"...{name}", "position": pos, "variadic": True})
            pos += 1
    return params


def _js_framework_binding(
    source: SourceFile, fn: FunctionNode, decorators: list[str], body: str
) -> RuntimeBinding | None:
    rb_id = stable_id(source.repo_id, source.file_id, fn.id, "binding")
    for deco in decorators:
        m = _NESTJS_DECO.match(deco.strip())
        if m:
            kind = m.group(1).upper()
            return RuntimeBinding(
                id=rb_id, repo_id=source.repo_id, file_id=source.file_id,
                kind="http_route", name=f"{kind} {fn.name}",
                target=fn.qualified_name,
                details={"framework": "nestjs", "decorator": deco},
                confidence=0.9,
            )
    m = _EXPRESS_RE.search(body)
    if m:
        return RuntimeBinding(
            id=rb_id, repo_id=source.repo_id, file_id=source.file_id,
            kind="http_route", name=f"{m.group(1).upper()} {m.group(2)}",
            target=fn.qualified_name,
            details={"framework": "express", "method": m.group(1), "path": m.group(2)},
            confidence=0.8,
        )
    return None


def _extract_js_calls(
    func_node: Node, src: bytes, source: SourceFile,
    fid: str, import_map: dict[str, str],
) -> list[FunctionEdge]:
    edges: list[FunctionEdge] = []
    for node in _walk(func_node):
        if node.type != "call_expression":
            continue
        fn_node = node.children[0] if node.children else None
        if fn_node is None:
            continue
        call_line = _line_of(node)
        if fn_node.type == "identifier":
            name = _node_text(fn_node, src)
            if name in _JS_SKIP_CALLS:
                continue
            resolved = import_map.get(name, name)
            conf = 0.88 if name in import_map else 0.65
            edges.append(FunctionEdge(
                id=stable_id(source.repo_id, fid, "CALLS", resolved, call_line),
                repo_id=source.repo_id, source_function_id=fid,
                target_symbol_name=resolved, edge_type=EdgeType.calls,
                confidence=conf,
                evidence={"file": source.relative_path, "line": call_line,
                          "extractor": "ts_ast"},
            ))
        elif fn_node.type == "member_expression":
            prop = _find_child(fn_node, "property_identifier")
            obj = fn_node.children[0] if fn_node.children else None
            if not prop:
                continue
            method = _node_text(prop, src)
            if method in _JS_SKIP_CALLS:
                continue
            if obj and obj.type == "identifier":
                owner = _node_text(obj, src)
                if owner in import_map:
                    resolved = f"{import_map[owner]}.{method}"
                    edges.append(FunctionEdge(
                        id=stable_id(source.repo_id, fid, "CALLS", resolved, call_line),
                        repo_id=source.repo_id, source_function_id=fid,
                        target_symbol_name=resolved, edge_type=EdgeType.calls,
                        confidence=0.78,
                        evidence={"file": source.relative_path, "line": call_line,
                                  "extractor": "ts_ast_import_aware"},
                    ))
                    continue
            edges.append(FunctionEdge(
                id=stable_id(source.repo_id, fid, "CALLS", method, call_line),
                repo_id=source.repo_id, source_function_id=fid,
                target_symbol_name=method, edge_type=EdgeType.calls,
                confidence=0.55,
                evidence={"file": source.relative_path, "line": call_line,
                          "extractor": "ts_ast"},
            ))
    return edges


class TreeSitterAnalyzer(LanguageAnalyzer):
    language = "unknown"
    extensions: set[str] = set()
    _lang_obj = None

    def analyze(self, source: SourceFile) -> AnalysisResult:
        raise NotImplementedError


class TreeSitterJSAnalyzer(TreeSitterAnalyzer):
    """Real AST analyzer for JavaScript and TypeScript."""
    language = "javascript"
    extensions = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
    _lang_obj = _JS_LANG

    def supports(self, language: str, path: Path) -> bool:
        return TREE_SITTER_AVAILABLE and language in (
            "javascript", "typescript", "tsx"
        )

    def analyze(self, source: SourceFile) -> AnalysisResult:
        result = AnalysisResult()
        src = source.text.encode("utf-8", errors="replace")
        lang = self._pick_lang(source)
        parser = Parser(lang)
        tree = parser.parse(src)
        import_map = self._build_import_map(tree.root_node, src)
        is_test = self._is_test_file(source.relative_path)
        self._visit_node(
            tree.root_node, src, source, result,
            import_map, is_test, [], [],
        )
        return result

    def _pick_lang(self, source: SourceFile) -> Language:
        ext = Path(source.relative_path).suffix.lower()
        if ext == ".tsx":
            return _TSX_LANG
        if ext == ".ts" or source.language == "typescript":
            return _TS_LANG
        return _JS_LANG

    def _build_import_map(self, root: Node, src: bytes) -> dict[str, str]:
        imp: dict[str, str] = {}
        for node in _walk(root):
            if node.type != "import_statement":
                continue
            source_node = _find_child(node, "string")
            mod = ""
            if source_node:
                mod = _node_text(source_node, src).strip("'\"`")
            clause = _find_child(node, "import_clause")
            if not clause:
                continue
            default = _find_child(clause, "identifier")
            if default:
                imp[_node_text(default, src)] = mod
            named = _find_child(clause, "named_imports")
            if named:
                for spec in named.children:
                    if spec.type != "import_specifier":
                        continue
                    names = [c for c in spec.children if c.type == "identifier"]
                    if len(names) == 2:
                        imp[_node_text(names[1], src)] = (
                            f"{mod}.{_node_text(names[0], src)}"
                        )
                    elif names:
                        sym = _node_text(names[0], src)
                        imp[sym] = f"{mod}.{sym}"
        return imp

    def _is_test_file(self, rel_path: str) -> bool:
        low = rel_path.lower()
        return any(x in low for x in (
            ".test.", ".spec.", "__tests__", "/test/", "/tests/", "/spec/",
        ))

    def _visit_node(
        self, node: Node, src: bytes, source: SourceFile,
        result: AnalysisResult, import_map: dict[str, str],
        is_test: bool, class_stack: list[str], decorators_pending: list[str],
    ) -> None:
        ntype = node.type

        if ntype == "decorator":
            decorators_pending.append(_node_text(node, src))
            return

        if ntype in ("class_declaration", "class"):
            self._handle_class(node, src, source, result, class_stack,
                               list(decorators_pending))
            decorators_pending.clear()
            name_node = _find_child(node, "type_identifier", "identifier")
            cls_name = _node_text(name_node, src) if name_node else "<class>"
            class_stack.append(cls_name)
            body = _find_child(node, "class_body")
            if body:
                for child in body.children:
                    self._visit_node(child, src, source, result, import_map,
                                     is_test, class_stack, [])
            class_stack.pop()
            return

        if ntype in ("function_declaration", "function",
                     "generator_function_declaration", "generator_function"):
            self._handle_function(node, src, source, result, import_map,
                                  is_test, class_stack, list(decorators_pending),
                                  is_arrow=False)
            decorators_pending.clear()

        elif ntype == "method_definition":
            self._handle_method(node, src, source, result, import_map,
                                is_test, class_stack, list(decorators_pending))
            decorators_pending.clear()

        elif ntype in ("lexical_declaration", "variable_declaration"):
            for decl in node.children:
                if decl.type == "variable_declarator":
                    val = _find_child(decl, "arrow_function", "function",
                                      "generator_function")
                    if val:
                        name_node = _find_child(decl, "identifier")
                        if name_node:
                            self._handle_function(
                                val, src, source, result, import_map,
                                is_test, class_stack, list(decorators_pending),
                                is_arrow=True,
                                override_name=_node_text(name_node, src),
                            )
            decorators_pending.clear()

        if ntype not in ("class_declaration", "class", "class_body",
                         "function_declaration", "function",
                         "generator_function_declaration", "generator_function",
                         "method_definition"):
            next_decos: list[str] = []
            for child in node.children:
                self._visit_node(child, src, source, result, import_map,
                                 is_test, class_stack, next_decos)

    def _handle_class(
        self, node: Node, src: bytes, source: SourceFile,
        result: AnalysisResult, class_stack: list[str], decorators: list[str],
    ) -> None:
        name_node = _find_child(node, "type_identifier", "identifier")
        if not name_node:
            return
        cls_name = _node_text(name_node, src)
        qname = ".".join(class_stack + [cls_name]) if class_stack else cls_name
        start = _line_of(node)
        end = _end_line(node)
        heritage = _find_child(node, "class_heritage")
        bases: list[str] = []
        if heritage:
            for ext in _walk(heritage):
                if ext.type == "identifier":
                    bases.append(_node_text(ext, src))
                    break
        body = _find_child(node, "class_body")
        method_count = sum(
            1 for c in (body.children if body else [])
            if c.type == "method_definition"
        )
        result.classes.append(ClassNode(
            id=stable_id(source.repo_id, source.relative_path, "class", qname),
            repo_id=source.repo_id, file_id=source.file_id,
            name=cls_name, qualified_name=qname,
            start_line=start, end_line=end,
            bases=bases,
            is_abstract=any("abstract" in d.lower() for d in decorators),
            decorators=decorators,
            loc=max(1, end - start + 1),
            method_count=method_count,
        ))

    def _handle_method(
        self, node: Node, src: bytes, source: SourceFile,
        result: AnalysisResult, import_map: dict[str, str],
        is_test: bool, class_stack: list[str], decorators: list[str],
    ) -> None:
        name_node = _find_child(node, "property_identifier", "identifier",
                                 "private_property_identifier")
        if not name_node:
            return
        self._handle_function(node, src, source, result, import_map,
                              is_test, class_stack, decorators,
                              is_arrow=False,
                              override_name=_node_text(name_node, src),
                              is_method=True)

    def _handle_function(
        self, node: Node, src: bytes, source: SourceFile,
        result: AnalysisResult, import_map: dict[str, str],
        is_test: bool, class_stack: list[str], decorators: list[str],
        is_arrow: bool, override_name: str | None = None,
        is_method: bool = False,
    ) -> None:
        if override_name:
            name = override_name
        else:
            name_node = _find_child(node, "identifier")
            if not name_node:
                return
            name = _node_text(name_node, src)

        qname = ".".join(class_stack + [name]) if class_stack else name
        start = _line_of(node)
        end = _end_line(node)
        body_text = _node_text(node, src)

        params_node = _find_child(node, "formal_parameters")
        params = _js_params(params_node, src) if params_node else []

        ret_type = None
        type_ann = _find_child(node, "type_annotation")
        if type_ann:
            ret_type = _node_text(type_ann, src).lstrip(": ").strip()

        is_async = any(c.type == "async" for c in node.children)
        params_str = ", ".join(
            p["name"] + (f": {p['type_hint']}" if p.get("type_hint") else "")
            for p in params
        )
        prefix = "async " if is_async else ""
        if is_arrow:
            sig = f"{prefix}({params_str}) =>"
            if ret_type:
                sig += f" {ret_type}"
        else:
            sig = f"{prefix}function {name}({params_str})"
            if ret_type:
                sig += f": {ret_type}"
        signature = normalize_ws(sig)[:300]

        fid = stable_id(source.repo_id, source.relative_path, qname, name)
        raw_doc = _jsdoc_comment(source.text, start)
        desc = _descriptor(raw_doc, "jsdoc_comment") if raw_doc else None
        if not desc:
            desc = infer_descriptor(name, signature, body_text)
        desc.function_id = fid

        is_test_fn = (
            is_test
            or name.startswith(("test", "it", "describe", "should"))
            or bool(_JEST_RE.match(name))
        )
        if is_method and name == "constructor":
            kind = FunctionKind.constructor
        elif is_method:
            kind = FunctionKind.method
        elif is_test_fn:
            kind = FunctionKind.test
        else:
            kind = FunctionKind.function

        fn = FunctionNode(
            id=fid, repo_id=source.repo_id, file_id=source.file_id,
            language=source.language,
            kind=kind, name=name, qualified_name=qname, display_name=qname,
            start_line=start, end_line=end,
            signature=signature, return_type=ret_type,
            parameters_json=params, decorators=decorators,
            descriptor=desc,
            body_hash=sha256_text(body_text),
            signature_hash=sha256_text(signature),
            descriptor_hash=sha256_text(desc.raw or desc.summary or ""),
            loc=max(1, end - start + 1),
            is_async=is_async, is_test=is_test_fn,
            enclosing_class=class_stack[-1] if class_stack else None,
            confidence=0.92,
            complexity=1 + sum(
                1 for n in _walk(node)
                if n.type in (
                    "if_statement", "for_statement", "for_in_statement",
                    "for_of_statement", "while_statement", "do_statement",
                    "try_statement", "switch_case", "ternary_expression",
                    "logical_and_expression", "logical_or_expression",
                )
            ),
        )
        result.functions.append(fn)

        body_node = _find_child(node, "statement_block")
        if body_node:
            result.edges.extend(
                _extract_js_calls(body_node, src, source, fid, import_map)
            )

        rb = _js_framework_binding(source, fn, decorators, body_text)
        if rb:
            result.runtime_bindings.append(rb)


class TreeSitterGoAnalyzer(TreeSitterAnalyzer):
    language = "go"
    extensions = {".go"}
    _lang_obj = _GO_LANG

    def supports(self, language: str, path: Path) -> bool:
        return TREE_SITTER_AVAILABLE and language == "go"

    def analyze(self, source: SourceFile) -> AnalysisResult:
        result = AnalysisResult()
        src = source.text.encode("utf-8", errors="replace")
        parser = Parser(_GO_LANG)
        tree = parser.parse(src)
        import_map = self._build_go_import_map(tree.root_node, src)
        is_test = (
            source.relative_path.endswith("_test.go")
            or "/testdata/" in source.relative_path
        )
        for node in tree.root_node.children:
            if node.type == "function_declaration":
                fn = self._go_func(node, src, source, import_map, is_test,
                                   receiver=None, name_override=None,
                                   params_node=None, ret_type=None)
                if fn:
                    result.functions.append(fn)
                    result.edges.extend(
                        self._go_calls(node, src, source, fn.id, import_map)
                    )
            elif node.type == "method_declaration":
                fn = self._go_method(node, src, source, import_map, is_test)
                if fn:
                    result.functions.append(fn)
                    result.edges.extend(
                        self._go_calls(node, src, source, fn.id, import_map)
                    )
        return result

    def _build_go_import_map(self, root: Node, src: bytes) -> dict[str, str]:
        imp: dict[str, str] = {}
        for node in _walk(root):
            if node.type != "import_spec":
                continue
            path_node = _find_child(node, "interpreted_string_literal",
                                     "raw_string_literal")
            alias_node = _find_child(node, "package_identifier", "blank_identifier")
            if not path_node:
                continue
            raw = _node_text(path_node, src).strip("\"'`")
            pkg = raw.split("/")[-1]
            alias = _node_text(alias_node, src) if alias_node else pkg
            imp[alias] = raw
        return imp

    def _go_method(
        self, node: Node, src: bytes, source: SourceFile,
        import_map: dict[str, str], is_test: bool,
    ) -> FunctionNode | None:
        # Layout: func <recv_param_list> <field_identifier> <param_list> [result] <block>
        param_lists = _find_all(node, "parameter_list")
        if len(param_lists) < 2:
            return None

        # Receiver type from first parameter_list
        recv_list = param_lists[0]
        receiver_type: str | None = None
        for child in _walk(recv_list):
            if child.type == "type_identifier":
                receiver_type = _node_text(child, src)
                break
            if child.type == "pointer_type":
                inner = _find_child(child, "type_identifier")
                if inner:
                    receiver_type = _node_text(inner, src)
                    break

        name_node = _find_child(node, "field_identifier")
        if not name_node:
            return None
        method_name = _node_text(name_node, src)

        # Params from second parameter_list
        params_node = param_lists[1]

        # Return type: third param_list (named returns) or bare type node
        ret_type: str | None = None
        if len(param_lists) >= 3:
            ret_type = _node_text(param_lists[2], src)
        else:
            found_params = False
            for child in node.children:
                if child is params_node:
                    found_params = True
                    continue
                if found_params and child.type in (
                    "type_identifier", "pointer_type", "qualified_type",
                    "slice_type", "map_type", "interface_type", "struct_type",
                    "array_type", "channel_type",
                ):
                    ret_type = _node_text(child, src)
                    break
                if found_params and child.type == "block":
                    break

        return self._go_func(node, src, source, import_map, is_test,
                             receiver=receiver_type, name_override=method_name,
                             params_node=params_node, ret_type=ret_type)

    def _go_func(
        self, node: Node, src: bytes, source: SourceFile,
        import_map: dict[str, str], is_test: bool,
        receiver: str | None, name_override: str | None,
        params_node: Node | None, ret_type: str | None,
    ) -> FunctionNode | None:
        if name_override:
            name = name_override
        else:
            name_node = _find_child(node, "identifier")
            if not name_node:
                return None
            name = _node_text(name_node, src)

        qname = f"{receiver}.{name}" if receiver else name
        start = _line_of(node)
        end = _end_line(node)
        body_text = _node_text(node, src)

        if params_node is not None:
            params = _go_params(params_node, src)
        else:
            all_plists = _find_all(node, "parameter_list")
            params = _go_params(all_plists[0], src) if all_plists else []

        if ret_type is None:
            all_plists = _find_all(node, "parameter_list")
            if len(all_plists) >= 2:
                ret_type = _node_text(all_plists[1], src)
            else:
                for child in node.children:
                    if child.type in (
                        "type_identifier", "pointer_type", "qualified_type",
                        "slice_type", "map_type", "interface_type", "struct_type",
                    ):
                        ret_type = _node_text(child, src)
                        break

        params_str = ", ".join(
            p["name"] + (f" {p['type_hint']}" if p.get("type_hint") else "")
            for p in params
        )
        sig = f"func {qname}({params_str})"
        if ret_type:
            sig += f" {ret_type}"
        signature = normalize_ws(sig)[:300]

        is_test_fn = is_test and (
            name.startswith(("Test", "Benchmark", "Example"))
            or name == "TestMain"
        )
        fid = stable_id(source.repo_id, source.relative_path, qname, name)
        lines = source.text.splitlines()
        raw_doc = _leading_comment(lines, start)
        desc = _descriptor(raw_doc, "go_comment") if raw_doc else None
        if not desc:
            desc = infer_descriptor(name, signature, body_text)
        desc.function_id = fid

        return FunctionNode(
            id=fid, repo_id=source.repo_id, file_id=source.file_id,
            language="go",
            kind=(FunctionKind.test if is_test_fn
                  else FunctionKind.method if receiver
                  else FunctionKind.function),
            name=name, qualified_name=qname, display_name=qname,
            start_line=start, end_line=end,
            signature=signature, return_type=ret_type,
            parameters_json=params,
            descriptor=desc,
            body_hash=sha256_text(body_text),
            signature_hash=sha256_text(signature),
            descriptor_hash=sha256_text(desc.raw or desc.summary or ""),
            loc=max(1, end - start + 1),
            is_async=False, is_test=is_test_fn,
            enclosing_class=receiver,
            confidence=0.94,
            complexity=1 + sum(
                1 for n in _walk(node)
                if n.type in (
                    "if_statement", "for_statement", "range_clause",
                    "switch_statement", "select_statement",
                    "type_switch_statement",
                )
            ),
        )

    def _go_calls(
        self, node: Node, src: bytes, source: SourceFile,
        fid: str, import_map: dict[str, str],
    ) -> list[FunctionEdge]:
        edges: list[FunctionEdge] = []
        for n in _walk(node):
            if n.type != "call_expression":
                continue
            fn_node = n.children[0] if n.children else None
            if fn_node is None:
                continue
            call_line = _line_of(n)
            if fn_node.type == "identifier":
                name = _node_text(fn_node, src)
                if name in _GO_SKIP_CALLS:
                    continue
                edges.append(FunctionEdge(
                    id=stable_id(source.repo_id, fid, "CALLS", name, call_line),
                    repo_id=source.repo_id, source_function_id=fid,
                    target_symbol_name=name, edge_type=EdgeType.calls,
                    confidence=0.75,
                    evidence={"file": source.relative_path, "line": call_line,
                              "extractor": "go_ast"},
                ))
            elif fn_node.type == "selector_expression":
                obj = fn_node.children[0] if fn_node.children else None
                sel = _find_child(fn_node, "field_identifier")
                if not (obj and sel):
                    continue
                obj_name = _node_text(obj, src)
                method = _node_text(sel, src)
                if obj_name in import_map:
                    resolved = f"{import_map[obj_name]}.{method}"
                    edges.append(FunctionEdge(
                        id=stable_id(source.repo_id, fid, "CALLS",
                                     resolved, call_line),
                        repo_id=source.repo_id, source_function_id=fid,
                        target_symbol_name=resolved, edge_type=EdgeType.calls,
                        confidence=0.82,
                        evidence={"file": source.relative_path, "line": call_line,
                                  "extractor": "go_ast_import_aware"},
                    ))
                else:
                    edges.append(FunctionEdge(
                        id=stable_id(source.repo_id, fid, "CALLS", method, call_line),
                        repo_id=source.repo_id, source_function_id=fid,
                        target_symbol_name=method, edge_type=EdgeType.calls,
                        confidence=0.60,
                        evidence={"file": source.relative_path, "line": call_line,
                                  "extractor": "go_ast"},
                    ))
        return edges


# ── Optional: Java, Rust, C# ─────────────────────────────────────────────────

try:
    import tree_sitter_c_sharp as _tscs
    import tree_sitter_java as _tsjava
    import tree_sitter_rust as _tsrust
    TREE_SITTER_JVM_AVAILABLE = True
except ImportError:
    TREE_SITTER_JVM_AVAILABLE = False


class TreeSitterJavaAnalyzer(LanguageAnalyzer):
    """Real AST analyzer for Java using tree-sitter."""

    def __init__(self):
        if TREE_SITTER_JVM_AVAILABLE:
            from tree_sitter import Language
            from tree_sitter import Parser as TSParser
            self._parser = TSParser(Language(_tsjava.language()))
        else:
            self._parser = None

    def supports(self, language: str, path) -> bool:
        return TREE_SITTER_JVM_AVAILABLE and language == "java"

    def analyze(self, source: SourceFile) -> AnalysisResult:
        from codegraph_mcp.analyzers.base import AnalysisResult
        if self._parser is None:
            return AnalysisResult()
        tree = self._parser.parse(source.text.encode("utf-8", errors="replace"))
        functions: list[FunctionNode] = []
        edges: list[FunctionEdge] = []
        self._visit(tree.root_node, source, functions, edges, enclosing_class=None)
        return AnalysisResult(functions=functions, edges=edges)

    def _visit(self, node, source, functions, edges, enclosing_class):
        if node.type == "class_declaration":
            cls_name = None
            for c in node.children:
                if c.type == "identifier":
                    cls_name = c.text.decode("utf-8", errors="replace")
                    break
            for c in node.children:
                self._visit(c, source, functions, edges, cls_name)
            return
        if node.type == "method_declaration":
            self._extract_method(node, source, functions, edges, enclosing_class)
            return
        if node.type == "constructor_declaration":
            self._extract_method(node, source, functions, edges, enclosing_class, is_ctor=True)
            return
        for c in node.children:
            self._visit(c, source, functions, edges, enclosing_class)

    def _extract_method(self, node, source, functions, edges, enclosing_class, is_ctor=False):
        name = None
        params = []
        return_type = None
        visibility = "package"
        is_async = False

        for c in node.children:
            if c.type == "identifier" and name is None:
                name = c.text.decode("utf-8", errors="replace")
            elif c.type == "modifiers":
                mods = c.text.decode("utf-8", errors="replace")
                if "public" in mods:
                    visibility = "public"
                elif "private" in mods:
                    visibility = "private"
                elif "protected" in mods:
                    visibility = "protected"
            elif c.type in ("type_identifier", "void_type", "integral_type",
                            "boolean_type", "floating_point_type", "generic_type",
                            "array_type"):
                if return_type is None:
                    return_type = c.text.decode("utf-8", errors="replace")
            elif c.type == "formal_parameters":
                for p in c.children:
                    if p.type == "formal_parameter":
                        parts = p.text.decode("utf-8", errors="replace").split()
                        if len(parts) >= 2:
                            params.append({"name": parts[-1], "type": " ".join(parts[:-1])})

        if not name:
            return

        qname = f"{enclosing_class}.{name}" if enclosing_class else name
        sl = node.start_point[0] + 1
        el = node.end_point[0] + 1
        sig = f"{'public' if visibility=='public' else visibility} {return_type or 'void'} {name}({', '.join(p['type'] for p in params)})"
        fid = stable_id(source.repo_id, source.file_id, qname, sig)

        fn = FunctionNode(
            id=fid, repo_id=source.repo_id, file_id=source.file_id,
            language="java",
            kind="constructor" if is_ctor else ("method" if enclosing_class else "function"),
            name=name, qualified_name=qname, display_name=name,
            start_line=sl, end_line=el,
            signature=sig, return_type=return_type,
            parameters=params, visibility=visibility,
            body_hash=stable_id(node.text.decode("utf-8", errors="replace")),
            signature_hash=stable_id(sig),
            is_async=is_async,
            is_test=name.startswith("test") or "@Test" in (node.text.decode("utf-8", errors="replace")),
            enclosing_class=enclosing_class,
            confidence=0.93,
            loc=el - sl + 1,
        )
        functions.append(fn)

        # Extract call edges from method body
        body_text = node.text.decode("utf-8", errors="replace") if node.text else ""
        import re
        for m in re.finditer(r'\b([a-z][a-zA-Z0-9_]*)\s*\(', body_text):
            callee = m.group(1)
            if callee in {"if", "while", "for", "switch", "return", "throw", "new"}:
                continue
            call_line = sl + body_text[: m.start()].count("\n")
            edges.append(FunctionEdge(
                id=stable_id(source.repo_id, fid, "CALLS", callee, str(call_line)),
                repo_id=source.repo_id, source_function_id=fid,
                target_symbol_name=callee, edge_type=EdgeType.calls,
                confidence=0.70,
                evidence={"file": source.relative_path, "line": call_line, "extractor": "java_ast"},
            ))


class TreeSitterRustAnalyzer(LanguageAnalyzer):
    """Real AST analyzer for Rust using tree-sitter."""

    def __init__(self):
        if TREE_SITTER_JVM_AVAILABLE:
            from tree_sitter import Language
            from tree_sitter import Parser as TSParser
            self._parser = TSParser(Language(_tsrust.language()))
        else:
            self._parser = None

    def supports(self, language: str, path) -> bool:
        return TREE_SITTER_JVM_AVAILABLE and language == "rust"

    def analyze(self, source: SourceFile) -> AnalysisResult:
        from codegraph_mcp.analyzers.base import AnalysisResult
        if self._parser is None:
            return AnalysisResult()
        tree = self._parser.parse(source.text.encode("utf-8", errors="replace"))
        functions: list[FunctionNode] = []
        edges: list[FunctionEdge] = []
        self._visit(tree.root_node, source, functions, edges, impl_type=None)
        return AnalysisResult(functions=functions, edges=edges)

    def _visit(self, node, source, functions, edges, impl_type):
        if node.type == "impl_item":
            # impl Foo { ... } or impl Trait for Foo { ... }
            type_name = None
            for c in node.children:
                if c.type == "type_identifier":
                    type_name = c.text.decode("utf-8", errors="replace")
                    break
            for c in node.children:
                self._visit(c, source, functions, edges, impl_type=type_name)
            return
        if node.type == "function_item":
            self._extract_fn(node, source, functions, edges, impl_type)
            return
        for c in node.children:
            self._visit(c, source, functions, edges, impl_type)

    def _extract_fn(self, node, source, functions, edges, impl_type):
        name = None
        params = []
        return_type = None
        is_async = False
        visibility = "private"

        for c in node.children:
            if c.type == "identifier":
                name = c.text.decode("utf-8", errors="replace")
            elif c.type == "visibility_modifier":
                visibility = "public"
            elif c.type == "function_modifiers":
                if b"async" in c.text:
                    is_async = True
            elif c.type == "parameters":
                for p in c.children:
                    if p.type == "parameter":
                        ptext = p.text.decode("utf-8", errors="replace")
                        parts = ptext.split(":", 1)
                        pname = parts[0].strip()
                        ptype = parts[1].strip() if len(parts) > 1 else ""
                        if pname not in {"self", "&self", "&mut self"}:
                            params.append({"name": pname, "type": ptype})
            elif c.type in ("type_identifier", "generic_type", "primitive_type",
                            "reference_type", "scoped_type_identifier", "tuple_type"):
                if return_type is None:
                    return_type = c.text.decode("utf-8", errors="replace")

        if not name:
            return

        qname = f"{impl_type}::{name}" if impl_type else name
        sl = node.start_point[0] + 1
        el = node.end_point[0] + 1
        sig = f"{'pub ' if visibility=='public' else ''}{'async ' if is_async else ''}fn {name}({', '.join(p['type'] for p in params)}){' -> ' + return_type if return_type else ''}"
        fid = stable_id(source.repo_id, source.file_id, qname, sig)

        fn = FunctionNode(
            id=fid, repo_id=source.repo_id, file_id=source.file_id,
            language="rust",
            kind="method" if impl_type else "function",
            name=name, qualified_name=qname, display_name=name,
            start_line=sl, end_line=el,
            signature=sig, return_type=return_type,
            parameters=params, visibility=visibility,
            body_hash=stable_id(node.text.decode("utf-8", errors="replace")),
            signature_hash=stable_id(sig),
            is_async=is_async,
            is_test="#[test]" in (source.text[max(0, node.start_byte-20):node.start_byte]),
            enclosing_class=impl_type,
            confidence=0.94,
            loc=el - sl + 1,
        )
        functions.append(fn)

        import re
        body_text = node.text.decode("utf-8", errors="replace") if node.text else ""
        for m in re.finditer(r'\b([a-z_][a-zA-Z0-9_]*)\s*\(', body_text):
            callee = m.group(1)
            if callee in {"if", "while", "for", "match", "let", "fn", "use", "mod"}:
                continue
            call_line = sl + body_text[:m.start()].count("\n")
            edges.append(FunctionEdge(
                id=stable_id(source.repo_id, fid, "CALLS", callee, str(call_line)),
                repo_id=source.repo_id, source_function_id=fid,
                target_symbol_name=callee, edge_type=EdgeType.calls,
                confidence=0.72,
                evidence={"file": source.relative_path, "line": call_line, "extractor": "rust_ast"},
            ))


class TreeSitterCSharpAnalyzer(LanguageAnalyzer):
    """Real AST analyzer for C# using tree-sitter."""

    def __init__(self):
        if TREE_SITTER_JVM_AVAILABLE:
            from tree_sitter import Language
            from tree_sitter import Parser as TSParser
            self._parser = TSParser(Language(_tscs.language()))
        else:
            self._parser = None

    def supports(self, language: str, path) -> bool:
        return TREE_SITTER_JVM_AVAILABLE and language == "csharp"

    def analyze(self, source: SourceFile) -> AnalysisResult:
        from codegraph_mcp.analyzers.base import AnalysisResult
        if self._parser is None:
            return AnalysisResult()
        tree = self._parser.parse(source.text.encode("utf-8", errors="replace"))
        functions: list[FunctionNode] = []
        edges: list[FunctionEdge] = []
        self._visit(tree.root_node, source, functions, edges, enclosing_class=None)
        return AnalysisResult(functions=functions, edges=edges)

    def _visit(self, node, source, functions, edges, enclosing_class):
        if node.type == "class_declaration":
            cls_name = None
            for c in node.children:
                if c.type == "identifier":
                    cls_name = c.text.decode("utf-8", errors="replace")
                    break
            for c in node.children:
                self._visit(c, source, functions, edges, cls_name)
            return
        if node.type == "method_declaration":
            self._extract_method(node, source, functions, edges, enclosing_class)
            return
        if node.type == "constructor_declaration":
            self._extract_method(node, source, functions, edges, enclosing_class, is_ctor=True)
            return
        for c in node.children:
            self._visit(c, source, functions, edges, enclosing_class)

    def _extract_method(self, node, source, functions, edges, enclosing_class, is_ctor=False):
        name = None
        params = []
        return_type = None
        visibility = "private"
        is_async = False

        for c in node.children:
            if c.type == "identifier" and name is None:
                name = c.text.decode("utf-8", errors="replace")
            elif c.type == "modifier":
                mtext = c.text.decode("utf-8", errors="replace")
                if mtext == "public":
                    visibility = "public"
                elif mtext == "async":
                    is_async = True
                elif mtext in ("protected", "internal"):
                    visibility = mtext
            elif c.type in ("predefined_type", "identifier", "generic_name",
                            "nullable_type", "array_type", "void_keyword"):
                if return_type is None and c.type != "identifier":
                    return_type = c.text.decode("utf-8", errors="replace")
                elif return_type is None and name is None:
                    return_type = c.text.decode("utf-8", errors="replace")
            elif c.type == "parameter_list":
                for p in c.children:
                    if p.type == "parameter":
                        pparts = p.text.decode("utf-8", errors="replace").split()
                        if len(pparts) >= 2:
                            params.append({"name": pparts[-1], "type": " ".join(pparts[:-1])})

        if not name:
            return

        qname = f"{enclosing_class}.{name}" if enclosing_class else name
        sl = node.start_point[0] + 1
        el = node.end_point[0] + 1
        sig = f"{visibility} {'async ' if is_async else ''}{return_type or 'void'} {name}({', '.join(p['type'] for p in params)})"
        fid = stable_id(source.repo_id, source.file_id, qname, sig)

        fn = FunctionNode(
            id=fid, repo_id=source.repo_id, file_id=source.file_id,
            language="csharp",
            kind="constructor" if is_ctor else ("method" if enclosing_class else "function"),
            name=name, qualified_name=qname, display_name=name,
            start_line=sl, end_line=el,
            signature=sig, return_type=return_type,
            parameters=params, visibility=visibility,
            body_hash=stable_id(node.text.decode("utf-8", errors="replace")),
            signature_hash=stable_id(sig),
            is_async=is_async,
            is_test=name.startswith("Test") or "[Test]" in source.text[max(0, node.start_byte-30):node.start_byte],
            enclosing_class=enclosing_class,
            confidence=0.93,
            loc=el - sl + 1,
        )
        functions.append(fn)

        import re
        body_text = node.text.decode("utf-8", errors="replace") if node.text else ""
        for m in re.finditer(r'\b([A-Za-z_][a-zA-Z0-9_]*)\s*\(', body_text):
            callee = m.group(1)
            if callee in {"if", "while", "for", "foreach", "switch", "return",
                          "throw", "new", "await", "var", "using"}:
                continue
            call_line = sl + body_text[:m.start()].count("\n")
            edges.append(FunctionEdge(
                id=stable_id(source.repo_id, fid, "CALLS", callee, str(call_line)),
                repo_id=source.repo_id, source_function_id=fid,
                target_symbol_name=callee, edge_type=EdgeType.calls,
                confidence=0.70,
                evidence={"file": source.relative_path, "line": call_line, "extractor": "csharp_ast"},
            ))
