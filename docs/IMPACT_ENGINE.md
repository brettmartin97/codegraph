# Impact Engine

The impact engine answers:

> If we touch this function, what can break and how do we prove safety?

## Inputs

- repo
- function query
- depth
- token budget

## Outputs

- target function
- direct callers
- direct callees
- unresolved dynamic callees
- related tests
- runtime bindings
- risk score
- confidence
- risk reasons
- recommended validation
- context pack

## Risk scoring v1

Risk is a weighted heuristic based on:

- caller count
- callee count
- unresolved dynamic calls
- runtime reachability hints
- missing tests
- low-quality descriptor
- complexity

## Future upgrades

- transitive impact expansion
- type/contract impact
- data resource impact
- API route impact
- CI failure history
- graph snapshot diffs
