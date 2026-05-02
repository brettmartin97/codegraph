"""Tests for prepare_change and context engine."""
from __future__ import annotations

from codegraph_mcp.graph.sqlite_store import SQLiteStore
from codegraph_mcp.indexing.indexer import Indexer
from codegraph_mcp.query.context import ContextEngine


def _setup(tmp_path, monkeypatch):
    from codegraph_mcp import config
    repo_root = tmp_path / "repos"
    repo_root.mkdir()
    monkeypatch.setattr(config.settings, "repo_root", repo_root)
    store = SQLiteStore(tmp_path / "cg.db")
    return store, Indexer(store), ContextEngine(store), repo_root


SAMPLE_CODE = '''
def create_order(payload: dict) -> dict:
    """Create a new order from payload.

    Args:
        payload: Order data dictionary.

    Returns:
        dict: Created order record.
    """
    validated = validate_order(payload)
    return save_order(validated)


def validate_order(payload: dict) -> dict:
    """Validate order payload fields."""
    if "item" not in payload:
        raise ValueError("Missing item")
    return payload


def save_order(data: dict) -> dict:
    """Persist order to database."""
    return {**data, "id": 42}


def test_create_order():
    result = create_order({"item": "widget"})
    assert result["id"] == 42
'''


def test_prepare_change_finds_target(tmp_path, monkeypatch):
    store, indexer, ctx, repo_root = _setup(tmp_path, monkeypatch)
    d = repo_root / "shop"
    d.mkdir()
    (d / "orders.py").write_text(SAMPLE_CODE)
    indexer.add_repo("shop", d)
    indexer.index_repo("shop")
    result = ctx.prepare_change("shop", "fix order validation logic")
    assert result["target_functions"]
    names = [f["qualified_name"] for f in result["target_functions"]]
    assert any("order" in n.lower() for n in names)


def test_prepare_change_returns_contracts(tmp_path, monkeypatch):
    store, indexer, ctx, repo_root = _setup(tmp_path, monkeypatch)
    d = repo_root / "shop"
    d.mkdir()
    (d / "orders.py").write_text(SAMPLE_CODE)
    indexer.add_repo("shop", d)
    indexer.index_repo("shop")
    result = ctx.prepare_change("shop", "modify create_order signature")
    assert "contracts_to_preserve" in result
    assert "validation_recipe" in result


def test_prepare_change_includes_validation_recipe(tmp_path, monkeypatch):
    store, indexer, ctx, repo_root = _setup(tmp_path, monkeypatch)
    d = repo_root / "shop"
    d.mkdir()
    (d / "orders.py").write_text(SAMPLE_CODE)
    indexer.add_repo("shop", d)
    indexer.index_repo("shop")
    result = ctx.prepare_change("shop", "fix create_order", max_tokens=4000)
    assert isinstance(result["validation_recipe"], list)
    assert len(result["validation_recipe"]) > 0


def test_prepare_change_unknown_repo(tmp_path, monkeypatch):
    store, indexer, ctx, repo_root = _setup(tmp_path, monkeypatch)
    import pytest
    with pytest.raises(ValueError, match="Unknown repo"):
        ctx.prepare_change("no_such_repo", "do something")


def test_repo_overview(tmp_path, monkeypatch):
    store, indexer, ctx, repo_root = _setup(tmp_path, monkeypatch)
    d = repo_root / "shop"
    d.mkdir()
    (d / "orders.py").write_text(SAMPLE_CODE)
    indexer.add_repo("shop", d)
    indexer.index_repo("shop")
    ov = ctx.repo_overview("shop")
    assert ov["function_count"] > 0
    assert "languages" in ov
