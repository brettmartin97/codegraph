# CodeGraph MCP — Setup for Claude Code / Claude Desktop

## 1. Install

```bash
pip install -e ".[mcp]"
```

Or from PyPI:
```bash
pip install "codegraph-mcp[full]"
```

## 2. Add to Claude Code (claude_desktop_config.json)

```json
{
  "mcpServers": {
    "codegraph": {
      "command": "python3",
      "args": ["-m", "codegraph_mcp.server.mcp_server"]
    }
  }
}
```

Or auto-configure all detected AI tools:
```bash
codegraph install-mcp
```

## 3. Index your project (first use)

Once the MCP is connected, call this tool once:

```
register_and_index(
  path="/path/to/your/project",
  repo_name="my-project"
)
```

After that, the graph persists in `~/.codegraph/codegraph.db` — no need to re-index unless files change.

## 4. Typical LLM workflow

1. `list_repos()` — see what's indexed
2. `repo_overview("my-project")` — orient yourself (~2k tokens)
3. `get_file_tree("my-project")` — browse structure
4. `find_function("my-project", "yourFunction")` — locate code
5. `prepare_change("my-project", "your task in plain English")` — get full context before editing
6. `get_function_impact("my-project", "functionName")` — blast-radius before touching anything
7. `get_function_source("my-project", "functionName")` — read source without opening the file
8. `index_repo("my-project")` — refresh after edits

## Data location

- DB: `~/.codegraph/codegraph.db`
- Override: set env var `CODEGRAPH_DB_PATH=/your/path/codegraph.db`
