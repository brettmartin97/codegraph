# CodeGraph for VS Code

Hover any function in your editor. CodeGraph answers the questions reviewers
actually ask:

> **`PlanExecutor.execute_plan`**
>
> 📞 **12 callers** · 🧪 **3 tests** cover this · 🕒 last changed **2 days ago**
>
> _category: orchestration · complexity: 9 · async_

The full call graph, related tests, last-commit info, and risk score show up
in the **CodeGraph** sidebar — one click to jump to any caller or test.

## How It Works

The extension talks to the local CodeGraph REST API (default `http://127.0.0.1:8811`).
By default it starts that API, registers the open workspace, and indexes it
automatically when VS Code starts.

Prerequisite:

```bash
pip install "codegraph-mcp[full]"
```

Then open your repo in VS Code. The status bar shows CodeGraph startup/indexing
state, and the **CodeGraph** sidebar shows the current function impact.

Manual fallback:

```bash
python -m uvicorn codegraph_mcp.server.rest_api:api --port 8811
codegraph repo add myrepo /path/to/your/repo
codegraph index myrepo
```

The extension auto-detects the workspace folder name as the repo name. Override
it with `codegraph.repo` if you registered it under a different name.

## Settings

| Setting | Default | Description |
|---|---|---|
| `codegraph.endpoint` | `http://127.0.0.1:8811` | Base URL of the CodeGraph REST API. |
| `codegraph.repo` | `""` | Repo name registered with CodeGraph. Falls back to workspace folder name. |
| `codegraph.autoStartServer` | `true` | Start the local CodeGraph REST API automatically. |
| `codegraph.autoIndexOnStart` | `true` | Register + index the workspace when CodeGraph starts. |
| `codegraph.hover.enabled` | `true` | Toggle the hover card. |

## Commands

- **CodeGraph: Index workspace repo** — register and run a full index.
- **CodeGraph: Show impact for function at cursor** — populate the sidebar from the cursor.
- **CodeGraph: Refresh** — re-check the API and refresh status.

## Build from source

```bash
cd vscode-extension
npm install
npm run compile
# F5 in VS Code to launch an Extension Development Host.
```

## License

Same as the parent CodeGraph MCP project.
