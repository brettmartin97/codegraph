# Security Model

CodeGraph MCP should be treated as a privileged context surface.

## Current controls

- Repositories are path-jailed under `CODEGRAPH_REPO_ROOT`.
- Docker Compose mounts repos read-only by default.
- MCP tools do not expose arbitrary shell execution.
- Source slices are secret-redacted.
- Docker logs are disabled by default.
- The app has a maximum indexed file size.

## Required production controls

- Per-repo allowlist
- AuthN/AuthZ for HTTP transport
- Audit log for all MCP calls
- Optional mTLS for internal agent traffic
- Docker socket disabled unless explicitly needed
- Container allowlist for logs
- Deny indexing `.env`, private keys, secrets by default
- Output redaction tests
