# Function Graph Schema

## FunctionNode

A canonical callable unit across languages.

Important fields:

- `language`
- `kind`
- `name`
- `qualified_name`
- `signature`
- `return_type`
- `parameters_json`
- `descriptor`
- `body_hash`
- `signature_hash`
- `descriptor_hash`
- `complexity`
- `is_test`
- `confidence`

## FunctionDescriptor

Descriptor provenance is mandatory.

Sources:

- `docstring`
- `python_comment`
- `javascript_comment`
- `typescript_comment`
- `java_comment`
- `inferred_static`
- `llm_generated` future only
- `none`

Generated descriptors must never be treated as source-of-truth.

## FunctionEdge

Edges are confidence-scored.

V1 supports:

- `CALLS`
- future-ready values for tests, runtime, data, config, routes, queues, APIs

Unresolved dynamic edges keep `target_function_id = null` and preserve `target_symbol_name`.

## RuntimeBinding

Represents Docker, Compose, Kubernetes, GitHub Actions, Argo, and other runtime/config entrypoints.
