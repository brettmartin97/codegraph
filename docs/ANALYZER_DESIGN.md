# Analyzer Design

## Goal

Support all major languages using a common function graph model.

## Capability levels

- Level 0: file inventory
- Level 1: function extraction
- Level 2: descriptors and signatures
- Level 3: local call extraction
- Level 4: cross-file call resolution
- Level 5: framework/runtime bindings
- Level 6: type-aware impact analysis

V1 focuses on levels 1-3 generally, with Python stronger than the rest.

## V1 analyzers

- Python AST analyzer
- Regex-based major-language analyzer
- Runtime analyzer for Dockerfile/YAML/Compose/Kubernetes/GitHub Actions

## V2 analyzer direction

Replace or augment regex extraction with tree-sitter query packs:

```text
queries/python/functions.scm
queries/typescript/functions.scm
queries/go/functions.scm
queries/java/functions.scm
...
```

Then add optional language-server enrichers:

- Pyright/Jedi
- tsserver
- gopls
- JDT/JavaParser
- Roslyn
- rust-analyzer
- clangd
