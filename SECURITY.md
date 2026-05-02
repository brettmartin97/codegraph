# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.3.x   | Yes       |
| < 0.3   | No        |

## Security Model

### Path Jail

All file operations are constrained to the configured `repo_root` directory
(default: `~/.codegraph/repos`). The `ensure_within()` guard in
`src/codegraph_mcp/security/path_jail.py` resolves symlinks and raises
`PermissionError` for any path that escapes the root.

Set `CODEGRAPH_ALLOW_EXTERNAL_REPOS=true` to allow absolute paths outside
`repo_root` — only do this in trusted environments (e.g. CI runners).

### Secret File Exclusion

The indexer never reads files that match any of the following patterns,
regardless of `repo_root` settings:

- Exact names: `.env`, `.env.*`, `credentials.json`, `service-account.json`,
  `id_rsa`, `id_ed25519`, `.netrc`, `.pgpass`, `.my.cnf`, `.boto`
- Suffixes: `.pem`, `.key`, `.p12`, `.pfx`, `.jks`, `.keystore`, `.cer`, `.crt`, `.der`
- Prefixes: `secret`, `credential`, `password`, `token`, `apikey`, `api_key`

These files are silently skipped during `iter_files()` and will never appear
in the graph or be returned by any MCP tool.

### MCP Server

The MCP server exposes no authentication by default and is intended to run
locally (stdio transport). Do not expose it on a network interface without
adding authentication middleware.

### Input Validation

All `repo` name arguments are looked up against the registered repository table
before use — unknown names return an error rather than allowing path traversal.

### No Network Calls

CodeGraph makes no outbound network calls during indexing or graph queries.
The optional LLM enrichment pipeline makes calls only to the configured
Anthropic or OpenAI API endpoint when explicitly invoked with `enrich_repo`.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Email: codegraph-security@proton.me

Alternatively, use [GitHub Security Advisories](https://docs.github.com/en/code-security/security-advisories) to report privately.

Include:
- A description of the vulnerability and its impact
- Steps to reproduce
- Affected versions

We aim to acknowledge reports within 48 hours and release a patch within 14 days
for confirmed vulnerabilities.
