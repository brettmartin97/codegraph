"""Tests for SQLiteStore operations."""
from __future__ import annotations

from codegraph_mcp.graph.models import EdgeType, FunctionEdge, FunctionNode, Repository
from codegraph_mcp.graph.sqlite_store import SQLiteStore
from codegraph_mcp.utils import sha256_text, stable_id


def _repo(name="r"):
    return Repository(id=stable_id("repo", name), name=name, path=f"/tmp/{name}")


def _fn(repo_id, file_id, name, qname=None, is_test=False, enclosing_class=None):
    sig = f"def {name}()"
    return FunctionNode(
        id=stable_id(repo_id, file_id, qname or name, sig),
        repo_id=repo_id,
        file_id=file_id,
        language="python",
        name=name,
        qualified_name=qname or name,
        display_name=name,
        start_line=1,
        end_line=5,
        body_hash=sha256_text(f"body-{name}"),
        signature_hash=sha256_text(sig),
        is_test=is_test,
        enclosing_class=enclosing_class,
    )


def test_add_and_get_repo(tmp_path):
    store = SQLiteStore(tmp_path / "cg.db")
    repo = _repo("myrepo")
    store.add_repo(repo)
    retrieved = store.get_repo("myrepo")
    assert retrieved is not None
    assert retrieved.name == "myrepo"


def test_list_repos(tmp_path):
    store = SQLiteStore(tmp_path / "cg.db")
    store.add_repo(_repo("a"))
    store.add_repo(_repo("b"))
    repos = store.list_repos()
    names = [r.name for r in repos]
    assert "a" in names
    assert "b" in names


def test_upsert_function(tmp_path):
    store = SQLiteStore(tmp_path / "cg.db")
    repo = _repo()
    store.add_repo(repo)
    fn = _fn(repo.id, "f1", "my_func")
    store.upsert_function(fn)
    store.conn.commit()
    retrieved = store.get_function(fn.id)
    assert retrieved is not None
    assert retrieved.name == "my_func"


def test_find_functions_by_name(tmp_path):
    store = SQLiteStore(tmp_path / "cg.db")
    repo = _repo()
    store.add_repo(repo)
    store.upsert_function(_fn(repo.id, "f1", "process_order"))
    store.upsert_function(_fn(repo.id, "f2", "cancel_order"))
    store.conn.commit()
    results = store.find_functions(repo.id, "order")
    assert len(results) == 2


def test_callers_and_callees(tmp_path):
    store = SQLiteStore(tmp_path / "cg.db")
    repo = _repo()
    store.add_repo(repo)
    caller = _fn(repo.id, "f1", "parent")
    callee = _fn(repo.id, "f2", "child")
    store.upsert_function(caller)
    store.upsert_function(callee)
    edge = FunctionEdge(
        id=stable_id(repo.id, caller.id, "CALLS", callee.id),
        repo_id=repo.id,
        source_function_id=caller.id,
        target_function_id=callee.id,
        edge_type=EdgeType.calls,
        confidence=0.9,
    )
    store.upsert_edge(edge)
    store.conn.commit()
    assert any(c.id == callee.id for c in store.callees(caller.id))
    assert any(c.id == caller.id for c in store.callers(callee.id))


def test_unresolved_callees(tmp_path):
    store = SQLiteStore(tmp_path / "cg.db")
    repo = _repo()
    store.add_repo(repo)
    fn = _fn(repo.id, "f1", "caller")
    store.upsert_function(fn)
    edge = FunctionEdge(
        id=stable_id(repo.id, fn.id, "CALLS", "mystery_func"),
        repo_id=repo.id,
        source_function_id=fn.id,
        target_function_id=None,
        target_symbol_name="mystery_func",
        edge_type=EdgeType.calls,
        confidence=0.4,
    )
    store.upsert_edge(edge)
    store.conn.commit()
    unresolved = store.unresolved_callees(fn.id)
    assert len(unresolved) == 1
    assert unresolved[0].target_symbol_name == "mystery_func"


def test_related_tests(tmp_path):
    store = SQLiteStore(tmp_path / "cg.db")
    repo = _repo()
    store.add_repo(repo)
    target = _fn(repo.id, "app.py", "process_order")
    test_fn = _fn(repo.id, "tests/test_order.py", "test_process_order", is_test=True)
    store.upsert_function(target)
    store.upsert_function(test_fn)
    # need file records for file_path lookups
    from codegraph_mcp.graph.models import CodeFile
    store.upsert_file(CodeFile(id="app.py", repo_id=repo.id, path="app.py", content_hash="a"))
    store.upsert_file(CodeFile(id="tests/test_order.py", repo_id=repo.id, path="tests/test_order.py", content_hash="b"))
    store.conn.commit()
    tests = store.related_tests(repo.id, target)
    assert any(t.name == "test_process_order" for t in tests)


def test_overview(tmp_path):
    store = SQLiteStore(tmp_path / "cg.db")
    repo = _repo()
    store.add_repo(repo)
    for i in range(5):
        store.upsert_function(_fn(repo.id, f"f{i}", f"func_{i}"))
    store.conn.commit()
    ov = store.overview(repo.id)
    assert ov["function_count"] == 5


def test_edge_resolution(tmp_path):
    store = SQLiteStore(tmp_path / "cg.db")
    repo = _repo()
    store.add_repo(repo)
    caller = _fn(repo.id, "f1", "parent")
    callee = _fn(repo.id, "f2", "my_helper", "utils.my_helper")
    store.upsert_function(caller)
    store.upsert_function(callee)
    edge = FunctionEdge(
        id=stable_id(repo.id, caller.id, "CALLS", "my_helper"),
        repo_id=repo.id,
        source_function_id=caller.id,
        target_function_id=None,
        target_symbol_name="my_helper",
        edge_type=EdgeType.calls,
        confidence=0.5,
    )
    store.upsert_edge(edge)
    store.conn.commit()
    store.resolve_edges(repo.id)
    resolved = store.callees(caller.id)
    assert any(c.id == callee.id for c in resolved)


def test_transitive_callers(tmp_path):
    store = SQLiteStore(tmp_path / "cg.db")
    repo = _repo()
    store.add_repo(repo)
    a = _fn(repo.id, "f1", "a")
    b = _fn(repo.id, "f2", "b")
    c = _fn(repo.id, "f3", "c")
    for fn in [a, b, c]:
        store.upsert_function(fn)
    for src, tgt in [(a, b), (b, c)]:
        store.upsert_edge(FunctionEdge(
            id=stable_id(repo.id, src.id, "CALLS", tgt.id),
            repo_id=repo.id, source_function_id=src.id, target_function_id=tgt.id,
            edge_type=EdgeType.calls, confidence=0.9,
        ))
    store.conn.commit()
    trans = store.transitive_callers(c.id, depth=5)
    ids = {f.id for f in trans}
    assert a.id in ids
    assert b.id in ids
