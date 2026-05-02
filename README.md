# CodeGraph MCP

**Know what breaks before you touch it.**

CodeGraph builds a function call graph from your codebase and exposes it as an MCP server — so AI agents like Claude can answer *"what will break if I change this function?"* with real data instead of guessing.

```
$ git diff origin/main...HEAD | codegraph ci pr-comment myrepo

## 🔍 CodeGraph — Call-site Blast Radius

_3 changed function(s) · 47 call-sites reached · 12 related tests_

| Function              | File          | Callers | Tests | Risk         |
|-----------------------|---------------|---------|-------|--------------|
| `process_order`       | `orders.py`   | 12+31   | 8     | 🔴 0.81      |
| `validate_address`    | `checkout.py` | 3+2     | 4     | 🟡 0.44      |
| `format_receipt`      | `receipts.py` | 1+0     | 0     | 🟢 0.12      |
```

## Why this exists

When an AI agent edits code it has no idea how connected that code is. A "small fix" to `process_order` that's called from 43 places is not a small fix. CodeGraph gives agents — and developers — the blast radius before the edit, not after the breakage.

## Features

- **8 languages** with real AST parsing: Python, JavaScript, TypeScript, TSX, Go, Java, Rust, C#
- **Infra/config add-on**: Dockerfile, Compose, Kubernetes manifests, Helm charts/templates, YAML, and JSON
- **MCP server** with 20+ tools for Claude, Cursor, and any MCP-compatible agent
- **PR comments**: `git diff | codegraph ci pr-comment` posts blast-radius tables to GitHub
- **Watch mode**: file changes re-index in milliseconds
- **VS Code extension**: caller count + risk score on hover, inline decorations
- **Risk scoring**: 0–1 score tells agents exactly how dangerous a change is
- **Incremental indexing**: only re-parses changed files

## Quick Start (5 minutes)

### 1. Install

```bash
pip install "codegraph-mcp[full]"
```

### 2. Wire into your AI tool

```bash
codegraph install-mcp
```

This detects Claude Desktop, Cursor, and Zed on your machine and writes the config automatically. Restart your AI tool after.

**Manual config** — add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "codegraph-mcp": {
      "command": "python3",
      "args": ["-m", "codegraph_mcp.server.mcp_server"]
    }
  }
}
```

### 3. Index your project

```bash
cd /path/to/your/project
codegraph init .
```

That's it. Claude can now answer questions about your codebase's call graph.

### 4. Ask Claude

> "Before you change `process_payment`, show me what calls it and what tests cover it."

Claude will use `get_function_impact` automatically. You'll see callers, tests, a risk score, and a recommended validation recipe — all from the live graph.

## CLI Reference

```bash
# Setup
codegraph init [path]              # register + index a repo
codegraph install-mcp              # auto-configure Claude/Cursor/Zed
codegraph doctor                   # health check

# Indexing
codegraph index <repo>             # full reindex
codegraph index <repo> --mode incremental  # fast: skip unchanged files
codegraph watch <repo>             # live watch mode

# Querying
codegraph find-function <repo> <query>
codegraph impact <repo> <function>
codegraph overview <repo>

# CI/CD
git diff origin/main...HEAD | codegraph ci pr-comment <repo>
git diff origin/main...HEAD | codegraph ci changed-functions <repo>
codegraph ci risk-gate <repo> --function <fn> --threshold 0.65
codegraph ci validate-boundaries <repo> --policy boundaries.yaml
```

## GitHub Actions

Drop this into `.github/workflows/codegraph-pr.yml` and every PR gets a blast-radius comment:

```yaml
- name: Generate impact comment
  run: |
    codegraph repo add ${{ github.event.repository.name }} ${{ github.workspace }} || true
    codegraph index ${{ github.event.repository.name }} --mode incremental
    git diff origin/${{ github.base_ref }}...HEAD \
      | codegraph ci pr-comment ${{ github.event.repository.name }} \
      > /tmp/comment.md
```

Full workflow templates are in [`.github/workflows/`](.github/workflows/).

## MCP Tools

The MCP server exposes these tools to AI agents:

| Tool | When agents use it |
|------|--------------------|
| `register_and_index` | First call on a new codebase |
| `graph_status` | Check freshness before queries |
| `get_function_impact` | **Before editing any function** — blast radius + risk score |
| `prepare_change` | Before a multi-function refactor |
| `find_function` | Locate a function by name or keyword |
| `semantic_search` | Find functions by what they do (requires enrichment) |
| `get_callers` / `get_callees` | Direct call relationships |
| `get_function_source` | Read function body + context |
| `list_functions_in_file` | All functions in a file with line ranges |
| `get_high_complexity_functions` | Hotspots for refactoring |
| `repo_overview` | Codebase summary at session start |
| `validate_boundaries` | Check architectural boundary violations |

## VS Code Extension

Install from the marketplace (search "CodeGraph MCP") or build from source:

```bash
cd vscode-extension
npm install && npm run compile
# Press F5 in VS Code to launch the extension host
```

Once installed, hover over any function definition to see:

```
CodeGraph — process_order
─────────────────────────
Callers     12
Tests       8
Risk score  🔴 0.81 (high)

Top callers: checkout.place_order, api.order_endpoint +10 more

[Show full blast radius]
```

## Configuration

Set via environment variables or a `.env` file:

```bash
CODEGRAPH_REPO_ROOT=~/.codegraph/repos      # where repos are stored
CODEGRAPH_DB_PATH=~/.codegraph/codegraph.db # SQLite database path
CODEGRAPH_ALLOW_EXTERNAL_REPOS=false        # allow paths outside repo_root
CODEGRAPH_MAX_FILE_BYTES=1000000            # skip files larger than this
```

## Language Support

| Language   | Analyzer       | Confidence | Install              |
|------------|----------------|------------|----------------------|
| Python     | Built-in AST   | 0.95       | always available     |
| JavaScript | tree-sitter    | 0.92       | `pip install "codegraph-mcp[treesitter]"` |
| TypeScript | tree-sitter    | 0.92       | same                 |
| Go         | tree-sitter    | 0.94       | same                 |
| Java       | tree-sitter    | 0.93       | `pip install "codegraph-mcp[treesitter-jvm]"` |
| Rust       | tree-sitter    | 0.94       | same                 |
| C#         | tree-sitter    | 0.93       | same                 |
| Dockerfile | Config/runtime | 0.90       | always available     |
| Compose    | Config/runtime | 0.95       | always available     |
| Kubernetes | Config/runtime | 0.85       | always available     |
| Helm       | Config/runtime | 0.70       | always available     |
| YAML       | Config/runtime | 0.80       | always available     |
| JSON       | Config/runtime | 0.80       | always available     |
| Others     | Regex fallback | 0.45       | always available     |

Config files are indexed automatically. No plugin setup is required: `codegraph init .`
detects common infra paths such as `Dockerfile`, `docker-compose.yml`, `k8s/*.yaml`,
`charts/*/Chart.yaml`, Helm `templates/*.yaml`, `*.yaml.gotmpl`, YAML, and JSON.
Dockerfiles expose build stages, `ENV` definitions, and shell/builder instructions that consume
those variables. `package.json` exposes the package, npm scripts, and dependency declarations as
separate graph nodes so hover/impact views point at useful config relationships instead of one
generic JSON blob.

## Architecture

```
Your codebase
    │
    ▼
RepoWalker ──► LanguageAnalyzer (Python/JS/TS/Go/Java/Rust/C#)
                      │
                      ▼
              FunctionNode + FunctionEdge
                      │
                      ▼
              SQLiteStore (function_nodes, function_edges, FTS5)
                      │
              ┌───────┴────────┐
              ▼                ▼
         MCP Server        REST API
    (Claude/Cursor/Zed)   (port 8000)
              │
              ▼
    ImpactEngine.function_impact()
    → callers, tests, risk_score, validation_recipe
```

## Benchmarks

Measured on Windows 11 (full mode, tree-sitter enabled). Run `python scripts/benchmark.py --full` for your hardware.

| Repo              | Files | Functions | Edges  | Full index | Incremental |
|-------------------|-------|-----------|--------|------------|-------------|
| requests (Python) | 43    | 612       | 1,961  | 1.4s       | ~200ms      |
| flask (Python)    | 89    | 866       | 2,835  | 2.9s       | ~400ms      |
| gin (Go)          | 110   | 1,314     | 8,488  | 3.9s       | ~450ms      |
| fastapi (Python)  | 2,703 | 4,381     | 11,720 | 42.5s      | ~3.9s       |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Key things:

```bash
pip install -e ".[dev]"
pytest tests/ --ignore=tests/test_e2e_large_repo.py   # must pass
ruff check src/ tests/
```

## License

MIT — see [LICENSE](LICENSE).
