from codegraph_mcp.graph.sqlite_store import SQLiteStore
from codegraph_mcp.indexing.indexer import Indexer


def test_store_schema(tmp_path, monkeypatch):
    from codegraph_mcp import config
    repo_root = tmp_path / "repos"
    repo_root.mkdir()
    sample = repo_root / "sample"
    sample.mkdir()
    (sample / "app.py").write_text("def hello():\n    return world()\n\ndef world():\n    return 1\n")
    monkeypatch.setattr(config.settings, "repo_root", repo_root)
    store = SQLiteStore(tmp_path / "cg.db")
    indexer = Indexer(store)
    indexer.add_repo("sample", sample)
    res = indexer.index_repo("sample")
    assert res["functions_seen"] == 2
    repo = store.get_repo("sample")
    assert repo
    fns = store.find_functions(repo.id, "hello")
    assert fns
