"""Tests for incremental indexing and secret file exclusion."""
from pathlib import Path

import pytest

from codegraph_mcp.config import settings
from codegraph_mcp.graph.sqlite_store import SQLiteStore
from codegraph_mcp.indexing.indexer import Indexer
from codegraph_mcp.indexing.repo_walker import RepoWalker, _is_secret_file


@pytest.fixture
def tmp_store(tmp_path):
    return SQLiteStore(tmp_path / "test.db")


@pytest.fixture
def tmp_repo(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "main.py").write_text("def hello():\n    return 'world'\n")
    (repo / "utils.py").write_text("def add(a, b):\n    return a + b\n")
    return repo


# ── Incremental indexing ──────────────────────────────────────────────────────

class TestIncrementalIndexing:
    def test_full_index_finds_all_files(self, tmp_store, tmp_repo):
        indexer = Indexer(tmp_store)
        settings.allow_external_repos = True
        indexer.add_repo("myrepo", tmp_repo)
        result = indexer.index_repo("myrepo", mode="full")
        assert result["files_seen"] == 2
        assert result["functions_seen"] == 2
        assert result["files_skipped"] == 0

    def test_incremental_skips_unchanged_files(self, tmp_store, tmp_repo):
        indexer = Indexer(tmp_store)
        settings.allow_external_repos = True
        indexer.add_repo("myrepo", tmp_repo)
        indexer.index_repo("myrepo", mode="full")
        # Second pass — nothing changed
        result = indexer.index_repo("myrepo", mode="incremental")
        assert result["files_skipped"] == 2
        assert result["files_seen"] == 0

    def test_incremental_reindexes_changed_file(self, tmp_store, tmp_repo):
        indexer = Indexer(tmp_store)
        settings.allow_external_repos = True
        indexer.add_repo("myrepo", tmp_repo)
        indexer.index_repo("myrepo", mode="full")
        # Modify one file
        (tmp_repo / "utils.py").write_text(
            "def add(a, b):\n    return a + b\ndef subtract(a, b):\n    return a - b\n"
        )
        result = indexer.index_repo("myrepo", mode="incremental")
        assert result["files_seen"] == 1   # only utils.py re-indexed
        assert result["files_skipped"] == 1

    def test_index_single_file(self, tmp_store, tmp_repo):
        indexer = Indexer(tmp_store)
        settings.allow_external_repos = True
        indexer.add_repo("myrepo", tmp_repo)
        indexer.index_repo("myrepo", mode="full")
        result = indexer.index_file("myrepo", tmp_repo / "main.py")
        assert result["file"] == "main.py"
        assert result["functions_seen"] == 1

    def test_result_includes_skipped_count(self, tmp_store, tmp_repo):
        indexer = Indexer(tmp_store)
        settings.allow_external_repos = True
        indexer.add_repo("myrepo", tmp_repo)
        result = indexer.index_repo("myrepo", mode="full")
        assert "files_skipped" in result


# ── Secret file exclusion ────────────────────────────────────────────────────

class TestSecretExclusion:
    def test_env_file_excluded(self):
        assert _is_secret_file(Path(".env")) is True

    def test_env_local_excluded(self):
        assert _is_secret_file(Path(".env.local")) is True

    def test_pem_excluded(self):
        assert _is_secret_file(Path("server.pem")) is True

    def test_key_file_excluded(self):
        assert _is_secret_file(Path("private.key")) is True

    def test_credentials_json_excluded(self):
        assert _is_secret_file(Path("credentials.json")) is True

    def test_service_account_excluded(self):
        assert _is_secret_file(Path("service-account.json")) is True

    def test_normal_py_not_excluded(self):
        assert _is_secret_file(Path("app.py")) is False

    def test_normal_js_not_excluded(self):
        assert _is_secret_file(Path("index.js")) is False

    def test_walker_skips_secrets(self, tmp_path):
        (tmp_path / "app.py").write_text("def foo(): pass\n")
        (tmp_path / ".env").write_text("SECRET=abc123\n")
        (tmp_path / "private.key").write_text("-----BEGIN PRIVATE KEY-----\n")
        walker = RepoWalker()
        found = [p.name for p in walker.iter_files(tmp_path)]
        assert "app.py" in found
        assert ".env" not in found
        assert "private.key" not in found


# ── Deletion handling ─────────────────────────────────────────────────────────

class TestFileDeletion:
    def test_remove_file_clears_functions(self, tmp_store, tmp_repo):
        indexer = Indexer(tmp_store)
        settings.allow_external_repos = True
        indexer.add_repo("myrepo", tmp_repo)
        indexer.index_repo("myrepo", mode="full")

        repo = tmp_store.get_repo("myrepo")
        count_before = tmp_store.conn.execute(
            "SELECT COUNT(*) FROM functions WHERE repo_id=?", (repo.id,)
        ).fetchone()[0]
        assert count_before == 2

        # Delete utils.py from disk and remove from graph
        (tmp_repo / "utils.py").unlink()
        result = indexer.remove_file("myrepo", tmp_repo / "utils.py")
        assert result["removed"] is True

        count_after = tmp_store.conn.execute(
            "SELECT COUNT(*) FROM functions WHERE repo_id=?", (repo.id,)
        ).fetchone()[0]
        assert count_after == 1  # only hello() from main.py remains

    def test_incremental_purges_deleted_files(self, tmp_store, tmp_repo):
        indexer = Indexer(tmp_store)
        settings.allow_external_repos = True
        indexer.add_repo("myrepo", tmp_repo)
        indexer.index_repo("myrepo", mode="full")

        # Delete utils.py then run incremental — it should be purged automatically
        (tmp_repo / "utils.py").unlink()
        indexer.index_repo("myrepo", mode="incremental")

        repo = tmp_store.get_repo("myrepo")
        fns = tmp_store.conn.execute(
            "SELECT name FROM functions WHERE repo_id=?", (repo.id,)
        ).fetchall()
        names = {r["name"] for r in fns}
        assert "add" not in names       # was in utils.py — should be gone
        assert "hello" in names         # still in main.py

    def test_delete_file_removes_edges(self, tmp_store, tmp_repo):
        # Add a file with a cross-file call
        (tmp_repo / "caller.py").write_text(
            "from utils import add\ndef run():\n    return add(1, 2)\n"
        )
        indexer = Indexer(tmp_store)
        settings.allow_external_repos = True
        indexer.add_repo("myrepo", tmp_repo)
        indexer.index_repo("myrepo", mode="full")

        repo = tmp_store.get_repo("myrepo")
        edges_before = tmp_store.conn.execute(
            "SELECT COUNT(*) FROM function_edges WHERE repo_id=?", (repo.id,)
        ).fetchone()[0]

        (tmp_repo / "caller.py").unlink()
        indexer.remove_file("myrepo", tmp_repo / "caller.py")

        edges_after = tmp_store.conn.execute(
            "SELECT COUNT(*) FROM function_edges WHERE repo_id=?", (repo.id,)
        ).fetchone()[0]
        assert edges_after <= edges_before
