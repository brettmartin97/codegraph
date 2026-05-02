from __future__ import annotations

from pathlib import Path

from codegraph_mcp.graph.models import ImpactReport
from codegraph_mcp.graph.sqlite_store import SQLiteStore
from codegraph_mcp.query.source import source_slice

# Risk level thresholds
_RISK_HIGH   = 0.65
_RISK_MEDIUM = 0.35


class ImpactEngine:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def function_impact(
        self,
        repo_name: str,
        function_query: str,
        depth: int = 3,
        max_tokens: int = 12000,
        include_transitive: bool = True,
    ) -> ImpactReport:
        repo = self.store.get_repo(repo_name)
        if not repo:
            raise ValueError(f"Unknown repo {repo_name}")
        fn = self.store.get_function_by_name(repo.id, function_query)
        if not fn:
            matches = self.store.find_functions(repo.id, function_query, 1)
            if not matches:
                raise ValueError(f"No function matched {function_query!r} in repo {repo_name!r}")
            fn = matches[0]

        direct_callers  = self.store.callers(fn.id)
        direct_callees  = self.store.callees(fn.id)
        unresolved      = self.store.unresolved_callees(fn.id)
        tests           = self.store.related_tests(repo.id, fn)
        runtime         = self._runtime_relevant(repo.id, fn)

        # Phase 9: Transitive traversal (bounded, min confidence 0.5 for transit)
        trans_callers: list = []
        trans_callees: list = []
        if include_transitive and depth > 1:
            trans_callers = self.store.transitive_callers(fn.id, depth=depth, min_confidence=0.5)
            trans_callees = self.store.transitive_callees(fn.id, depth=depth, min_confidence=0.5)
            # Exclude already-in-direct
            direct_caller_ids = {c.id for c in direct_callers}
            direct_callee_ids = {c.id for c in direct_callees}
            trans_callers = [c for c in trans_callers if c.id not in direct_caller_ids]
            trans_callees = [c for c in trans_callees if c.id not in direct_callee_ids]

        # ── Risk scoring (Phase 9) ────────────────────────────────────────────
        reasons: list[str] = []
        risk = 0.0

        caller_total = len(direct_callers) + len(trans_callers)
        if caller_total > 0:
            reasons.append(f"Reached by {caller_total} function(s) in the call graph ({len(direct_callers)} direct)")
            risk += min(0.20, 0.04 * caller_total)

        if direct_callees:
            reasons.append(f"Calls {len(direct_callees)} direct callee(s)")
            risk += min(0.10, 0.02 * len(direct_callees))

        if unresolved:
            reasons.append(f"Has {len(unresolved)} unresolved/dynamic callee(s) – confidence is limited")
            risk += min(0.12, 0.04 * len(unresolved))

        if runtime:
            reasons.append(f"Reachable via {len(runtime)} runtime binding(s) (routes, jobs, events)")
            risk += 0.20

        if not tests:
            reasons.append("No related tests found – changes are unvalidated")
            risk += 0.18

        if fn.descriptor and fn.descriptor.quality_score < 0.55:
            reasons.append("Function descriptor is inferred or low quality – intent unclear")
            risk += 0.08

        if (fn.complexity or 0) > 8:
            reasons.append(f"Cyclomatic complexity {fn.complexity} is high")
            risk += min(0.15, (fn.complexity or 0) / 80)

        if fn.is_async:
            reasons.append("Async function – concurrency concerns may apply")
            risk += 0.04

        # Check historical failures
        prior_failures = self.store.failures_for_function(fn.id)
        if prior_failures:
            reasons.append(f"{len(prior_failures)} historical failure event(s) recorded for this function")
            risk += min(0.12, 0.06 * len(prior_failures))

        risk = round(min(1.0, risk), 3)
        risk_level = "high" if risk >= _RISK_HIGH else ("medium" if risk >= _RISK_MEDIUM else "low")

        confidence = max(0.25, min(0.95, 0.90 - 0.03 * len(unresolved) - 0.01 * len(trans_callers)))

        # ── Infer change intent ────────────────────────────────────────────────
        change_intent = "unknown"
        if fn.decorators:
            d_str = " ".join(fn.decorators).lower()
            if "route" in d_str or "get(" in d_str or "post(" in d_str:
                change_intent = "runtime_config_change"
            elif "task" in d_str:
                change_intent = "side_effect_change"
        if fn.name.startswith("test_"):
            change_intent = "test_only_change"

        # ── Context pack (token-budgeted) ─────────────────────────────────────
        repo_path = Path(repo.path)
        token_budget = max_tokens
        pack = []

        def _add_slice(fn_node, reason, score):
            nonlocal token_budget
            sl = source_slice(repo_path, self.store, fn_node, reason, score)
            cost = max(1, len(sl.content) // 4)
            if token_budget - cost >= 0:
                token_budget -= cost
                pack.append(sl)

        _add_slice(fn, "Target function", 1.0)
        for c in direct_callers[:6]:
            _add_slice(c, "Direct caller", 0.88)
        for c in direct_callees[:6]:
            _add_slice(c, "Direct callee", 0.80)
        for t in tests[:5]:
            _add_slice(t, "Related test", 0.84)
        for c in trans_callers[:4]:
            _add_slice(c, "Transitive caller", 0.60)

        # ── Validation recipe ─────────────────────────────────────────────────
        file_path = self.store.file_path(fn.file_id)
        validation: list[str] = [f"Review {file_path}:{fn.start_line}-{fn.end_line}"]
        if tests:
            for t in tests[:5]:
                tf = self.store.file_path(t.file_id)
                validation.append(f"Run {tf}::{t.name}")
        else:
            validation.append("Write or identify tests for this function before modifying")
        if unresolved:
            validation.append(f"Verify {len(unresolved)} unresolved call targets won't break")
        if runtime:
            validation.append("Verify runtime route/job/event contract is preserved")
        if fn.descriptor and fn.descriptor.quality_score < 0.55:
            validation.append("Document function intent before changing (descriptor is inferred)")
        validation.append("Run ruff/mypy after changes")

        return ImpactReport(
            target_function=fn,
            direct_callers=direct_callers,
            direct_callees=direct_callees,
            transitive_callers=trans_callers[:20],
            transitive_callees=trans_callees[:20],
            unresolved_callees=unresolved,
            related_tests=tests,
            runtime_entrypoints=runtime,
            risk_score=risk,
            risk_level=risk_level,
            confidence=round(confidence, 3),
            reasons=reasons,
            recommended_validation=validation,
            context_pack=pack,
            change_intent=change_intent,
        )

    def _runtime_relevant(self, repo_id: str, fn) -> list:
        path = self.store.file_path(fn.file_id).lower()
        bindings = self.store.runtime_bindings(repo_id)
        # Match by file path hints or function name in target
        hints = {p for p in path.replace("\\", "/").split("/") if p and p not in {"src", "app", "lib", "."}}
        hints.add(fn.name.lower())
        if fn.enclosing_class:
            hints.add(fn.enclosing_class.lower())
        relevant = []
        for rb in bindings:
            hay = f"{rb.name} {rb.target or ''} {rb.kind}".lower()
            if any(h in hay for h in hints):
                relevant.append(rb)
        return relevant[:10]

