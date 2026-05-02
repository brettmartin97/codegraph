# Contributing to CodeGraph MCP

Thank you for your interest in contributing!

## Development Setup

```bash
git clone https://github.com/brettmartin97/code-graph-mcp
cd code-graph-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

This installs all optional dependencies including tree-sitter analyzers for
Python, JS/TS, Go, Java, Rust, and C#.

## Running Tests

```bash
# Full suite (excludes slow e2e tests)
pytest tests/ --ignore=tests/test_e2e_large_repo.py

# Single test file
pytest tests/test_treesitter_analyzers.py -v

# With coverage
pytest tests/ --ignore=tests/test_e2e_large_repo.py --cov=src/codegraph_mcp --cov-report=term-missing
```

All 92+ tests must pass before submitting a PR.

## Code Style

```bash
ruff check src/ tests/      # lint
ruff format src/ tests/     # format
mypy src/                   # type check
```

CI enforces ruff and mypy on every PR.

## Project Structure

```
src/codegraph_mcp/
  analyzers/       # Language-specific AST parsers
    treesitter.py  # JS/TS/Go/Java/Rust/C# via tree-sitter
    generic.py     # Python AST + regex fallback
    registry.py    # Analyzer dispatch
  cli/             # Typer CLI (codegraph command)
  graph/           # SQLite store + models
  indexing/        # Repo walker + incremental indexer
  query/           # Impact engine, context engine, git history
  server/          # MCP server (FastMCP) + REST API
  policy/          # Boundary checker
  memory/          # Failure event ingestion
  security/        # Path jail
```

## Adding a New Language Analyzer

1. Install the tree-sitter grammar: `pip install tree-sitter-<lang>`
2. Add a class in `src/codegraph_mcp/analyzers/treesitter.py` extending
   `LanguageAnalyzer`. Implement `supports()` and `analyze()`.
3. Register it in `AnalyzerRegistry.__init__()` in `registry.py`.
4. Add the package to `pyproject.toml` under `[project.optional-dependencies]`.
5. Update `active_languages` in the registry.
6. Add tests in `tests/test_treesitter_analyzers.py`.

Look at `TreeSitterGoAnalyzer` as a reference implementation.

## Adding an MCP Tool

Add a `@mcp.tool()` decorated function inside the `create_server()` function
in `src/codegraph_mcp/server/mcp_server.py`. Follow the existing docstring
format — the docstring is what AI agents see, so write it from the agent's
perspective: *when to use this tool*, not just *what it does*.

## Pull Request Guidelines

- Keep PRs focused — one feature or fix per PR
- Add tests for new functionality
- Update CHANGELOG.md with a brief description under `[Unreleased]`
- Run the full test suite before submitting
- Sign your commits (`git commit -s`)

## Releasing

1. Bump version in `pyproject.toml`
2. Update `CHANGELOG.md` — move `[Unreleased]` to the new version
3. Tag: `git tag v0.x.y && git push --tags`
4. CI publishes to PyPI automatically on tag push
