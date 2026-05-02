from __future__ import annotations

import json
from pathlib import Path

from codegraph_mcp.config import ensure_runtime_dirs, settings
from codegraph_mcp.graph.sqlite_store import SQLiteStore
from codegraph_mcp.indexing.indexer import Indexer
from codegraph_mcp.memory.failures import FailureIngestor
from codegraph_mcp.query.context import ContextEngine
from codegraph_mcp.query.impact import ImpactEngine
from codegraph_mcp.server._cache import cache_get, cache_invalidate, cache_key, cache_set

ensure_runtime_dirs()
store = SQLiteStore(settings.db_path)
indexer = Indexer(store)
context = ContextEngine(store)
impact = ImpactEngine(store)
ingestor = FailureIngestor(store)

try:
    from mcp.server.fastmcp import FastMCP
except Exception as exc:  # pragma: no cover
    FastMCP = None
    MCP_IMPORT_ERROR = exc
else:
    MCP_IMPORT_ERROR = None


def _json(data) -> str:
    return json.dumps(data, indent=2, default=str)


if FastMCP:
    mcp = FastMCP("codegraph")

    # ── Registration / indexing ──────────────────────────────────────────────

    def _enricher_kwargs() -> dict:
        kwargs: dict = {}
        if settings.llm_api_key:
            kwargs["api_key"] = settings.llm_api_key
        if settings.llm_model:
            kwargs["model"] = settings.llm_model
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
            kwargs["url"] = settings.llm_base_url
        return kwargs

    def _maybe_auto_enrich(repo_name: str, result: dict) -> dict:
        """Run heuristic enrichment after indexing if enrichment_auto is enabled.

        Failures are reported in result['enrichment_error'] but never raise.
        """
        if not settings.enrichment_auto:
            return result
        try:
            from codegraph_mcp.enrichment.runner import make_enricher, run_enrichment
            r = store.get_repo(repo_name)
            if not r:
                return result
            enricher = make_enricher(settings.enrichment_backend, **_enricher_kwargs())
            # Auto-scale: always cover the whole repo; settings cap is just the default
            # lower-bound (avoids silently under-enriching large repos).
            actual_count: int = store.conn.execute(
                "SELECT COUNT(*) FROM functions WHERE repo_id=?", (r.id,)
            ).fetchone()[0]
            max_fns = max(settings.enrichment_max_functions, actual_count)
            stats = run_enrichment(
                store, r.id, enricher,
                batch_size=settings.enrichment_batch_size,
                max_functions=max_fns,
            )
            enricher.close()
            result["enrichment"] = stats
        except Exception as exc:
            result["enrichment_error"] = str(exc)
        return result

    @mcp.tool()
    def register_and_index(path: str, repo_name: str = "", mode: str = "full") -> str:
        """Register a local directory as a repo and index it.
        This is the first tool to call when starting work on a codebase.
        path must be an absolute path to the repo root on disk.
        repo_name defaults to the directory name if omitted.
        Heuristic enrichment runs automatically (descriptors → category/tags/importance).
        """
        cache_invalidate()
        p = Path(path)
        name = repo_name or p.name
        repo = indexer.add_repo(name, p)
        result = indexer.index_repo(repo.name, mode)
        result = _maybe_auto_enrich(repo.name, result)
        return _json(result)

    @mcp.tool()
    def index_repo(repo: str, mode: str = "full") -> str:
        """Re-index an already-registered repository to pick up file changes.
        Use after editing files to refresh the graph.
        Heuristic enrichment runs automatically for newly added functions.
        """
        cache_invalidate()
        result = indexer.index_repo(repo, mode)
        result = _maybe_auto_enrich(repo, result)
        return _json(result)

    @mcp.tool()
    def index_zip(zip_path: str, repo_name: str, mode: str = "full") -> str:
        """Register and index a repository supplied as a zip archive."""
        cache_invalidate()
        repo = indexer.add_repo_from_zip(repo_name, Path(zip_path))
        result = indexer.index_repo(repo.name, mode)
        result = _maybe_auto_enrich(repo.name, result)
        return _json(result)

    @mcp.tool()
    def list_repos() -> str:
        """List all registered repositories with file/function counts and enrichment status."""
        repos = store.list_repos()
        results = []
        for r in repos:
            ov = store.overview(r.id)
            stats = store.enrichment_stats(r.id)
            results.append({
                "name": r.name,
                "path": r.path,
                "file_count": ov["file_count"],
                "function_count": ov["function_count"],
                "languages": ov["languages"],
                "enrichment": stats,
            })
        return _json(results)

    # ── Enrichment ───────────────────────────────────────────────────────────

    @mcp.tool()
    def enrich_repo(repo: str, backend: str = "", max_functions: int = 0,
                    force: bool = False) -> str:
        """Add semantic descriptions to functions using LLM or heuristic rules.

        backend: heuristic (default, zero-cost), anthropic, openai, vllm, ollama, custom_http
          - heuristic: instant, infers category/tags from names+decorators, no LLM
          - anthropic: uses Claude Haiku — set CODEGRAPH_LLM_API_KEY
          - openai/vllm/ollama: OpenAI-compatible — set CODEGRAPH_LLM_BASE_URL
          - custom_http: calls any /invoke-compatible endpoint — set CODEGRAPH_LLM_BASE_URL

        After enrichment, semantic_search works across purpose, category, and tags.
        force=True re-enriches already-enriched functions.
        """
        cache_invalidate()
        from codegraph_mcp.enrichment.runner import make_enricher, run_enrichment
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        chosen_backend = backend or settings.enrichment_backend
        try:
            enricher = make_enricher(chosen_backend, **_enricher_kwargs())
        except (ImportError, ValueError) as exc:
            return _json({"error": str(exc)})
        max_fn = max_functions or settings.enrichment_max_functions
        stats = run_enrichment(
            store, r.id, enricher,
            batch_size=settings.enrichment_batch_size,
            max_functions=max_fn,
            force=force,
        )
        enricher.close()
        return _json({"repo": repo, "backend": chosen_backend, **stats})

    @mcp.tool()
    def enrichment_status(repo: str) -> str:
        """Show enrichment coverage: how many functions have semantic descriptions,
        breakdown by category. Use to decide if enrich_repo is needed.
        """
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        return _json(store.enrichment_stats(r.id))

    # ── High-level intelligence tools ────────────────────────────────────────

    @mcp.tool()
    def repo_overview(repo: str) -> str:
        """Return a summary of the repo: file count, function count, languages, descriptor coverage.

        WHEN TO USE: At the start of a session to orient yourself — how big is the codebase,
        what languages are in use, are there high-complexity hotspots to be aware of?"""
        k = cache_key("repo_overview", repo=repo)
        if hit := cache_get(k):
            return hit
        return cache_set(k, _json(context.repo_overview(repo)))

    @mcp.tool()
    def prepare_change(repo: str, task: str, max_tokens: int = 12000,
                       compact: bool = True) -> str:
        """Generate a structured change plan for a task description.

        WHEN TO USE: Before starting any non-trivial edit. Describe the task in plain
        English; this tool returns the exact functions to modify, their call contracts,
        blast radius, and a structured validation_checklist.
        Always call this before get_function_impact on large changes.

        compact: when True (default), strips storage hashes and compacts the embedded
                 impact_report — same shape used by get_function_impact. Cuts payload
                 ~70%. Set False to receive full source slices and raw descriptors.
        """
        k = cache_key("prepare_change", repo=repo, task=task,
                      max_tokens=max_tokens, compact=compact)
        if hit := cache_get(k):
            return hit
        plan = context.prepare_change(repo, task, max_tokens)
        if compact:
            if plan.get("target_functions"):
                plan["target_functions"] = [_compact_fn(fn) for fn in plan["target_functions"]]
            if plan.get("impact_report"):
                plan["impact_report"] = _compact_impact_report(plan["impact_report"])

        # Build a compact key_facts header — the most important static facts
        # the agent should read first, before paging through full lists.
        targets = plan.get("target_functions") or []
        ir = plan.get("impact_report") or {}
        primary = targets[0] if targets else None
        key_facts = {
            "primary_target": (primary or {}).get("qualified_name"),
            "primary_location": (
                f"{(primary or {}).get('file', '')}:"
                f"{(primary or {}).get('start_line', '')}-"
                f"{(primary or {}).get('end_line', '')}"
            ) if primary else None,
            "candidate_count": len(targets),
            "blast_radius": {
                "direct_callers": len(ir.get("direct_callers") or []),
                "transitive_callers": len(ir.get("transitive_callers") or []),
                "related_tests": len(ir.get("related_tests") or []),
                "unresolved_callees": len(ir.get("unresolved_callees") or []),
                "runtime_entrypoints": len(ir.get("runtime_entrypoints") or []),
            },
            "risk_level": plan.get("risk_level"),
            "risk_score": plan.get("risk_score"),
            "confidence": plan.get("confidence"),
            "files_to_touch": plan.get("safe_edit_boundary") or [],
            "top_reasons": (ir.get("reasons") or [])[:3],
        }
        plan = {"key_facts": key_facts, **plan}

        return cache_set(k, _json(plan))

    # ── Search ───────────────────────────────────────────────────────────────

    @mcp.tool()
    def find_function(repo: str, query: str, limit: int = 10) -> str:
        """Search for functions by name or keyword across the repo.

        WHEN TO USE: When you know roughly what a function is called but not its exact
        qualified name. Returns names, signatures, file locations, and complexity.
        Then call get_function_impact on results before editing them."""
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        fns = store.find_functions(r.id, query, limit)
        return _json([{
            "qualified_name": f.qualified_name,
            "file": store.file_path(f.file_id),
            "lines": f"{f.start_line}-{f.end_line}",
            "signature": f.signature,
            "kind": str(f.kind),
            "is_async": f.is_async,
            "is_test": f.is_test,
            "complexity": f.complexity,
            "category": f.descriptor.category if f.descriptor else None,
            "summary": f.descriptor.summary if f.descriptor else None,
            "purpose": f.descriptor.purpose if f.descriptor else None,
        } for f in fns])

    @mcp.tool()
    def semantic_search(repo: str, query: str, limit: int = 10) -> str:
        """Find functions by what they DO, not what they are called.

        WHEN TO USE: When you need to locate the function responsible for a behaviour
        (e.g. "validates user input", "sends email notification").
        Requires prior enrichment (enrich_repo). Falls back to name search otherwise."""
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        results = store.semantic_search(r.id, query, limit)
        return _json(results)

    # ── Function-level tools ─────────────────────────────────────────────────

    # Fields to strip from FunctionNode dumps when compact=True.
    # These are storage-internal hashes/ids agents don't need on the wire.
    _COMPACT_FN_DROP = {
        "body_hash", "signature_hash", "descriptor_hash",
        "repo_id", "file_id", "id", "parent_symbol_id",
        "annotations", "parameters_json", "namespace",
        "is_generator", "display_name",
    }

    def _compact_fn(fn: dict) -> dict:
        """Slim a FunctionNode dict for agent consumption.

        Keeps the fields a calling LLM actually uses (qualified_name, file/lines,
        signature, complexity, summary, category, decorators, return_type) and
        drops storage hashes + redundant aliases. Cuts payload size ~80%.

        Resolves file_id → file path so 'file' is always present (agents can't
        use file_id without a separate store lookup).
        """
        # Resolve file path before dropping file_id
        file_id = fn.get("file_id")
        if file_id and "file" not in fn:
            try:
                fn = {**fn, "file": store.file_path(file_id)}
            except Exception:
                pass
        out = {k: v for k, v in fn.items() if k not in _COMPACT_FN_DROP}
        # Drop confidence when fully resolved (the common case)
        if out.get("confidence") == 1.0:
            out.pop("confidence", None)
        # Drop empty decorator lists
        if not out.get("decorators"):
            out.pop("decorators", None)
        # Drop visibility when null
        if out.get("visibility") is None:
            out.pop("visibility", None)
        # Flatten descriptor to summary/category/purpose only
        desc = out.pop("descriptor", None)
        if desc:
            if desc.get("summary"):
                out["summary"] = desc["summary"]
            if desc.get("category"):
                out["category"] = desc["category"]
            if desc.get("purpose") and desc.get("purpose") != desc.get("summary"):
                out["purpose"] = desc["purpose"]
            if desc.get("tags"):
                out["tags"] = desc["tags"]
        return out

    # Well-known stdlib / pydantic / generic Python noise names that clutter
    # the unresolved_callees list without providing any architectural signal.
    _UNRESOLVED_NOISE = frozenset({
        # pydantic
        "model_dump", "model_validate", "model_fields", "model_json_schema",
        "parse_obj", "dict", "json", "schema",
        # dunder / magic
        "__init__", "__call__", "__enter__", "__exit__", "__iter__", "__next__",
        # stdlib builtins
        "round", "abs", "len", "str", "int", "float", "bool", "list", "dict",
        "set", "tuple", "type", "repr", "vars", "dir", "id",
        # stdlib methods on built-in containers
        "append", "extend", "pop", "get", "setdefault", "update", "items",
        "keys", "values", "clear", "copy", "remove", "discard", "add",
        # logging
        "debug", "info", "warning", "error", "critical", "exception", "log",
        # stdlib top-level
        "time.monotonic", "time.time", "time.sleep", "time.perf_counter",
        "asyncio.to_thread", "asyncio.sleep", "asyncio.gather",
        "os.path.join", "os.path.exists", "os.makedirs", "os.getenv",
        "json.dumps", "json.loads",
    })

    def _compact_impact_report(report: dict) -> dict:
        """Compact an ImpactReport dict for MCP transport."""
        compacted = dict(report)
        for key in ("target_function",):
            if compacted.get(key):
                compacted[key] = _compact_fn(compacted[key])
        for list_key in (
            "direct_callers", "direct_callees",
            "transitive_callers", "transitive_callees",
            "related_tests",
        ):
            if compacted.get(list_key):
                compacted[list_key] = [_compact_fn(f) for f in compacted[list_key]]
        # Trim unresolved_callees: drop noise (stdlib/pydantic/builtins), keep
        # only names that carry architectural signal for the agent.
        if compacted.get("unresolved_callees"):
            compacted["unresolved_callees"] = [
                {"target": e.get("target_symbol_name"),
                 "edge_type": e.get("edge_type"),
                 "confidence": e.get("confidence")}
                for e in compacted["unresolved_callees"]
                if e.get("target_symbol_name") not in _UNRESOLVED_NOISE
            ]
        # Drop heavy context_pack by default; agents can request via compact=False
        compacted.pop("context_pack", None)
        return compacted

    @mcp.tool()
    def get_function_impact(repo: str, function: str, depth: int = 3,
                            max_tokens: int = 12000, compact: bool = True) -> str:
        """Compute blast radius for a function — callers, tests, risk score.

        WHEN TO USE: Before editing ANY function. Returns:
          - direct_callers / transitive_callers: everything that will break
          - related_tests: tests that exercise this function
          - risk_score 0-1: 0=safe to change, >=0.65=high risk, needs careful review
          - recommended_validation: exactly what to run after the change
        A risk_score >= 0.65 means touching this function will likely break callers.

        depth: transitive caller traversal depth (default 3)
        compact: drop storage hashes + context_pack from response (default True,
                 cuts payload size ~80%). Set False to receive full source slices.
        """
        k = cache_key("get_function_impact", repo=repo, function=function,
                      depth=depth, max_tokens=max_tokens, compact=compact)
        if hit := cache_get(k):
            return hit
        report = impact.function_impact(repo, function, depth, max_tokens).model_dump()
        if compact:
            report = _compact_impact_report(report)
            tgt = report.get("target_function") or {}
            key_facts = {
                "target": tgt.get("qualified_name"),
                "location": (
                    f"{tgt.get('file','')}:{tgt.get('start_line','')}-{tgt.get('end_line','')}"
                ) if tgt else None,
                "complexity": tgt.get("complexity"),
                "loc": tgt.get("loc"),
                "is_test": tgt.get("is_test"),
                "blast_radius": {
                    "direct_callers": len(report.get("direct_callers") or []),
                    "transitive_callers": len(report.get("transitive_callers") or []),
                    "direct_callees": len(report.get("direct_callees") or []),
                    "transitive_callees": len(report.get("transitive_callees") or []),
                    "related_tests": len(report.get("related_tests") or []),
                    "unresolved_callees": len(report.get("unresolved_callees") or []),
                    "runtime_entrypoints": len(report.get("runtime_entrypoints") or []),
                },
                "risk_level": report.get("risk_level"),
                "risk_score": report.get("risk_score"),
                "confidence": report.get("confidence"),
                "top_reasons": (report.get("reasons") or [])[:3],
            }
            report = {"key_facts": key_facts, **report}
        return cache_set(k, _json(report))

    @mcp.tool()
    def get_function_source(repo: str, function: str) -> str:
        """Return source code + descriptor for a function.
        Includes file, line range, full source text, signature, decorators, and
        LLM-generated purpose/category/tags if enriched.
        """
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        fn = store.get_function_by_name(r.id, function)
        if not fn:
            matches = store.find_functions(r.id, function, 1)
            if not matches:
                return _json({"error": f"No function matched {function!r}"})
            fn = matches[0]
        file_path = store.file_path(fn.file_id)
        full_path = Path(r.path) / file_path
        source = ""
        try:
            lines = full_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            start = max(0, fn.start_line - 1)
            end = min(len(lines), fn.end_line)
            source = "\n".join(lines[max(0, start - 3):end])
        except Exception as exc:
            source = f"<could not read source: {exc}>"
        desc = fn.descriptor.model_dump() if fn.descriptor else None
        return _json({
            "qualified_name": fn.qualified_name,
            "file": file_path,
            "start_line": fn.start_line,
            "end_line": fn.end_line,
            "signature": fn.signature,
            "decorators": fn.decorators,
            "source": source,
            "descriptor": desc,
            "complexity": fn.complexity,
            "loc": fn.loc,
        })

    @mcp.tool()
    def get_function_descriptor(repo: str, function: str) -> str:
        """Return semantic descriptor for a function without its source.
        Lighter than get_function_source. Includes summary, params, returns,
        raises, purpose, category, importance, tags.
        """
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        fn = store.get_function_by_name(r.id, function)
        if not fn:
            matches = store.find_functions(r.id, function, 1)
            if not matches:
                return _json({"error": f"No function matched {function!r}"})
            fn = matches[0]
        desc = fn.descriptor.model_dump() if fn.descriptor else None
        return _json({"function": fn.qualified_name, "signature": fn.signature,
                      "descriptor": desc})

    # ── Class tools ──────────────────────────────────────────────────────────

    @mcp.tool()
    def get_class(repo: str, class_name: str) -> str:
        """Return a class definition: bases, docstring, decorators, and all its methods.
        Shows the full public contract of a class without reading the whole file.
        """
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        cls = store.get_class(r.id, class_name)
        if not cls:
            return _json({"error": f"Class {class_name!r} not found. Use get_file_tree to browse."})
        methods = store.class_methods(r.id, class_name)
        return _json({
            **cls,
            "methods": [{
                "name": m.name,
                "qualified_name": m.qualified_name,
                "lines": f"{m.start_line}-{m.end_line}",
                "signature": m.signature,
                "kind": str(m.kind),
                "complexity": m.complexity,
                "is_async": m.is_async,
                "summary": m.descriptor.summary if m.descriptor else None,
                "purpose": m.descriptor.purpose if m.descriptor else None,
            } for m in methods],
        })

    @mcp.tool()
    def get_class_hierarchy(repo: str) -> str:
        """Return all inheritance relationships in a repo as child→parent pairs.
        Use to understand the class structure before modifying base classes.
        """
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        pairs = store.get_class_hierarchy(r.id)
        return _json(pairs)

    @mcp.tool()
    def list_classes(repo: str, limit: int = 50) -> str:
        """List all classes in a repo with their base classes, method counts, and docstrings."""
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        return _json(store.list_classes(r.id, limit))

    # ── File-level tools ─────────────────────────────────────────────────────

    @mcp.tool()
    def get_file_tree(repo: str, max_depth: int = 4) -> str:
        """Directory tree annotated with language, line count, function count per file."""
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        rows = store.files(r.id)
        file_meta: dict[str, dict] = {}
        for row in rows:
            file_meta[row["path"]] = {"language": row["language"],
                                       "line_count": row["line_count"]}
        fn_count: dict[str, int] = {}
        for row in store.conn.execute(
            "SELECT file_id, COUNT(*) c FROM functions WHERE repo_id=? GROUP BY file_id",
            (r.id,)
        ):
            path = store.file_path(row["file_id"])
            fn_count[path] = row["c"]
        _SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
                 "build", "target", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
        repo_path = Path(r.path)
        lines_out = [f"[{r.name}] {r.path}"]

        def _walk(directory: Path, prefix: str, depth: int):
            if depth > max_depth:
                return
            try:
                entries = sorted(directory.iterdir(),
                                 key=lambda p: (p.is_file(), p.name.lower()))
            except PermissionError:
                return
            for entry in entries:
                if entry.name in _SKIP or entry.name.startswith("."):
                    continue
                rel = str(entry.relative_to(repo_path))
                if entry.is_dir():
                    lines_out.append(f"{prefix}{entry.name}/")
                    _walk(entry, prefix + "  ", depth + 1)
                else:
                    meta = file_meta.get(rel, {})
                    lang = meta.get("language", "")
                    ann = ""
                    if lang:
                        ann = f"  [{lang}, {meta.get('line_count','')}L, {fn_count.get(rel,0)}fn]"
                    lines_out.append(f"{prefix}{entry.name}{ann}")

        _walk(repo_path, "  ", 1)
        return "\n".join(lines_out)

    @mcp.tool()
    def list_functions_in_file(repo: str, file_path: str) -> str:
        """List all functions defined in a specific file with their line ranges.

        WHEN TO USE: When you're about to edit a file and want to know exactly which
        functions it contains, their signatures, and complexity before reading the source."""
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        norm = file_path.replace("\\", "/")
        rows = store.conn.execute(
            "SELECT f.*, fd.summary, fd.purpose, fd.category, fi.path as fpath "
            "FROM functions f "
            "JOIN files fi ON fi.id=f.file_id "
            "LEFT JOIN function_descriptors fd ON fd.function_id=f.id "
            "WHERE f.repo_id=? AND (REPLACE(fi.path,'\\\\','/') LIKE ? "
            "OR REPLACE(fi.path,'\\\\','/')=?) ORDER BY f.start_line",
            (r.id, f"%{norm}%", norm),
        ).fetchall()
        if not rows:
            return _json({"error": f"No functions in {file_path!r}. Check path with get_file_tree."})
        return _json([{
            "name": row["name"],
            "qualified_name": row["qualified_name"],
            "lines": f"{row['start_line']}-{row['end_line']}",
            "signature": row["signature"],
            "kind": row["kind"],
            "complexity": row["complexity"],
            "is_async": bool(row["is_async"]),
            "is_test": bool(row["is_test"]),
            "category": row["category"],
            "summary": row["summary"],
            "purpose": row["purpose"],
        } for row in rows])

    # ── Call-graph tools ─────────────────────────────────────────────────────

    @mcp.tool()
    def get_callers(repo: str, function: str) -> str:
        """List all functions that directly call the given function.

        WHEN TO USE: When you need the immediate call-sites of a function — for example
        to update them after changing a signature. For full transitive blast radius
        use get_function_impact instead."""
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        fn = store.get_function_by_name(r.id, function)
        if not fn:
            matches = store.find_functions(r.id, function, 1)
            if not matches:
                return _json({"error": f"No function matched {function!r}"})
            fn = matches[0]
        callers = store.callers(fn.id)
        return _json([{
            "qualified_name": c.qualified_name,
            "file": store.file_path(c.file_id),
            "lines": f"{c.start_line}-{c.end_line}",
            "signature": c.signature,
            "complexity": c.complexity,
        } for c in callers])

    @mcp.tool()
    def get_callees(repo: str, function: str) -> str:
        """List all functions directly called by the given function.

        WHEN TO USE: To understand a function's dependencies before refactoring it,
        or to find which internal helpers it relies on."""
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        fn = store.get_function_by_name(r.id, function)
        if not fn:
            matches = store.find_functions(r.id, function, 1)
            if not matches:
                return _json({"error": f"No function matched {function!r}"})
            fn = matches[0]
        callees = store.callees(fn.id)
        unresolved = store.unresolved_callees(fn.id)
        return _json({
            "resolved_callees": [{
                "qualified_name": c.qualified_name,
                "file": store.file_path(c.file_id),
                "lines": f"{c.start_line}-{c.end_line}",
                "signature": c.signature,
            } for c in callees],
            "unresolved_callees": [e.target_symbol_name for e in unresolved],
        })

    @mcp.tool()
    def get_high_complexity_functions(repo: str, limit: int = 20,
                                       min_complexity: int = 5) -> str:
        """List the most complex functions in the repo, sorted by complexity.

        WHEN TO USE: When looking for refactoring targets, understanding risk areas,
        or deciding where to focus test coverage. High-complexity functions are the
        most likely source of bugs and most expensive to change."""
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        rows = store.conn.execute(
            "SELECT f.*, fd.summary, fd.purpose, fd.category FROM functions f "
            "LEFT JOIN function_descriptors fd ON fd.function_id=f.id "
            "WHERE f.repo_id=? AND COALESCE(f.complexity,0) >= ? "
            "ORDER BY COALESCE(f.complexity,0) DESC LIMIT ?",
            (r.id, min_complexity, limit),
        ).fetchall()
        return _json([{
            "qualified_name": row["qualified_name"],
            "file": store.file_path(row["file_id"]),
            "lines": f"{row['start_line']}-{row['end_line']}",
            "complexity": row["complexity"],
            "loc": row["loc"],
            "signature": row["signature"],
            "category": row["category"],
            "summary": row["summary"],
            "purpose": row["purpose"],
            "is_test": bool(row["is_test"]),
        } for row in rows])

    # ── Snapshot / diff ──────────────────────────────────────────────────────

    @mcp.tool()
    def snapshot_repo(repo: str, ref: str = "HEAD") -> str:
        """Create a named snapshot of the current function graph for later diff."""
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        count = store.snapshot_repo(r.id, ref)
        return _json({"repo": repo, "ref": ref, "functions_snapshotted": count})

    @mcp.tool()
    def diff_snapshots(repo: str, ref_a: str = "HEAD", ref_b: str = "latest") -> str:
        """Compare two snapshots: added/removed/changed functions."""
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        diff = store.diff_snapshots(r.id, ref_a, ref_b)
        return _json(diff.model_dump())

    # ── Boundary policy ──────────────────────────────────────────────────────

    @mcp.tool()
    def validate_boundaries(repo: str, policy_file: str = "boundaries.yaml") -> str:
        """Check call edges against a boundary policy file, report violations."""
        from codegraph_mcp.policy.boundaries import BoundaryChecker, load_policy
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        policy_path = Path(policy_file)
        if not policy_path.is_absolute():
            policy_path = Path(r.path) / policy_file
        try:
            policy = load_policy(policy_path)
        except FileNotFoundError as exc:
            return _json({"error": str(exc)})
        checker = BoundaryChecker(store)
        violations = checker.check(r.id, policy)
        return _json({"repo": repo, "policy_file": str(policy_path),
                      "violations": [v.model_dump() for v in violations]})

    # ── Failure memory ───────────────────────────────────────────────────────

    @mcp.tool()
    def ingest_failure(repo: str, kind: str, message: str, stack_trace: str = "",
                       source: str = "manual", language: str = "python") -> str:
        """Record a failure (test failure, exception, CI failure) linked to functions.
        The graph remembers which functions have failed — surfaces in get_function_impact.
        """
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        event = ingestor.ingest(r.id, kind=kind, message=message,
                                stack_trace=stack_trace, source=source, language=language)
        return _json({"id": event.id, "kind": event.kind,
                      "functions_matched": event.function_ids})

    @mcp.tool()
    def get_failure_history(repo: str, function: str | None = None,
                            limit: int = 20) -> str:
        """Recent failure events, optionally scoped to a specific function."""
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo {repo}"})
        if function:
            fn = store.get_function_by_name(r.id, function)
            if not fn:
                matches = store.find_functions(r.id, function, 1)
                fn = matches[0] if matches else None
            if fn:
                events = store.failures_for_function(fn.id)
            else:
                return _json({"error": f"No function matched {function!r}"})
        else:
            events = store.recent_failures(r.id, limit)
        return _json([e.model_dump() for e in events[:limit]])


    @mcp.tool()
    def graph_status(repo: str) -> str:
        """Check whether the CodeGraph index is fresh and ready to use.

        WHEN TO USE: At the start of any session, or when uncertain whether the graph
        reflects recent edits. Returns hours_stale, file_count, and a human-readable
        warning if the graph is more than 24h old.
        A warning here means impact analysis results may be incomplete.
        """
        r = store.get_repo(repo)
        if not r:
            return _json({"error": f"Unknown repo: {repo}. Call register_and_index first."})
        freshness = store.graph_freshness(r.id)
        ov = store.overview(r.id)
        from codegraph_mcp.analyzers.registry import AnalyzerRegistry
        reg = AnalyzerRegistry()
        return _json({
            "repo": repo,
            "indexed": freshness["indexed"],
            "hours_stale": freshness.get("hours_stale"),
            "last_indexed_at": freshness.get("last_indexed_at"),
            "file_count": ov["file_count"],
            "function_count": ov["function_count"],
            "languages": ov["languages"],
            "active_analyzers": reg.active_languages,
            "warning": freshness.get("warning"),
            "tip": (
                "Graph is fresh." if not freshness.get("warning")
                else "Run index_repo with mode='incremental' to refresh."
            ),
        })


def main():
    if not FastMCP:
        raise RuntimeError(
            f"mcp package not installed: {MCP_IMPORT_ERROR}")
    mcp.run()


if __name__ == "__main__":
    main()
