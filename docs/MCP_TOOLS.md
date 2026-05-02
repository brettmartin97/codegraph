# MCP Tool Contracts

## repo_overview

Returns language mix, function count, descriptor coverage, runtime bindings, high-complexity functions, and risk flags.

## index_repo

Refreshes a registered repo.

Inputs:

```json
{"repo": "myrepo", "mode": "full"}
```

## find_function

Finds functions/methods/procedures by fuzzy name/signature.

```json
{"repo": "myrepo", "query": "execute_plan", "limit": 10}
```

## get_function_impact

Returns blast radius for a function.

```json
{
  "repo": "myrepo",
  "function": "PlanExecutor.execute_plan",
  "depth": 2,
  "max_tokens": 12000
}
```

## prepare_change

North-star tool. Agents should call this before editing.

```json
{
  "repo": "myrepo",
  "task": "fix retry failure disposition handling",
  "max_tokens": 12000
}
```

Returns:

- target functions
- impact report
- context pack
- safe edit boundary
- contracts to preserve
- validation recipe
- confidence
