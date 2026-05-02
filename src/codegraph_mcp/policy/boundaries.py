"""Boundary policy engine.

Loads a YAML policy file (see examples/boundaries.yaml) and checks CALLS
edges against allow/deny layer rules.  Produces BoundaryViolation records
for every edge that crosses a forbidden boundary.
"""
from __future__ import annotations

import re
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

from codegraph_mcp.graph.models import BoundaryPolicy, BoundaryRule, BoundaryViolation
from codegraph_mcp.graph.sqlite_store import SQLiteStore

# ── YAML loading ──────────────────────────────────────────────────────────────

def load_policy(policy_path: Path) -> BoundaryPolicy:
    """Load a BoundaryPolicy from a YAML file.

    Expected format::

        layer_map:
          "api/**": api
          "domain/**": domain
          "infra/**": infra
          "tests/**": tests

        boundaries:
          - name: no-infra-in-domain
            allow: [domain, api]
            deny: [infra]
          - name: api-only-calls-domain
            allow: [api, domain]
            deny: [tests]
    """
    if yaml is None:
        raise ImportError("PyYAML is required for boundary policy. Install it with: pip install pyyaml")
    if not policy_path.is_file():
        raise FileNotFoundError(f"Policy file not found: {policy_path}")
    with policy_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError("Policy YAML must be a mapping at the top level")
    layer_map: dict[str, str] = {
        str(pattern): str(layer)
        for pattern, layer in (data.get("layer_map") or {}).items()
    }
    rules: list[BoundaryRule] = []
    for entry in data.get("boundaries") or []:
        rules.append(BoundaryRule(
            name=str(entry.get("name", "unnamed")),
            allow=[str(x) for x in (entry.get("allow") or [])],
            deny=[str(x) for x in (entry.get("deny") or [])],
        ))
    return BoundaryPolicy(boundaries=rules, layer_map=layer_map)


# ── Layer resolution ──────────────────────────────────────────────────────────

def _glob_to_regex(pattern: str) -> re.Pattern:
    """Convert a simple glob (** supported) to a compiled regex."""
    # Escape, then undo our own escaping for * and **
    esc = re.escape(pattern)
    esc = esc.replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return re.compile(f"^{esc}", re.IGNORECASE)


def _file_layer(file_path: str, layer_map: dict[str, str]) -> str | None:
    """Return the layer name for *file_path* using glob patterns in *layer_map*."""
    norm = file_path.replace("\\", "/")
    for pattern, layer in layer_map.items():
        rx = _glob_to_regex(pattern)
        if rx.search(norm):
            return layer
    return None


# ── Main checker ──────────────────────────────────────────────────────────────

class BoundaryChecker:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def check(self, repo_id: str, policy: BoundaryPolicy) -> list[BoundaryViolation]:
        """Walk every resolved CALLS edge and check against policy rules."""
        violations: list[BoundaryViolation] = []
        edges = self.store.all_resolved_edges(repo_id)
        # Build file-path cache: function_id -> file_path
        file_cache: dict[str, str] = {}

        def _file(fn_id: str) -> str:
            if fn_id not in file_cache:
                fn = self.store.get_function(fn_id)
                if fn:
                    file_cache[fn_id] = self.store.file_path(fn.file_id)
                else:
                    file_cache[fn_id] = ""
            return file_cache[fn_id]

        for edge in edges:
            from_file = _file(edge.source_function_id)
            to_file   = _file(edge.target_function_id) if edge.target_function_id else ""
            if not from_file or not to_file:
                continue
            from_layer = _file_layer(from_file, policy.layer_map)
            to_layer   = _file_layer(to_file,   policy.layer_map)
            if from_layer is None or to_layer is None:
                continue
            if from_layer == to_layer:
                continue
            for rule in policy.boundaries:
                # Rule applies when the "from" layer is in the rule's allow/deny universe
                all_layers = set(rule.allow) | set(rule.deny)
                if from_layer not in all_layers:
                    continue
                if to_layer in rule.deny:
                    violations.append(BoundaryViolation(
                        rule_name=rule.name,
                        from_file=from_file,
                        to_file=to_file,
                        from_layer=from_layer,
                        to_layer=to_layer,
                        function_id=edge.source_function_id,
                        target_function_id=edge.target_function_id or "",
                        edge_type=str(edge.edge_type),
                        confidence=edge.confidence,
                    ))
        return violations
