# Changelog

All notable changes to CodeGraph MCP are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

## [0.3.0] - 2024-01-01

### Added

**Language support**
- Tree-sitter analyzers for JavaScript, TypeScript, TSX, and Go (0.92-0.94 confidence vs 0.45 for regex)
- Tree-sitter analyzers for Java, Rust, and C# (0.93-0.94 confidence)
- All analyzers are optional extras; regex fallback activates when not installed

**Indexing**
- Incremental indexing: `mode="incremental"` skips unchanged files using content hashes
- `codegraph watch <repo>` — live file-watch mode via watchfiles
- Deletion handling: files removed from disk are purged from the graph automatically
- Secret/credential file exclusion (`.env`, `.pem`, `id_rsa`, etc. never indexed)

**CLI**
- `codegraph init [path]` — one-command register + index + optional watch
- `codegraph doctor` — health check for DB, analyzers, MCP server, watchfiles
- `codegraph install-mcp` — auto-configure Claude Desktop, Cursor, and Zed
- `codegraph watch <repo>` — incremental watch mode with add/modify/delete handling

**CI/CD**
- `codegraph ci changed-functions` — parse unified diff, return affected functions as JSON
- `codegraph ci pr-comment` — full blast-radius markdown comment from a git diff
- `codegraph ci risk-gate` — fail CI if risk score exceeds threshold
- GitHub Actions workflow templates: `codegraph-pr.yml` and `codegraph-nightly.yml`

**MCP server**
- `graph_status` tool — check index freshness before running queries
- Freshness warnings injected into `get_function_impact` responses when graph is stale
- All tool docstrings rewritten for agent-native use (when to call, what results mean)

**VS Code extension**
- Hover provider: caller count, test coverage, risk score on function definition hover
- Inline decorations: `🟢/🟡/🔴 N callers` next to every function definition
- Blast-radius panel: full impact report in a webview
- Commands: `Show Blast Radius`, `Refresh Decorations`, `Run Doctor`

**Graph correctness**
- `SQLiteStore.delete_file()` — cascading delete of functions, edges, and descriptors
- `SQLiteStore.function_at(repo_id, path, line)` — innermost function at a line
- `SQLiteStore.functions_at_lines(repo_id, path, lines)` — functions overlapping changed lines
- `SQLiteStore.graph_freshness(repo_id)` — staleness hours + human-readable warning

### Changed
- `pyproject.toml` extras reorganised: `treesitter`, `treesitter-jvm`, `watch`, `full`
- `codegraph doctor` exits 1 when optional deps are missing (was 0)
- `codegraph index` now prints files skipped in incremental mode

### Fixed
- `stable_id` shadowed as local variable in `index_repo` causing `UnboundLocalError`
- `sqlite_store.py` truncation: recovered `_function_from_row`, `transitive_callers`,
  `snapshot_repo`, `diff_snapshots`, and related methods from compiled bytecode
- Null bytes injected by Windows→Linux mount path writes stripped across all source files

## [0.2.0] - 2023-11-01

### Added
- MCP server with 20+ tools via FastMCP
- REST API server via FastAPI
- LLM enrichment pipeline (Anthropic and OpenAI backends)
- Boundary policy checker (`boundaries.yaml`)
- Failure event ingestion and history

## [0.1.0] - 2023-09-01

### Added
- Initial release: Python AST indexer, SQLite graph store, basic CLI
