from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from codegraph_mcp.config import ensure_runtime_dirs, settings
from codegraph_mcp.graph.sqlite_store import SQLiteStore
from codegraph_mcp.indexing.indexer import Indexer
from codegraph_mcp.memory.failures import FailureIngestor
from codegraph_mcp.query.context import ContextEngine
from codegraph_mcp.query.impact import ImpactEngine

api = FastAPI(title="CodeGraph MCP", version="0.2.0")
ensure_runtime_dirs()
store = SQLiteStore(settings.db_path)
indexer = Indexer(store)
context_engine = ContextEngine(store)
impact_engine = ImpactEngine(store)
failure_ingestor = FailureIngestor(store)


class RepoAddRequest(BaseModel):
    name: str
    path: str


class IndexRequest(BaseModel):
    mode: str = "full"


class ImpactRequest(BaseModel):
    function: str
    depth: int = 2
    max_tokens: int = 12000
    include_transitive: bool = True


class PrepareChangeRequest(BaseModel):
    task: str
    max_tokens: int = 12000


class SnapshotRequest(BaseModel):
    ref: str = "HEAD"


class FailureRequest(BaseModel):
    kind: str
    message: str
    stack_trace: str = ""
    source: str = "api"
    language: str = "python"


class BoundaryRequest(BaseModel):
    policy_file: str = "boundaries.yaml"


@api.get("/healthz")
def healthz():
    return {"status": "ok", "db_path": str(settings.db_path), "version": "0.2.0"}


@api.post("/repos")
def add_repo(req: RepoAddRequest):
    try:
        return indexer.add_repo(req.name, Path(req.path)).model_dump()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@api.get("/repos")
def list_repos():
    return [r.model_dump() for r in store.list_repos()]


@api.post("/repos/{repo}/index")
def index(repo: str, req: IndexRequest):
    try:
        return indexer.index_repo(repo, req.mode)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@api.get("/repos/{repo}/overview")
def overview(repo: str):
    try:
        return context_engine.repo_overview(repo)
    except Exception as exc:
        raise HTTPException(404, str(exc)) from exc


@api.get("/repos/{repo}/functions")
def functions(repo: str, q: str, limit: int = 20):
    r = store.get_repo(repo)
    if not r:
        raise HTTPException(404, f"Unknown repo {repo}")
    return [f.model_dump() for f in store.find_functions(r.id, q, limit)]


@api.post("/repos/{repo}/impact")
def impact(repo: str, req: ImpactRequest):
    try:
        return impact_engine.function_impact(
            repo, req.function, req.depth, req.max_tokens, req.include_transitive
        ).model_dump()
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@api.post("/repos/{repo}/prepare-change")
def prepare_change(repo: str, req: PrepareChangeRequest):
    try:
        return context_engine.prepare_change(repo, req.task, req.max_tokens)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@api.post("/repos/{repo}/snapshot")
def snapshot(repo: str, req: SnapshotRequest):
    r = store.get_repo(repo)
    if not r:
        raise HTTPException(404, f"Unknown repo {repo}")
    count = store.snapshot_repo(r.id, req.ref)
    return {"repo": repo, "ref": req.ref, "functions_snapshotted": count}


@api.get("/repos/{repo}/diff")
def diff(repo: str, ref_a: str = "HEAD", ref_b: str = "latest"):
    r = store.get_repo(repo)
    if not r:
        raise HTTPException(404, f"Unknown repo {repo}")
    return store.diff_snapshots(r.id, ref_a, ref_b).model_dump()


@api.post("/repos/{repo}/validate-boundaries")
def validate_boundaries(repo: str, req: BoundaryRequest):
    from codegraph_mcp.policy.boundaries import BoundaryChecker, load_policy
    r = store.get_repo(repo)
    if not r:
        raise HTTPException(404, f"Unknown repo {repo}")
    policy_path = Path(req.policy_file)
    if not policy_path.is_absolute():
        policy_path = Path(r.path) / req.policy_file
    try:
        policy = load_policy(policy_path)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    checker = BoundaryChecker(store)
    violations = checker.check(r.id, policy)
    return {"repo": repo, "violations": [v.model_dump() for v in violations]}


@api.post("/repos/{repo}/failures")
def ingest_failure(repo: str, req: FailureRequest):
    r = store.get_repo(repo)
    if not r:
        raise HTTPException(404, f"Unknown repo {repo}")
    event = failure_ingestor.ingest(
        r.id, kind=req.kind, message=req.message,
        stack_trace=req.stack_trace, source=req.source, language=req.language,
    )
    return {"id": event.id, "kind": event.kind, "functions_matched": event.function_ids}


@api.get("/repos/{repo}/failures/{function}")
def get_failures(repo: str, function: str):
    r = store.get_repo(repo)
    if not r:
        raise HTTPException(404, f"Unknown repo {repo}")
    fn = store.get_function_by_name(r.id, function)
    if not fn:
        matches = store.find_functions(r.id, function, 1)
        if not matches:
            raise HTTPException(404, f"No function matched {function!r}")
        fn = matches[0]
    return [e.model_dump() for e in store.failures_for_function(fn.id)]


@api.get("/repos/{repo}/failures")
def recent_failures(repo: str, limit: int = 20):
    r = store.get_repo(repo)
    if not r:
        raise HTTPException(404, f"Unknown repo {repo}")
    return [e.model_dump() for e in store.recent_failures(r.id, limit)]


# ── Editor / IDE integration ────────────────────────────────────────────────


@api.get("/repos/{repo}/function-at")
def function_at(repo: str, file: str, line: int):
    """Resolve a (file, line) cursor position to a function summary.

    Powers the VS Code hover card: returns the function and the headline stats
    (callers, callees, related tests, last-change, risk).
    """
    from codegraph_mcp.query.git_history import last_change_for_range

    r = store.get_repo(repo)
    if not r:
        raise HTTPException(404, f"Unknown repo {repo}")
    fn = store.function_at(r.id, file, line)
    if not fn:
        raise HTTPException(404, f"No function at {file}:{line}")
    callers = store.callers(fn.id)
    callees = store.callees(fn.id)
    unresolved = store.unresolved_callees(fn.id)
    tests = store.related_tests(r.id, fn)
    last_change = last_change_for_range(
        Path(r.path), file, fn.start_line, fn.end_line
    )
    # Merge resolved + unresolved callee names for full outbound call context
    callee_names: list[str] = [c.qualified_name for c in callees]
    callee_names += [
        e.target_symbol_name for e in unresolved
        if e.target_symbol_name and e.target_symbol_name not in callee_names
    ]
    return {
        "repo": repo,
        "function": {
            "qualified_name": fn.qualified_name,
            "name": fn.name,
            "kind": fn.kind,
            "file": store.file_path(fn.file_id),
            "start_line": fn.start_line,
            "end_line": fn.end_line,
            "loc": fn.loc,
            "signature": fn.signature,
            "parameters": fn.parameters_json,
            "return_type": fn.return_type,
            "decorators": fn.decorators,
            "enclosing_class": fn.enclosing_class,
            "is_async": fn.is_async,
            "is_test": fn.is_test,
            "complexity": fn.complexity,
            "summary": fn.descriptor.summary if fn.descriptor else None,
            "purpose": fn.descriptor.purpose if fn.descriptor else None,
            "category": fn.descriptor.category if fn.descriptor else None,
            "docstring": (
                fn.descriptor.raw
                if fn.descriptor and fn.descriptor.source == "docstring"
                else None
            ),
            "side_effects": fn.descriptor.side_effects if fn.descriptor else [],
        },
        "stats": {
            "caller_count": len(callers),
            "callee_count": len(callee_names),
            "test_count": len(tests),
        },
        "callers": [
            {
                "qualified_name": c.qualified_name,
                "file": store.file_path(c.file_id),
                "line": c.start_line,
            }
            for c in callers[:25]
        ],
        "callees": callee_names[:25],
        "tests": [
            {
                "qualified_name": t.qualified_name,
                "file": store.file_path(t.file_id),
                "line": t.start_line,
            }
            for t in tests[:25]
        ],
        "last_change": last_change,
    }


class DiffBlastRequest(BaseModel):
    base: str = "main"
    head: str = "HEAD"
    depth: int = 2
    max_tokens: int = 4000


@api.post("/repos/{repo}/diff-blast-radius")
def diff_blast_radius(repo: str, req: DiffBlastRequest):
    """Compute blast-radius for every function changed between two git refs.

    Returns one entry per changed function with its impact summary. Powers the
    GitHub Action that posts a PR comment.
    """
    from codegraph_mcp.query.git_history import changed_line_ranges

    r = store.get_repo(repo)
    if not r:
        raise HTTPException(404, f"Unknown repo {repo}")
    changes = changed_line_ranges(Path(r.path), req.base, req.head)
    if not changes:
        return {"repo": repo, "base": req.base, "head": req.head, "functions": []}

    seen: set[str] = set()
    out: list[dict] = []
    for path, lines in changes.items():
        fns = store.functions_at_lines(r.id, path, lines)
        for fn in fns:
            if fn.id in seen:
                continue
            seen.add(fn.id)
            try:
                report = impact_engine.function_impact(
                    repo, fn.qualified_name, req.depth, req.max_tokens, True
                )
            except ValueError:
                continue
            out.append(
                {
                    "qualified_name": fn.qualified_name,
                    "file": store.file_path(fn.file_id),
                    "start_line": fn.start_line,
                    "end_line": fn.end_line,
                    "risk_score": report.risk_score,
                    "risk_level": report.risk_level,
                    "direct_callers": len(report.direct_callers),
                    "transitive_callers": len(report.transitive_callers),
                    "direct_callees": len(report.direct_callees),
                    "related_tests": len(report.related_tests),
                    "runtime_entrypoints": len(report.runtime_entrypoints),
                    "reasons": report.reasons,
                    "recommended_validation": report.recommended_validation,
                }
            )
    out.sort(key=lambda x: x["risk_score"], reverse=True)
    return {"repo": repo, "base": req.base, "head": req.head, "functions": out}
