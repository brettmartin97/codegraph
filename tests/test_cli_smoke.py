"""Smoke tests for CLI entrypoints."""
from __future__ import annotations

import ast
import json
from pathlib import Path

from typer.testing import CliRunner

import codegraph_mcp.cli.app as cli_app
from codegraph_mcp.cli.app import app
from codegraph_mcp.config import Settings, ensure_runtime_dirs

runner = CliRunner()


def test_doctor():
    result = runner.invoke(app, ["doctor"])
    # doctor exits 1 when optional deps are missing (expected in CI without full extras)
    # what matters is that it runs and reports meaningful output
    assert result.exit_code in (0, 1)
    assert "database" in result.output.lower() or "Database" in result.output
    assert "CodeGraph" in result.output or "check" in result.output.lower()
    assert "OK database" in result.output or "FAIL database" in result.output


def test_cli_runtime_strings_are_ascii_safe():
    """CLI text should not crash legacy Windows consoles."""
    source = Path(cli_app.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    bad_strings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if not node.value.isascii():
                bad_strings.append((node.lineno, node.value))
    assert bad_strings == []


def test_ensure_runtime_dirs_creates_portable_defaults(tmp_path):
    settings = Settings(
        db_path=tmp_path / "state" / "codegraph.db",
        repo_root=tmp_path / "state" / "repos",
    )
    ensure_runtime_dirs(settings)

    assert settings.db_path.parent.is_dir()
    assert settings.repo_root.is_dir()


def test_repo_list_empty(tmp_path, monkeypatch):
    from codegraph_mcp import config
    monkeypatch.setattr(config.settings, "db_path", tmp_path / "cg.db")
    monkeypatch.setattr(config.settings, "repo_root", tmp_path / "repos")
    result = runner.invoke(app, ["repo", "list"])
    assert result.exit_code == 0


def test_index_unknown_repo(tmp_path, monkeypatch):
    from codegraph_mcp import config
    monkeypatch.setattr(config.settings, "db_path", tmp_path / "cg.db")
    monkeypatch.setattr(config.settings, "repo_root", tmp_path / "repos")
    result = runner.invoke(app, ["index", "does_not_exist"])
    assert result.exit_code != 0


def test_prepare_change_unknown_repo(tmp_path, monkeypatch):
    from codegraph_mcp import config
    monkeypatch.setattr(config.settings, "db_path", tmp_path / "cg.db")
    monkeypatch.setattr(config.settings, "repo_root", tmp_path / "repos")
    result = runner.invoke(app, ["prepare-change", "no_repo", "fix the thing"])
    assert result.exit_code != 0


def test_repo_add_and_list(tmp_path, monkeypatch):
    from codegraph_mcp import config
    repo_root = tmp_path / "repos"
    repo_root.mkdir()
    sample = repo_root / "myrepo"
    sample.mkdir()
    (sample / "main.py").write_text("def main():\n    pass\n")
    monkeypatch.setattr(config.settings, "db_path", tmp_path / "cg.db")
    monkeypatch.setattr(config.settings, "repo_root", repo_root)
    result = runner.invoke(app, ["repo", "add", "myrepo", str(sample)])
    assert result.exit_code == 0
    result2 = runner.invoke(app, ["repo", "list", "--json"])
    assert result2.exit_code == 0
    data = json.loads(result2.output)
    assert any(r["name"] == "myrepo" for r in data)
