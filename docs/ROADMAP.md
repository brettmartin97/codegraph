# CodeGraph MCP Roadmap

## Goal: turn v1 scaffold into an agentic software engineering intelligence layer

## North star

Build a Dockerized Python platform that creates a persistent, versioned, evidence-backed **function-impact graph** across major programming languages and exposes it through MCP, REST, and CLI so agents can safely understand, modify, validate, and reason about large codebases.

The end state is not "code search." It is:

```text
Repo → Function Graph → Runtime Graph → Test Graph → Data Graph → Impact Engine
     → Context Packs → Safe Edit Boundaries → Validation Recipes → CI Feedback
```

---

# 0. Operating rules for future agents

Every future agent working on this repo should follow these constraints.

## Non-negotiables

```text
1. Keep the system read-only by default.
2. Do not add arbitrary shell execution through MCP.
3. Preserve evidence and confidence scores for graph claims.
4. Prefer deterministic static analysis over LLM-generated claims.
5. LLM enrichment must be clearly marked as generated/non-authoritative.
6. Every new analyzer must degrade gracefully.
7. Every MCP response must be bounded by token/context budgets.
8. Every feature should be usable by CLI, REST, and MCP where practical.
9. Never silently drop uncertainty; expose unresolved/low-confidence edges.
10. Keep the app useful without external services.
```

## Core design doctrine

```text
Static graph is the source of truth.
LLM summaries are annotations.
Runtime logs are evidence.
CI/test results are feedback.
MCP tools are narrow, safe views over the intelligence layer.
```

---

# 1. Phase 1 — Stabilize v1 foundation

## Objective

Make the current scaffold reliable, installable, testable, and pleasant to run locally.

## Deliverables

```text
1. Ensure package installs cleanly with pip/uv.
2. Validate Docker build.
3. Validate Docker Compose local run.
4. Ensure CLI entrypoints work.
5. Ensure REST server starts cleanly.
6. Ensure MCP server starts cleanly.
7. Add smoke tests.
8. Add example repo indexing test.
9. Add database migration/bootstrap sanity checks.
10. Add deterministic output for basic indexing.
```

## Agent tasks

### 1.1 Repo hygiene

```text
- Review project layout.
- Remove dead imports.
- Fix packaging metadata.
- Ensure pyproject.toml has correct dependencies.
- Add ruff/mypy/pytest config.
- Add pre-commit config if useful.
```

### 1.2 Docker validation

```text
- Build image.
- Run container with mounted example repo.
- Run CLI inside container.
- Start REST API.
- Start MCP server.
- Document commands.
```

### 1.3 Basic test suite

Minimum tests:

```text
tests/test_cli_smoke.py
tests/test_index_python.py
tests/test_store_sqlite.py
tests/test_prepare_change.py
tests/test_redaction.py
tests/test_runtime_parser.py
```

## Acceptance criteria

```text
make test passes
docker compose up works
codegraph index examples/sample_repo works
codegraph prepare-change examples/sample_repo "modify foo" returns structured JSON
REST /healthz returns healthy
MCP server exposes expected tool list
```

---

# 2. Phase 2 — Make the function graph real

## Objective

Move from basic symbol extraction to durable, queryable, function-level intelligence.

## Deliverables

```text
1. Stable function IDs.
2. Function snapshots.
3. Signature/body/descriptor hashes.
4. Function parameters.
5. Function descriptors.
6. Function edges with evidence.
7. Function lookup.
8. Function context retrieval.
```

## Required graph entities

```text
Repository
File
Function
Parameter
Descriptor
Type
Import
FunctionEdge
RuntimeBinding
TestBinding
DataResourceBinding
IndexRun
FunctionSnapshot
```

## Function identity rules

Use stable IDs based on:

```text
repo_id
relative path
qualified name
kind
normalized signature
```

Fallback when qualified names are unavailable:

```text
repo_id
relative path
line range
body hash
```

## Hashes to store

```text
body_hash
signature_hash
descriptor_hash
dependency_hash
call_edges_hash
```

## Agent tasks

### 2.1 Function ID hardening

```text
- Implement stable function ID generation.
- Add regression tests proving IDs survive line movement when possible.
- Add fallback ID behavior for anonymous/lambda functions.
```

### 2.2 Function snapshots

```text
- Store function state per index run.
- Compare latest vs previous snapshot.
- Detect changed body/signature/descriptor/calls.
```

### 2.3 Function query API

Implement:

```text
find_function
get_function
get_function_descriptor
get_function_context
get_function_callers
get_function_callees
```

## Acceptance criteria

```text
Given a repo, app can list all functions with stable IDs.
Given a function name, app returns exact and fuzzy matches.
Given a function, app returns descriptor, signature, source slice, callers, callees.
Given a re-index, app can identify changed functions.
```

---

# 3. Phase 3 — Analyzer engine upgrade

## Objective

Create scalable multi-language support using tree-sitter query packs plus language-specific resolver plugins.

## Language capability levels

```text
Level 0: file inventory
Level 1: function extraction
Level 2: descriptors + signatures
Level 3: imports + local calls
Level 4: cross-file call resolution
Level 5: framework/runtime bindings
Level 6: type-aware impact analysis
```

## Initial target matrix

```text
Python       Level 5
JavaScript   Level 4
TypeScript   Level 5
Go           Level 5
Java         Level 4
YAML         runtime/config graph
Dockerfile   runtime/config graph
Shell        Level 2/3
```

## Expansion matrix

```text
C#           Level 4, later Level 6 with Roslyn
Rust         Level 4, later Level 6 with rust-analyzer
C/C++        Level 3, later clangd enrichment
Ruby         Level 3/4
PHP          Level 3/4
Kotlin       Level 3/4
Scala        Level 3
Terraform    config/resource graph
SQL          data-resource graph
```

## Agent tasks

### 3.1 Tree-sitter query packs

Create query files:

```text
analyzers/queries/python/functions.scm
analyzers/queries/python/calls.scm
analyzers/queries/python/imports.scm
analyzers/queries/python/descriptors.scm
analyzers/queries/typescript/functions.scm
analyzers/queries/typescript/calls.scm
...
```

Each language should support:

```text
functions
methods
constructors
lambdas/closures where feasible
imports
calls
comments/descriptors
classes/types
tests
framework bindings where possible
```

### 3.2 Generic query engine

```text
- Load tree-sitter grammar.
- Load language query pack.
- Extract captures.
- Normalize into app models.
- Preserve evidence: file, line, capture type, raw text.
```

### 3.3 Language resolver plugins

Each plugin handles:

```text
qualified names
module/package naming
import resolution
method receiver resolution
framework-specific route/task/test detection
descriptor conventions
```

## Acceptance criteria

```text
Each Tier 1 language can extract functions and descriptors.
Python, TS, Go can extract basic local calls.
Python FastAPI/Flask/Typer/Celery bindings detected.
TS Express/Nest/Fastify basics detected.
Go net/http/gin/chi basics detected.
```

---

# 4. Phase 4 — Descriptor intelligence

## Objective

Turn function comments/docstrings into structured behavioral descriptors, and infer missing descriptors.

## Descriptor sources

```text
docstring
JSDoc
JavaDoc
C# XML comments
Go preceding comments
Rust doc comments
Doxygen
YARD
PHPDoc
KDoc
ScalaDoc
shell preceding comments
OpenAPI descriptions
GraphQL schema descriptions
inferred_static
llm_generated
```

## Descriptor fields

```text
summary
params
returns
raises/errors
side_effects
state_mutations
external_calls
idempotency
security_sensitivity
examples
quality_score
source
evidence
```

## Agent tasks

### 4.1 Structured parser

Implement descriptor parsers for:

```text
Python docstrings
JSDoc/TSDoc
Go comments
JavaDoc
C# XML comments
Rust doc comments
PHPDoc
Doxygen-style comments
```

### 4.2 Descriptor quality scoring

Score:

```text
1.00 complete structured human descriptor matching signature
0.80 partial structured descriptor
0.60 unstructured human summary
0.45 inferred static descriptor
0.30 name-only inference
0.00 missing
```

### 4.3 Descriptor drift detection

Detect:

```text
signature param not documented
documented param missing
return mismatch
stale descriptor hash after body change
missing errors/raises
missing side-effect hints
```

### 4.4 Static inference

Infer from:

```text
function name
parameters
return type
return statements
called functions
decorators/annotations
enclosing class/module
database/write calls
publish/send/emit calls
```

## Acceptance criteria

```text
App reports descriptor coverage.
App flags stale/missing descriptors.
App provides inferred descriptors when human docs are missing.
Every descriptor includes source and confidence/quality.
```

---

# 5. Phase 5 — Call graph and dependency resolution

## Objective

Build confidence-scored call relationships that are honest, useful, and extensible.

## Edge confidence model

```text
0.98 exact local function resolution
0.90 imported symbol resolved
0.80 class method resolved through type/member context
0.70 framework binding resolved
0.55 name match in same module/package
0.40 callback/dynamic dispatch guessed
0.25 reflection/string-based possible edge
```

## Required edge types

```text
CALLS
CALLED_BY
REFERENCES
READS_FIELD
WRITES_FIELD
INSTANTIATES
THROWS
CATCHES
RETURNS_TYPE
ACCEPTS_TYPE
DECORATED_BY
ANNOTATED_WITH
IMPLEMENTS
OVERRIDES
TESTED_BY
TESTS
ROUTE_TO
JOB_TO
EVENT_TO
COMMAND_TO
CONFIGURED_BY
USES_ENV
USES_SECRET_REF
USES_FEATURE_FLAG
USES_DATABASE_TABLE
USES_QUEUE
USES_TOPIC
USES_EXTERNAL_API
SERIALIZES
DESERIALIZES
```

## Agent tasks

### 5.1 Local call resolution

```text
- Resolve calls within same function.
- Resolve calls within same file.
- Resolve class method calls where obvious.
- Capture unresolved calls with target_symbol_name.
```

### 5.2 Import-aware call resolution

```text
- Build import map per file.
- Resolve imported function/class/module calls.
- Add confidence based on resolution precision.
```

### 5.3 Dynamic-call handling

Do not fake certainty.

```text
- Store unresolved dynamic edges.
- Keep target_function_id null.
- Preserve target_symbol_name.
- Attach evidence explaining uncertainty.
```

### 5.4 Transitive graph traversal

Implement bounded traversal:

```text
direct callers
direct callees
transitive callers depth N
transitive callees depth N
shortest dependency paths
```

## Acceptance criteria

```text
Given function X, app returns direct callers/callees.
Given unresolved calls, app exposes them as unresolved with evidence.
Impact engine can traverse high/medium/low-confidence paths separately.
```

---

# 6. Phase 6 — Runtime graph

## Objective

Connect code to how it actually runs.

## Runtime entrypoint types

```text
HTTP route
CLI command
scheduled job
queue consumer
event handler
serverless function
test entrypoint
main function
container command
Kubernetes probe
workflow step
Argo workflow step
GitHub Actions step
Jenkins stage
```

## Agent tasks

### 6.1 Docker and Compose

Parse:

```text
Dockerfile
docker-compose.yml
compose.yaml
.env.example metadata only
entrypoint scripts
```

Extract:

```text
service
image
build context
command
entrypoint
ports
volumes
env vars names only by default
depends_on
healthcheck
```

### 6.2 Kubernetes

Parse:

```text
Deployment
StatefulSet
DaemonSet
Service
Ingress
ConfigMap metadata
Secret metadata only
Job
CronJob
ServiceAccount
Role/RoleBinding names
```

Extract:

```text
workload -> container
container -> image
container -> command
workload -> config refs
service -> selector
ingress -> service
cronjob -> command
```

### 6.3 Framework bindings

Map runtime entrypoints to functions:

```text
FastAPI route -> function
Flask route -> function
Django URL -> view
Typer/Click command -> function
Celery task -> function
Express route -> handler
NestJS controller -> method
Go HTTP handler -> function
Spring route -> method
ASP.NET route -> method
```

## Acceptance criteria

```text
Given a function, app can report whether it is externally reachable.
Given a route/job/service, app can report the function entrypoint.
Given a Docker/K8s service, app can identify likely source files.
```

---

# 7. Phase 7 — Test graph and validation recipes

## Objective

Make the app tell agents how to prove a change is safe.

## Test binding signals

```text
naming conventions
imports
call edges
fixtures
test class names
test function names
path proximity
coverage reports if available
framework metadata
```

## Agent tasks

### 7.1 Test detection

Support:

```text
pytest
unittest
Jest
Vitest
Go test
JUnit
NUnit/xUnit/MSTest
cargo test
PHPUnit
RSpec
```

### 7.2 Test-to-function binding

Create `TESTS` and `TESTED_BY` edges using:

```text
direct calls
imports
name similarity
path conventions
framework conventions
coverage files
```

### 7.3 Validation recipe engine

Given changed function or task, output:

```text
unit tests to run
integration tests to run
runtime log checks
lint/type checks
boundary checks
descriptor checks
manual validation notes where needed
```

## Acceptance criteria

```text
get_function_impact returns related tests.
prepare_change returns validation recipe.
PR diff mode recommends targeted tests.
Validation recipe includes reason/evidence for each command.
```

---

# 8. Phase 8 — Context pack engine

## Objective

Create the agent-native context unit: bounded, ranked, evidence-backed context packs.

## Context pack contents

```text
target function source
descriptor
direct callers
direct callees
related tests
runtime bindings
config dependencies
types/contracts
historical failures
risk notes
contracts to preserve
safe edit boundary
```

## Ranking signals

```text
symbol/name match
task keyword match
graph proximity
runtime reachability
test proximity
descriptor relevance
recent change history
historical failures
centrality
confidence score
semantic score optional
```

## Token budget behavior

The engine must:

```text
respect max_tokens
prioritize target/evidence/tests
deduplicate overlapping slices
return omitted relevant items
preserve line ranges
explain why each slice was included
```

## Agent tasks

### 8.1 Context pack builder

Implement:

```text
build_context_pack(task, target, max_tokens)
pack ranking
source slicing
deduplication
budget enforcement
omission reporting
```

### 8.2 Context levels

Support:

```text
L0 repo map
L1 subsystem map
L2 file summary
L3 function descriptor
L4 function source slice
L5 callers/callees/tests/config
L6 full impact pack
L7 architecture reasoning pack
```

## Acceptance criteria

```text
prepare_change returns useful context under budget.
Every context item includes file, lines, reason, score, evidence.
App reports omitted relevant items.
Agents can use context pack without scanning the repo manually.
```

---

# 9. Phase 9 — Impact engine

## Objective

Produce accurate blast-radius analysis when a function changes.

## Impact dimensions

```text
direct callers
transitive callers
direct callees
transitive callees
importers
types/contracts
tests
runtime routes/jobs/events
config/env dependencies
database/queue/API dependencies
package/module boundaries
historical failures
descriptor drift
```

## Change intent classes

```text
signature_change
return_contract_change
error_handling_change
side_effect_change
persistence_change
auth_security_change
runtime_config_change
test_only_change
refactor_only_change
unknown
```

## Risk scoring

```text
caller_count
transitive_reach
runtime_exposure
external_api_usage
db_write_usage
queue/event_usage
low_test_coverage
descriptor_missing_or_stale
high_complexity
dynamic_dispatch_uncertainty
security_sensitive_code
historical_failure_density
```

## Agent tasks

### 9.1 Direct impact

```text
Given function X:
- direct callers
- direct callees
- tests
- runtime bindings
- descriptor quality
- obvious contracts
```

### 9.2 Transitive impact

```text
Bound traversal by:
- depth
- confidence threshold
- edge type
- token budget
```

### 9.3 Risk report

Produce:

```text
risk score
risk level
risk reasons
confidence
recommended validation
safe edit boundary
contracts to preserve
```

## Acceptance criteria

```text
get_function_impact returns high-quality blast radius.
Risk score is explainable, not a black box.
Uncertainty is visible.
Impact output is directly usable by an agent before editing.
```

---

# 10. Phase 10 — `prepare_change` flagship tool

## Objective

Make `prepare_change` the north-star MCP tool every coding agent calls before modifying a repo.

## Input

```json
{
  "repo": "example",
  "task": "Fix retry failure disposition handling in the executor",
  "target_hint": "PlanExecutor.execute_plan",
  "max_tokens": 12000,
  "include_runtime": true,
  "include_tests": true,
  "include_validation": true
}
```

## Output

```text
target functions
function descriptors
impact report
context pack
safe edit boundary
contracts to preserve
related tests
runtime paths
validation recipe
risk score
confidence
omitted relevant context
```

## Agent tasks

```text
- Build query planner for prepare_change.
- Resolve task to candidate functions.
- Rank candidates.
- Build impact report.
- Build context pack.
- Generate validation recipe.
- Generate safe edit boundary.
- Return structured evidence.
```

## Acceptance criteria

```text
Given a natural-language task, prepare_change identifies likely functions.
It returns enough context for an agent to act without broad repo scraping.
It gives tests and validation.
It gives risks and contracts.
It works across at least Python + TS + Go initially.
```

---

# 11. Phase 11 — Snapshot and diff engine

## Objective

Support graph diffs across time, branches, commits, and agent changes.

## Diff targets

```text
main vs working tree
base branch vs PR branch
before index vs after index
release N vs release N+1
agent run before vs after
```

## Diff outputs

```text
functions added
functions removed
functions changed
signatures changed
descriptors changed
call edges added/removed
runtime entrypoints changed
tests added/removed
new untested public functions
new dependency cycles
risk delta
```

## CLI commands

```bash
codegraph diff --base main --head HEAD
codegraph pr-impact --base origin/main --head HEAD
codegraph changed-functions --base main --head HEAD
codegraph descriptor-drift --base main --head HEAD
```

## Acceptance criteria

```text
App can compare two snapshots.
App can report changed function blast radius.
App can output PR-ready impact summary.
Graph diffs are deterministic enough for CI.
```

---

# 12. Phase 12 — Boundary policy engine

## Objective

Let repos define architectural rules and have the graph enforce them.

## Example policy

```yaml
boundaries:
  - name: api_to_service
    allow:
      - api -> services
    deny:
      - api -> repositories
  - name: domain_purity
    deny:
      - domain -> infrastructure
      - domain -> fastapi
      - domain -> sqlalchemy
```

## Agent tasks

```text
- Define boundary policy schema.
- Map files/functions to layers.
- Check imports and calls against rules.
- Add violation evidence.
- Add CLI command and MCP tool.
```

## MCP/CLI

```text
codegraph.validate_boundaries
codegraph.boundary_report
```

```bash
codegraph validate-boundaries --repo example
```

## Acceptance criteria

```text
Boundary violations are detected with file/line evidence.
PR diff mode reports new violations.
prepare_change warns when target area has boundary risk.
```

---

# 13. Phase 13 — Failure memory

## Objective

Make the app remember where failures happened and connect failures to code graph nodes.

## Failure sources

```text
test failures
CI logs
Docker logs
stack traces
runtime exceptions
agent repair attempts
manual annotations
```

## Data model

```text
FailureEvent
FailureEvidence
FailureToFunctionEdge
FailureToTestEdge
FailureResolution
```

## Agent tasks

```text
- Add stacktrace parser.
- Map stack frames to functions.
- Ingest pytest/Jest/Go test output.
- Ingest restricted Docker logs.
- Store failure events.
- Link failures to functions/tests.
```

## Acceptance criteria

```text
Given a stack trace, app maps it to source functions.
Given a function, app reports historical failures.
prepare_change includes prior failures when relevant.
Impact risk score incorporates historical failures.
```

---

# 14. Phase 14 — Agent telemetry feedback loop

## Objective

Improve context ranking and tool usefulness over time.

## Track per agent run

```text
task
tool calls
context pack returned
files read
files edited
tests run
tests passed/failed
follow-up calls
final status
manual override notes
```

## Agent tasks

```text
- Define agent run telemetry schema.
- Add optional telemetry ingestion endpoint.
- Add CLI import for agent run summaries.
- Link edited files/functions to context packs.
- Score whether context pack was sufficient.
```

## Ranking feedback

Use telemetry to learn:

```text
which files are often needed together
which tests are predictive
which context items were ignored
which graph edges were useful
which missing edges caused follow-up search
```

## Acceptance criteria

```text
App can record an agent run.
App can report context-pack effectiveness.
Ranking engine can use feedback signals optionally.
```

---

# 15. Phase 15 — Optional LLM enrichment

## Objective

Use LLMs to enrich descriptors and summaries while preserving static evidence as source of truth.

## Rules

```text
LLM output is never authoritative.
Every LLM-generated summary must cite graph/source evidence.
Generated descriptors must be marked llm_generated.
Do not overwrite human descriptors.
Do not overwrite static facts.
```

## Use cases

```text
subsystem summaries
function behavior summaries
risk explanations
patch plan suggestions
architecture review narrative
descriptor enrichment candidates
```

## Agent tasks

```text
- Add LLM provider interface.
- Add local OpenAI-compatible endpoint support.
- Add configurable model endpoint.
- Add prompt templates with evidence-only constraints.
- Store generated summaries separately.
```

## Acceptance criteria

```text
App can generate optional function summaries.
Summaries cite source slices/edges.
Disabling LLM support leaves core app fully functional.
```

---

# 16. Phase 16 — Semantic search optional layer

## Objective

Add embeddings without making them mandatory.

## Search modes

```text
symbol search
keyword search
graph search
semantic search
hybrid search
```

## Optional backends

```text
SQLite FTS
Qdrant
pgvector
LanceDB
DuckDB VSS if suitable
```

## Agent tasks

```text
- Add embedding provider interface.
- Add local embedding endpoint support.
- Embed descriptors, function summaries, file summaries.
- Add hybrid ranking.
- Keep graph proximity stronger than semantic match for impact.
```

## Acceptance criteria

```text
Semantic search improves task-to-function matching.
System still works when embeddings are disabled.
Search results include evidence and confidence.
```

---

# 17. Phase 17 — CI/CD mode

## Objective

Make the tool useful in pre-merge and post-merge workflows.

## Commands

```bash
codegraph ci index
codegraph ci pr-impact --base origin/main --head HEAD
codegraph ci validate-boundaries
codegraph ci descriptor-drift
codegraph ci test-recommendations
codegraph ci risk-gate --max-risk high
```

## Outputs

```text
JSON report
Markdown PR comment
SARIF optional
JUnit-like report optional
```

## Acceptance criteria

```text
CI can fail on boundary violations.
CI can warn on high-risk untested function changes.
CI can post PR impact summaries.
CI output is stable and machine-readable.
```

---

# 18. Phase 18 — Enterprise hardening

## Objective

Make it safe enough for team/enterprise use.

## Security controls

```text
path jail
read-only mounts
secret redaction
no arbitrary command execution
container allowlist
repo allowlist
MCP auth if HTTP transport
audit logs
rate limits
max file size
max graph traversal depth
.env exclusion by default
private key redaction
token redaction
```

## Operational controls

```text
structured logs
Prometheus metrics
OpenTelemetry traces
health checks
index run metrics
cache metrics
query latency metrics
database size metrics
```

## Acceptance criteria

```text
Security review doc exists.
Threat model exists.
All MCP tools have input validation.
Sensitive files are excluded/redacted by default.
Metrics show indexing/query performance.
```

---

# 19. Phase 19 — Performance and scale

## Objective

Handle large repos efficiently.

## Targets

```text
100K LOC: fast local indexing
1M LOC: acceptable incremental indexing
10M LOC: worker/queue/multi-repo mode later
```

## Optimizations

```text
file hash cache
incremental indexing
parallel file parsing
batched DB writes
prepared statements
source slice cache
query result cache
impact cache
graph traversal caps
hotspot precomputation
```

## Metrics to track

```text
files/sec indexed
functions/sec extracted
edges/sec resolved
DB write time
query latency p50/p95
context pack build time
memory usage
cache hit rate
```

## Acceptance criteria

```text
Incremental indexing only touches changed files.
Large generated/vendor dirs are skipped.
Context pack queries remain fast on 100K+ LOC repos.
Performance benchmark script exists.
```

---

# 20. Phase 20 — Multi-repo and dependency intelligence

## Objective

Support monorepos and related service repos.

## Features

```text
multi-repo registry
cross-repo dependency edges
package dependency graph
API/client contract linking
shared library impact analysis
service-to-service runtime map
```

## Agent tasks

```text
- Add repo grouping/workspace model.
- Add package manager parsers.
- Resolve internal package imports.
- Link OpenAPI/gRPC/GraphQL contracts.
- Track cross-repo impact.
```

## Acceptance criteria

```text
Workspace overview shows multiple repos.
Shared library change can report affected downstream services.
prepare_change can include cross-repo impact when enabled.
```

---

# Final target architecture

```text
CodeGraph MCP
├── MCP Server
├── REST API
├── CLI
├── Indexing Engine
├── Analyzer Registry
│   ├── Tree-sitter Query Engine
│   ├── Language Resolver Plugins
│   └── Framework Binding Plugins
├── Function Graph
├── Runtime Graph
├── Test Graph
├── Data/Resource Graph
├── Descriptor Graph
├── Snapshot/Diff Engine
├── Impact Engine
├── Context Pack Builder
├── Validation Recipe Engine
├── Boundary Policy Engine
├── Failure Memory
├── Agent Telemetry Store
├── Optional Semantic Search
├── Optional LLM Enrichment
└── Storage Backends
    ├── SQLite default
    ├── Kuzu optional
    ├── Neo4j optional
    └── Qdrant/pgvector optional
```

---

# Recommended build order

The next agents should not randomly jump around. Use this order:

```text
1. Stabilize scaffold.
2. Harden function model and stable IDs.
3. Improve Python analyzer deeply.
4. Add JS/TS/Go analyzers.
5. Build call graph resolution.
6. Build prepare_change properly.
7. Add context pack budgeting.
8. Add runtime graph.
9. Add test graph.
10. Add snapshot/diff engine.
11. Add boundary policy.
12. Add failure memory.
13. Add CI mode.
14. Add optional semantic/LLM enrichment.
15. Expand languages.
```

---

# MVP-to-platform milestones

## Milestone A — Useful local MCP

```text
Function extraction
Descriptors
Find function
Callers/callees
Impact report
Context pack
CLI/REST/MCP working
```

## Milestone B — Agent change-prep system

```text
prepare_change
safe edit boundary
contracts to preserve
related tests
validation recipe
runtime bindings
risk score
```

## Milestone C — CI intelligence

```text
graph snapshots
diffs
PR impact
descriptor drift
boundary checks
test recommendations
```

## Milestone D — Engineering intelligence substrate

```text
failure memory
agent telemetry
repo health
semantic search
optional LLM summaries
multi-repo support
```

---

# Definition of "fully realized"

This project is fully realized when a coding agent can call one tool:

```text
codegraph.prepare_change
```

And receive:

```text
What code matters
Why it matters
What can break
What contracts must stay stable
What tests prove the change
What runtime path reaches it
What architecture rules apply
What historical failures exist
What context fits in budget
What uncertainty remains
```

At that point, CodeGraph MCP is not another MCP.
It is the **prefrontal cortex for agentic code modification**.
