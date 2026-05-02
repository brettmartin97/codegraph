"""Failure memory: parse stack traces and ingest failure events.

Usage::

    from codegraph_mcp.memory.failures import FailureIngestor
    ingestor = FailureIngestor(store)
    event = ingestor.ingest(repo_id, kind="test_failure", message="AssertionError",
                            stack_trace=traceback_text, source="pytest")
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from codegraph_mcp.graph.models import FailureEvent
from codegraph_mcp.graph.sqlite_store import SQLiteStore

# Python traceback frame: '  File "path/to/file.py", line 42, in function_name'
_PY_FRAME_RE = re.compile(
    r'File "(?P<file>[^"]+)",\s*line\s*(?P<line>\d+),\s*in\s*(?P<func>[^\n]+)'
)

# Java/Kotlin/Scala: '    at com.example.Foo.bar(Foo.java:42)'
_JAVA_FRAME_RE = re.compile(
    r"at\s+(?P<cls>[\w.$]+)\.(?P<func>\w+)\([^:]+:(?P<line>\d+)\)"
)

# Go: '    /path/to/file.go:42 +0x...'
_GO_FRAME_RE = re.compile(
    r"(?P<file>[^ \t]+\.go):(?P<line>\d+)\s+\+0x"
)

# Rust: '   0: path::module::function'
_RUST_FRAME_RE = re.compile(
    r"^\s+\d+:\s+(?P<func>[A-Za-z_:][A-Za-z0-9_:<>]+)", re.M
)

# Generic JS/TS: 'at functionName (file.ts:42:5)'
_JS_FRAME_RE = re.compile(
    r"at\s+(?P<func>[\w.<>]+)\s+\((?P<file>[^:)]+):(?P<line>\d+):\d+\)"
)


def parse_python_stacktrace(text: str) -> list[dict]:
    """Extract frames from a Python traceback string.

    Returns a list of dicts: ``{"file": str, "line": int, "function": str}``.
    """
    frames: list[dict] = []
    for m in _PY_FRAME_RE.finditer(text):
        frames.append({
            "file": m.group("file"),
            "line": int(m.group("line")),
            "function": m.group("func").strip(),
        })
    return frames


def parse_java_stacktrace(text: str) -> list[dict]:
    frames: list[dict] = []
    for m in _JAVA_FRAME_RE.finditer(text):
        frames.append({
            "file": m.group("cls"),   # class name used as file proxy
            "line": int(m.group("line")),
            "function": f"{m.group('cls')}.{m.group('func')}",
        })
    return frames


def parse_go_stacktrace(text: str) -> list[dict]:
    frames: list[dict] = []
    for m in _GO_FRAME_RE.finditer(text):
        frames.append({
            "file": m.group("file"),
            "line": int(m.group("line")),
            "function": "",
        })
    return frames


def parse_js_stacktrace(text: str) -> list[dict]:
    frames: list[dict] = []
    for m in _JS_FRAME_RE.finditer(text):
        frames.append({
            "file": m.group("file"),
            "line": int(m.group("line")),
            "function": m.group("func").strip(),
        })
    return frames


def parse_stacktrace(text: str, language: str = "python") -> list[dict]:
    """Auto-detect or parse a stack trace according to *language*."""
    parsers = {
        "python": parse_python_stacktrace,
        "java": parse_java_stacktrace,
        "kotlin": parse_java_stacktrace,
        "go": parse_go_stacktrace,
        "javascript": parse_js_stacktrace,
        "typescript": parse_js_stacktrace,
    }
    parser = parsers.get(language, parse_python_stacktrace)
    return parser(text)


class FailureIngestor:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def ingest(
        self,
        repo_id: str,
        kind: str,
        message: str,
        stack_trace: str = "",
        source: str = "unknown",
        language: str = "python",
        metadata: dict | None = None,
    ) -> FailureEvent:
        """Parse *stack_trace*, map frames to known functions, and save a FailureEvent.

        Returns the saved FailureEvent.
        """
        frames = parse_stacktrace(stack_trace, language)

        # Map frames to known function IDs
        matched_fn_ids: list[str] = []
        first_file: str | None = None
        first_line: int | None = None
        for frame in frames:
            file_path = frame.get("file", "")
            func_name = frame.get("function", "")
            line = frame.get("line")
            if first_file is None and file_path:
                first_file = file_path
                first_line = line
            if func_name:
                candidates = self.store.find_functions(repo_id, func_name, 3)
                for fn in candidates:
                    if fn.id not in matched_fn_ids:
                        matched_fn_ids.append(fn.id)
                    if len(matched_fn_ids) >= 10:
                        break
            if len(matched_fn_ids) >= 10:
                break

        event = FailureEvent(
            id=str(uuid.uuid4()),
            repo_id=repo_id,
            kind=kind,
            message=message[:2000],
            stack_trace=stack_trace[:8000],
            function_ids=matched_fn_ids,
            file_path=first_file or "",
            line=first_line,
            occurred_at=datetime.now(timezone.utc).isoformat(),
            source=source,
            metadata=metadata or {},
        )
        self.store.add_failure_event(event)
        return event
