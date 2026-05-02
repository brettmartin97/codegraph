from __future__ import annotations

from datetime import datetime, timezone

try:
    from enum import StrEnum
except ImportError:
    import enum
    class StrEnum(str, enum.Enum):
        pass
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class FunctionKind(StrEnum):
    function = "function"
    method = "method"
    constructor = "constructor"
    lambda_fn = "lambda"
    closure = "closure"
    procedure = "procedure"
    endpoint_handler = "endpoint_handler"
    test = "test"


class EdgeType(StrEnum):
    calls = "CALLS"
    references = "REFERENCES"
    imports = "IMPORTS"
    tested_by = "TESTED_BY"
    tests = "TESTS"
    route_to = "ROUTE_TO"
    job_to = "JOB_TO"
    event_to = "EVENT_TO"
    command_to = "COMMAND_TO"
    configured_by = "CONFIGURED_BY"
    uses_env = "USES_ENV"
    uses_database_table = "USES_DATABASE_TABLE"
    uses_queue = "USES_QUEUE"
    uses_external_api = "USES_EXTERNAL_API"
    runtime_reaches = "RUNTIME_REACHES"
    defines = "DEFINES"
    inherits = "INHERITS"


# ── Enrichment category taxonomy ─────────────────────────────────────────────

ENRICHMENT_CATEGORIES = [
    "routing", "data-access", "business-logic", "utility", "config",
    "model", "api-client", "orchestration", "testing", "cli", "ui",
    "middleware", "serialization", "error-handling", "security", "other",
]


class Repository(BaseModel):
    id: str
    name: str
    path: str
    created_at: str = Field(default_factory=utcnow)


class CodeFile(BaseModel):
    id: str
    repo_id: str
    path: str
    language: str | None = None
    size_bytes: int = 0
    line_count: int = 0
    content_hash: str
    is_test: bool = False
    is_generated: bool = False
    is_vendor: bool = False
    last_indexed_at: str = Field(default_factory=utcnow)


class FunctionDescriptor(BaseModel):
    function_id: str
    raw: str | None = None
    summary: str | None = None
    params: dict[str, str] = Field(default_factory=dict)
    returns: str | None = None
    raises: list[str] = Field(default_factory=list)
    side_effects: list[str] = Field(default_factory=list)
    source: str = "none"
    quality_score: float = 0.0
    # ── Enrichment fields (populated by enrichment pipeline) ─────────────────
    purpose: str | None = None          # one-sentence LLM/heuristic description
    category: str | None = None         # from ENRICHMENT_CATEGORIES
    importance: float = 0.0             # 0-1 architectural centrality
    tags: list[str] = Field(default_factory=list)
    enrichment_source: str = "none"     # none | heuristic | anthropic | openai | custom_http
    enriched_at: str | None = None


class ClassNode(BaseModel):
    """Represents a class/struct definition extracted from source."""
    id: str
    repo_id: str
    file_id: str
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    bases: list[str] = Field(default_factory=list)      # base class names
    docstring: str | None = None
    is_abstract: bool = False
    decorators: list[str] = Field(default_factory=list)
    loc: int = 0
    method_count: int = 0


class FunctionNode(BaseModel):
    id: str
    repo_id: str
    file_id: str
    language: str
    kind: str = FunctionKind.function
    name: str
    qualified_name: str
    display_name: str
    start_line: int
    end_line: int
    signature: str | None = None
    return_type: str | None = None
    parameters_json: list[dict[str, Any]] = Field(default_factory=list)
    decorators: list[str] = Field(default_factory=list)
    annotations: dict[str, Any] = Field(default_factory=dict)
    visibility: str | None = None
    descriptor: FunctionDescriptor | None = None
    body_hash: str
    signature_hash: str
    descriptor_hash: str | None = None
    complexity: int | None = None
    loc: int = 0
    is_async: bool = False
    is_generator: bool = False
    is_test: bool = False
    parent_symbol_id: str | None = None
    enclosing_class: str | None = None
    namespace: str | None = None
    confidence: float = 1.0


class FunctionEdge(BaseModel):
    id: str
    repo_id: str
    source_function_id: str | None = None
    target_function_id: str | None = None
    target_symbol_name: str | None = None
    edge_type: str
    confidence: float
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utcnow)


class RuntimeBinding(BaseModel):
    id: str
    repo_id: str
    file_id: str | None = None
    kind: str
    name: str
    target: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.7


class CodeSlice(BaseModel):
    file: str
    line_range: tuple[int, int]
    content: str
    reason: str
    score: float
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class ImpactReport(BaseModel):
    target_function: FunctionNode
    direct_callers: list[FunctionNode] = Field(default_factory=list)
    direct_callees: list[FunctionNode] = Field(default_factory=list)
    transitive_callers: list[FunctionNode] = Field(default_factory=list)
    transitive_callees: list[FunctionNode] = Field(default_factory=list)
    unresolved_callees: list[FunctionEdge] = Field(default_factory=list)
    related_tests: list[FunctionNode] = Field(default_factory=list)
    runtime_entrypoints: list[RuntimeBinding] = Field(default_factory=list)
    risk_score: float
    risk_level: str = "unknown"
    confidence: float
    reasons: list[str]
    recommended_validation: list[str]
    context_pack: list[CodeSlice] = Field(default_factory=list)
    change_intent: str = "unknown"


class FunctionSnapshot(BaseModel):
    id: str
    function_id: str
    repo_id: str
    ref: str = "HEAD"
    captured_at: str = Field(default_factory=utcnow)
    body_hash: str
    signature_hash: str
    descriptor_hash: str | None = None
    qualified_name: str
    signature: str | None = None
    start_line: int
    end_line: int
    caller_count: int = 0
    callee_count: int = 0


class FunctionDiff(BaseModel):
    function_id: str
    qualified_name: str
    file: str
    change_type: str
    body_changed: bool = False
    signature_changed: bool = False
    descriptor_changed: bool = False


class SnapshotDiff(BaseModel):
    repo: str
    ref_a: str
    ref_b: str
    functions_added: list[FunctionDiff] = Field(default_factory=list)
    functions_removed: list[FunctionDiff] = Field(default_factory=list)
    functions_changed: list[FunctionDiff] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class BoundaryRule(BaseModel):
    name: str
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class BoundaryPolicy(BaseModel):
    boundaries: list[BoundaryRule] = Field(default_factory=list)
    layer_map: dict[str, str] = Field(default_factory=dict)


class BoundaryViolation(BaseModel):
    rule_name: str
    from_file: str
    to_file: str
    from_layer: str
    to_layer: str
    function_id: str | None = None
    target_function_id: str | None = None
    edge_type: str
    confidence: float


class FailureEvent(BaseModel):
    id: str
    repo_id: str
    kind: str
    message: str
    stack_trace: str | None = None
    function_ids: list[str] = Field(default_factory=list)
    file_path: str | None = None
    line: int | None = None
    occurred_at: str = Field(default_factory=utcnow)
    source: str = "manual"
    metadata: dict[str, Any] = Field(default_factory=dict)
