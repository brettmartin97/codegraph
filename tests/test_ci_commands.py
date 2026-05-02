"""Tests for codegraph ci changed-functions and pr-comment commands."""
import json
import textwrap

import pytest
from typer.testing import CliRunner

from codegraph_mcp.cli.app import app
from codegraph_mcp.config import settings
from codegraph_mcp.graph.sqlite_store import SQLiteStore
from codegraph_mcp.indexing.indexer import Indexer

runner = CliRunner()


@pytest.fixture()
def tmp_repo(tmp_path):
    db = SQLiteStore(tmp_path / "cg.db")
    idx = Indexer(db)

    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    (repo_dir / "svc.py").write_text(textwrap.dedent("""\
        def process_order(order_id):
            validate(order_id)
            return charge(order_id)

        def validate(order_id):
            if not order_id:
                raise ValueError

        def charge(order_id):
            return True
    """))
    return db, idx, repo_dir


def _patch_global_store(monkeypatch, db, idx):
    """Point the CLI's module-level store/indexer/impact_engine at our tmp fixtures."""
    import codegraph_mcp.cli.app as cli_mod
    from codegraph_mcp.query.impact import ImpactEngine
    monkeypatch.setattr(cli_mod, "store", db)
    monkeypatch.setattr(cli_mod, "indexer", idx)
    monkeypatch.setattr(cli_mod, "impact_engine", ImpactEngine(db))


class TestFunctionsAtLines:
    def test_overlapping_lines_returns_function(self, tmp_path):
        db = SQLiteStore(tmp_path / "cg.db")
        idx = Indexer(db)
        repo_dir = tmp_path / "r"
        repo_dir.mkdir()
        (repo_dir / "foo.py").write_text(
            "def alpha():\n    pass\n\ndef beta():\n    pass\n"
        )
        settings.allow_external_repos = True
        settings.repo_root = tmp_path
        idx.add_repo("r", repo_dir)
        idx.index_repo("r")

        repo = db.get_repo("r")
        # alpha starts at line 1
        fns = db.functions_at_lines(repo.id, "foo.py", {1, 2})
        assert any(f.name == "alpha" for f in fns)

    def test_non_overlapping_lines_returns_empty(self, tmp_path):
        db = SQLiteStore(tmp_path / "cg.db")
        idx = Indexer(db)
        repo_dir = tmp_path / "r"
        repo_dir.mkdir()
        (repo_dir / "foo.py").write_text(
            "def alpha():\n    pass\n\ndef beta():\n    pass\n"
        )
        settings.allow_external_repos = True
        settings.repo_root = tmp_path
        idx.add_repo("r", repo_dir)
        idx.index_repo("r")

        repo = db.get_repo("r")
        # line 100 doesn't exist in the file
        fns = db.functions_at_lines(repo.id, "foo.py", {100})
        assert fns == []

    def test_unknown_file_returns_empty(self, tmp_path):
        db = SQLiteStore(tmp_path / "cg.db")
        idx = Indexer(db)
        repo_dir = tmp_path / "r"
        repo_dir.mkdir()
        (repo_dir / "foo.py").write_text("def alpha():\n    pass\n")
        settings.allow_external_repos = True
        settings.repo_root = tmp_path
        idx.add_repo("r", repo_dir)
        idx.index_repo("r")
        repo = db.get_repo("r")
        fns = db.functions_at_lines(repo.id, "nonexistent.py", {1})
        assert fns == []


class TestChangedFunctionsCommand:
    SAMPLE_DIFF = textwrap.dedent("""\
        diff --git a/svc.py b/svc.py
        index abc..def 100644
        --- a/svc.py
        +++ b/svc.py
        @@ -1,3 +1,4 @@
         def process_order(order_id):
        -    validate(order_id)
        +    validate(order_id)  # patched
             return charge(order_id)
    """)

    def test_returns_json_list(self, tmp_path, monkeypatch):
        db = SQLiteStore(tmp_path / "cg.db")
        idx = Indexer(db)
        repo_dir = tmp_path / "myrepo"
        repo_dir.mkdir()
        (repo_dir / "svc.py").write_text(
            "def process_order(order_id):\n    return True\n"
        )
        settings.allow_external_repos = True
        settings.repo_root = tmp_path
        idx.add_repo("myrepo", repo_dir)
        idx.index_repo("myrepo")
        _patch_global_store(monkeypatch, db, idx)

        diff_file = tmp_path / "pr.diff"
        diff_file.write_text(self.SAMPLE_DIFF)
        result = runner.invoke(app, ["ci", "changed-functions", "myrepo", "--diff", str(diff_file)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, list)
        # process_order starts at line 1, diff touches line 2 → overlaps
        names = [r["function"] for r in data]
        assert "process_order" in names

    def test_empty_diff_returns_empty_list(self, tmp_path, monkeypatch):
        db = SQLiteStore(tmp_path / "cg.db")
        idx = Indexer(db)
        repo_dir = tmp_path / "r"
        repo_dir.mkdir()
        (repo_dir / "x.py").write_text("def foo():\n    pass\n")
        settings.allow_external_repos = True
        settings.repo_root = tmp_path
        idx.add_repo("r", repo_dir)
        idx.index_repo("r")
        _patch_global_store(monkeypatch, db, idx)

        diff_file = tmp_path / "empty.diff"
        diff_file.write_text("")
        result = runner.invoke(app, ["ci", "changed-functions", "r", "--diff", str(diff_file)])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == []
