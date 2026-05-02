"""Lightweight git history queries used by REST/CLI/VS Code integrations.

Shells out to ``git`` for last-modified info on a file/range. All operations are
read-only and best-effort: if ``git`` is missing, the repo isn't a git
repository, or the path isn't tracked, the helper returns ``None`` rather than
raising. We never run user-supplied shell — only a fixed set of git arguments
with positional path arguments.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _git_available() -> bool:
    return shutil.which("git") is not None


def _run(args: list[str], cwd: Path) -> str | None:
    if not _git_available():
        return None
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def last_change_for_file(repo_path: Path, rel_path: str) -> dict | None:
    """Return ``{commit, author, date_iso, subject, days_ago}`` for the most
    recent commit touching ``rel_path`` (or ``None`` if unknown)."""
    rel = rel_path.replace("\\", "/")
    out = _run(
        ["log", "-1", "--format=%H%x09%an%x09%aI%x09%s", "--", rel],
        repo_path,
    )
    if not out:
        return None
    parts = out.split("\t", 3)
    if len(parts) < 4:
        return None
    commit, author, date_iso, subject = parts
    return _format_change(commit, author, date_iso, subject)


def last_change_for_range(
    repo_path: Path, rel_path: str, start_line: int, end_line: int
) -> dict | None:
    """Return the most recent commit touching the line range.

    Uses ``git log -L start,end:file``. Returns ``None`` for renames or files
    not tracked by git.
    """
    rel = rel_path.replace("\\", "/")
    if start_line < 1:
        start_line = 1
    if end_line < start_line:
        end_line = start_line
    out = _run(
        [
            "log",
            "-1",
            "--no-patch",
            "--format=%H%x09%an%x09%aI%x09%s",
            f"-L{start_line},{end_line}:{rel}",
        ],
        repo_path,
    )
    if not out:
        # Fall back to file-level history.
        return last_change_for_file(repo_path, rel)
    line = out.splitlines()[0] if out else ""
    parts = line.split("\t", 3)
    if len(parts) < 4:
        return last_change_for_file(repo_path, rel)
    commit, author, date_iso, subject = parts
    return _format_change(commit, author, date_iso, subject)


def changed_files(repo_path: Path, base_ref: str, head_ref: str = "HEAD") -> list[str]:
    """List files changed between two refs (relative paths, forward slashes)."""
    out = _run(
        ["diff", "--name-only", f"{base_ref}...{head_ref}"], repo_path
    )
    if out is None:
        return []
    return [line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()]


def changed_line_ranges(
    repo_path: Path, base_ref: str, head_ref: str = "HEAD"
) -> dict[str, set[int]]:
    """Return a map of ``rel_path -> {changed_line_numbers_in_head}``.

    Parses unified-diff hunk headers (``@@ -a,b +c,d @@``) and records the new
    file's line numbers. This is what we use to map a PR diff to functions via
    :meth:`SQLiteStore.functions_at_lines`.
    """
    out = _run(
        ["diff", "--unified=0", f"{base_ref}...{head_ref}"], repo_path
    )
    if not out:
        return {}
    result: dict[str, set[int]] = {}
    current: str | None = None
    for line in out.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            # Strip "b/" prefix that git adds.
            if path.startswith("b/"):
                path = path[2:]
            if path == "/dev/null":
                current = None
            else:
                current = path.replace("\\", "/")
                result.setdefault(current, set())
        elif line.startswith("@@") and current:
            # @@ -a,b +c,d @@ ...
            try:
                plus = line.split("+", 1)[1].split(" ", 1)[0]
                if "," in plus:
                    start_s, count_s = plus.split(",", 1)
                    start = int(start_s)
                    count = int(count_s)
                else:
                    start = int(plus)
                    count = 1
            except (ValueError, IndexError):
                continue
            if count == 0:
                continue
            for ln in range(start, start + count):
                result[current].add(ln)
    return result


def _format_change(commit: str, author: str, date_iso: str, subject: str) -> dict:
    days_ago: float | None = None
    try:
        dt = datetime.fromisoformat(date_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        days_ago = round(
            (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0, 2
        )
    except ValueError:
        pass
    return {
        "commit": commit[:12],
        "author": author,
        "date": date_iso,
        "subject": subject,
        "days_ago": days_ago,
        "human": _humanize(days_ago) if days_ago is not None else None,
    }


def _humanize(days: float) -> str:
    if days < 1 / 24:
        return "just now"
    if days < 1:
        hours = max(1, int(days * 24))
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    if days < 30:
        d = int(round(days))
        return f"{d} day{'s' if d != 1 else ''} ago"
    if days < 365:
        m = int(round(days / 30))
        return f"{m} month{'s' if m != 1 else ''} ago"
    y = int(round(days / 365))
    return f"{y} year{'s' if y != 1 else ''} ago"
