"""Tests for Python indexing: functions, descriptors, edges, async, classes."""
from __future__ import annotations

from codegraph_mcp.graph.sqlite_store import SQLiteStore
from codegraph_mcp.indexing.indexer import Indexer


def _setup(tmp_path, monkeypatch):
    from codegraph_mcp import config
    repo_root = tmp_path / "repos"
    repo_root.mkdir()
    monkeypatch.setattr(config.settings, "repo_root", repo_root)
    store = SQLiteStore(tmp_path / "cg.db")
    indexer = Indexer(store)
    return store, indexer, repo_root


def _make_repo(repo_root, name, files):
    d = repo_root / name
    d.mkdir()
    for fname, content in files.items():
        p = d / fname
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return d


def test_basic_function_extraction(tmp_path, monkeypatch):
    store, indexer, repo_root = _setup(tmp_path, monkeypatch)
    d = _make_repo(repo_root, "s", {"app.py": "def hello():\n    return 1\ndef world():\n    return 2\n"})
    indexer.add_repo("s", d)
    res = indexer.index_repo("s")
    assert res["functions_seen"] == 2
    repo = store.get_repo("s")
    fns = store.find_functions(repo.id, "hello")
    assert len(fns) == 1
    assert fns[0].name == "hello"


def test_docstring_descriptor(tmp_path, monkeypatch):
    store, indexer, repo_root = _setup(tmp_path, monkeypatch)
    code = '''
def greet(name: str) -> str:
    """Return a greeting string.

    Args:
        name: The person to greet.

    Returns:
        str: Greeting message.
    """
    return f"Hello, {name}"
'''
    d = _make_repo(repo_root, "s", {"greet.py": code})
    indexer.add_repo("s", d)
    indexer.index_repo("s")
    repo = store.get_repo("s")
    fns = store.find_functions(repo.id, "greet")
    assert fns
    fn = fns[0]
    assert fn.descriptor is not None
    assert fn.descriptor.source == "docstring"
    assert fn.descriptor.quality_score >= 0.6


def test_async_function(tmp_path, monkeypatch):
    store, indexer, repo_root = _setup(tmp_path, monkeypatch)
    d = _make_repo(repo_root, "s", {"a.py": "async def fetch(url: str):\n    pass\n"})
    indexer.add_repo("s", d)
    indexer.index_repo("s")
    repo = store.get_repo("s")
    fns = store.find_functions(repo.id, "fetch")
    assert fns[0].is_async is True


def test_class_method(tmp_path, monkeypatch):
    store, indexer, repo_root = _setup(tmp_path, monkeypatch)
    code = "class Svc:\n    def run(self, x):\n        return x\n"
    d = _make_repo(repo_root, "s", {"svc.py": code})
    indexer.add_repo("s", d)
    indexer.index_repo("s")
    repo = store.get_repo("s")
    fns = store.find_functions(repo.id, "run")
    assert fns
    assert fns[0].enclosing_class == "Svc"


def test_call_edges(tmp_path, monkeypatch):
    store, indexer, repo_root = _setup(tmp_path, monkeypatch)
    code = "def foo():\n    return bar()\n\ndef bar():\n    return 1\n"
    d = _make_repo(repo_root, "s", {"c.py": code})
    indexer.add_repo("s", d)
    indexer.index_repo("s")
    repo = store.get_repo("s")
    foo = store.get_function_by_name(repo.id, "foo")
    assert foo
    callees = store.callees(foo.id)
    assert any(c.name == "bar" for c in callees)


def test_test_function_detected(tmp_path, monkeypatch):
    store, indexer, repo_root = _setup(tmp_path, monkeypatch)
    code = "def test_something():\n    assert True\n"
    d = _make_repo(repo_root, "s", {"tests/test_x.py": code})
    indexer.add_repo("s", d)
    indexer.index_repo("s")
    repo = store.get_repo("s")
    fns = store.find_functions(repo.id, "test_something")
    assert fns
    assert fns[0].is_test is True


def test_stable_id_survives_same_content(tmp_path, monkeypatch):
    """Same function in same file should produce same ID both times."""
    store, indexer, repo_root = _setup(tmp_path, monkeypatch)
    code = "def stable_func(a, b):\n    return a + b\n"
    d = _make_repo(repo_root, "s", {"mod.py": code})
    indexer.add_repo("s", d)
    indexer.index_repo("s")
    repo = store.get_repo("s")
    id1 = store.find_functions(repo.id, "stable_func")[0].id
    indexer.index_repo("s")
    id2 = store.find_functions(repo.id, "stable_func")[0].id
    assert id1 == id2


def test_inferred_descriptor_fallback(tmp_path, monkeypatch):
    store, indexer, repo_root = _setup(tmp_path, monkeypatch)
    d = _make_repo(repo_root, "s", {"m.py": "def process_order(order):\n    return order\n"})
    indexer.add_repo("s", d)
    indexer.index_repo("s")
    repo = store.get_repo("s")
    fn = store.find_functions(repo.id, "process_order")[0]
    assert fn.descriptor is not None
    assert fn.descriptor.source in ("docstring", "inferred_static")
