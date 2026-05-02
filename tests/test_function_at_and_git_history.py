"""Tests for the new editor/CI surfaces: function_at lookup, git diff parsing,
and the diff blast-radius CLI/REST contract.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codegraph_mcp.graph.sqlite_store import SQLiteStore
from codegraph_mcp.indexing.indexer import Indexer
from codegraph_mcp.query import git_history

# ── function_at ──────────────────────────────────────────────────────────────


def _bootstrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from codegraph_mcp import config

    repo_root = tmp_path / "repos"
    repo_root.mkdir()
    sample = repo_root / "sample"
    sample.mkdir()
    (sample / "app.py").write_text(
        "def outer():\n"               # line 1
        "    return inner()\n"         # line 2
        "\n"                            # line 3
        "def inner():\n"               # line 4
        "    return 42\n"              # line 5
    )
    monkeypatch.setattr(config.settings, "repo_root", repo_root)
    store = SQLiteStore(tmp_path / "cg.db")
    indexer = Indexer(store)
    indexer.add_repo("sample", sample)
    indexer.index_repo("sample")
    return store, sample


def test_function_at_resolves_innermost(tmp_path, monkeypatch):
    store, _ = _bootstrap(tmp_path, monkeypatch)
    repo = store.get_repo("sample")
    assert repo

    fn1 = store.function_at(repo.id, "app.py", 2)
    assert fn1 is not None
    assert fn1.name == "outer"

    fn2 = store.function_at(repo.id, "app.py", 5)
    assert fn2 is not None
    assert fn2.name == "inner"


def test_function_at_unknown_returns_none(tmp_path, monkeypatch):
    store, _ = _bootstrap(tmp_path, monkeypatch)
    repo = store.get_repo("sample")
    assert repo
    assert store.function_at(repo.id, "missing.py", 1) is None
    # Line outside any function body.
    assert store.function_at(repo.id, "app.py", 3) is None


# ── unified diff parsing ────────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.mark.skipif(
    not git_history._git_available(), reason="git not installed on this runner"
)
def test_changed_line_ranges(tmp_path):
    repo = tmp_path / "g"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.py").write_text("a = 1\nb = 2\nc = 3\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "f.py").write_text("a = 1\nb = 99\nc = 3\nd = 4\n")
    _git(repo, "commit", "-q", "-am", "edit")

    changes = git_history.changed_line_ranges(repo, "main~1", "HEAD")
    assert "f.py" in changes
    # Line 2 was modified, line 4 was added.
    assert changes["f.py"] >= {2, 4}


@pytest.mark.skipif(
    not git_history._git_available(), reason="git not installed on this runner"
)
def test_last_change_for_file(tmp_path):
    repo = tmp_path / "g"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "first commit subject")

    info = git_history.last_change_for_file(repo, "f.py")
    assert info is not None
    assert info["subject"] == "first commit subject"
    assert info["author"] == "t"
    assert info["days_ago"] is not None
    assert info["human"] is not None
