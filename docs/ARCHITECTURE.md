# Architecture

CodeGraph MCP is a function-first code intelligence substrate.

## Core pipeline

```text
Repo -> Files -> Language analyzers -> Functions -> Descriptors -> Edges -> Runtime bindings -> Impact engine -> Context packs -> MCP/REST/CLI
```

## Subsystems

- **Indexer**: walks repo, detects language, invokes analyzers, persists graph.
- **Analyzer Registry**: dispatches files to Python, regex-major-language, and runtime analyzers.
- **SQLite Store**: durable local graph store.
- **Impact Engine**: computes callers, callees, unresolved dynamic calls, test proximity, runtime relevance, risk, and validation.
- **Context Engine**: produces repo overview and `prepare_change` responses.
- **MCP Server**: exposes agent-native tools.
- **REST API**: exposes service endpoints for non-MCP callers.
- **CLI**: gives repeatable scripts for agents and CI.

## Design target

The primary unit is a function-like callable:

- function
- method
- constructor
- lambda/closure where extractable
- endpoint handler
- job/event handler
- test function

The app is not intended to be a compiler-perfect static analyzer in v1. It is designed to be useful, honest, confidence-scored, and extensible.
