from pathlib import Path

from codegraph_mcp.graph.models import CodeSlice, FunctionNode
from codegraph_mcp.graph.sqlite_store import SQLiteStore
from codegraph_mcp.security.redaction import redact


def source_slice(repo_path: Path, store: SQLiteStore, fn: FunctionNode, reason: str, score: float) -> CodeSlice:
    rel = store.file_path(fn.file_id)
    path = repo_path / rel
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        content = "\n".join(lines[max(0, fn.start_line - 1): fn.end_line])
    except Exception as exc:
        content = f"<failed to read source: {exc}>"
    return CodeSlice(file=rel, line_range=(fn.start_line, fn.end_line), content=redact(content), reason=reason, score=score, evidence=[{"function_id": fn.id, "qualified_name": fn.qualified_name}])
