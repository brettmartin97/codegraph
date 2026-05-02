from __future__ import annotations

import re

from codegraph_mcp.graph.sqlite_store import SQLiteStore
from codegraph_mcp.query.impact import ImpactEngine

# Stop-words excluded from task keyword matching
_STOP = frozenset({
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or",
    "is", "it", "be", "this", "that", "with", "from", "by", "not", "use",
    "make", "add", "fix", "update", "change", "get", "set",
})


class ContextEngine:
    def __init__(self, store: SQLiteStore):
        self.store = store
        self.impact = ImpactEngine(store)

    def repo_overview(self, repo_name: str) -> dict:
        repo = self.store.get_repo(repo_name)
        if not repo:
            raise ValueError(f"Unknown repo {repo_name}")
        overview = self.store.overview(repo.id)
        overview["repo"] = repo.name
        overview["path"] = repo.path
        _INFRA_KINDS = {"from", "expose", "cmd", "entrypoint", "run"}
        runtime = self.store.runtime_bindings(repo.id)
        overview["runtime_bindings"] = [
            {"kind": r.kind, "name": r.name, "target": r.target, "confidence": r.confidence}
            for r in runtime[:20]
            if r.kind not in _INFRA_KINDS
        ]
        overview["risk_flags"] = []
        if overview["descriptor_coverage"] < 0.5:
            overview["risk_flags"].append("Low function descriptor coverage")
        if overview["high_complexity"]:
            overview["risk_flags"].append(f"{len(overview['high_complexity'])} high-complexity function(s) detected")
        return overview

    def prepare_change(self, repo_name: str, task: str, max_tokens: int = 12000) -> dict:
        repo = self.store.get_repo(repo_name)
        if not repo:
            raise ValueError(f"Unknown repo {repo_name}")

        # ── Phase 10: Better target discovery ────────────────────────────────
        # Tokenize task, skip stop-words, weight by term frequency
        raw_words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", task.lower())
        words = [w for w in raw_words if len(w) > 2 and w not in _STOP]
        # Camel/snake expansion: "createOrder" -> "create", "order"
        expanded: list[str] = []
        for w in words:
            parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", w).replace("_", " ").split()
            expanded.extend(p.lower() for p in parts if len(p) > 2 and p.lower() not in _STOP)
        all_terms = list(dict.fromkeys(words + expanded))[:12]

        # Search across all terms, deduplicate by function id
        candidate_map: dict[str, tuple[int, object]] = {}
        for term in all_terms:
            for fn in self.store.find_functions(repo.id, term, 15):
                hay = " ".join([
                    fn.qualified_name,
                    fn.signature or "",
                    fn.descriptor.summary if fn.descriptor else "",
                    fn.descriptor.raw or "" if fn.descriptor else "",
                ]).lower()
                score = sum(1 for t in all_terms if t in hay)
                # Prefer exact name matches
                if fn.name.lower() in all_terms:
                    score += 3
                if fn.qualified_name.lower() in all_terms:
                    score += 2
                if fn.id not in candidate_map or candidate_map[fn.id][0] < score:
                    candidate_map[fn.id] = (score, fn)

        scored = [(s, fn) for s, fn in candidate_map.values() if s > 0]
        scored.sort(key=lambda x: x[0], reverse=True)
        targets = [fn for _, fn in scored[:5]]

        # ── Build impact for top target ───────────────────────────────────────
        impact_report = None
        if targets:
            try:
                impact_report = self.impact.function_impact(
                    repo_name, targets[0].qualified_name, max_tokens=max_tokens
                )
            except Exception:
                pass

        # ── Safe edit boundary = unique files of top candidates ───────────────
        safe_boundary = sorted({self.store.file_path(t.file_id) for t in targets[:3]})

        # ── Contracts to preserve ─────────────────────────────────────────────
        contracts = self._contracts(targets[:3])

        # ── Validation recipe ─────────────────────────────────────────────────
        if impact_report:
            validation = impact_report.recommended_validation
        elif targets:
            validation = [
                f"Review {self.store.file_path(t.file_id)}:{t.start_line}-{t.end_line}"
                for t in targets[:3]
            ] + ["Run full test suite after changes"]
        else:
            validation = [
                "No target function identified",
                "Run repo_overview to understand codebase structure",
                "Use find_function to locate relevant code before changing",
            ]

        # ── Related tests ─────────────────────────────────────────────────────
        related_tests = []
        if targets:
            seen_test_ids: set[str] = set()
            for fn in targets[:3]:
                for t in self.store.related_tests(repo.id, fn, limit=5):
                    if t.id not in seen_test_ids:
                        seen_test_ids.add(t.id)
                        related_tests.append({"name": t.qualified_name, "file": self.store.file_path(t.file_id)})

        # ── Structured validation checklist (agent-actionable) ────────────────
        # Each item has: step (human text), kind (review|test|lint|integration),
        # target (file path / test path / function name), and optional cmd.
        checklist: list[dict] = []
        for fn in targets[:3]:
            file_path = self.store.file_path(fn.file_id)
            checklist.append({
                "step": f"Review {fn.qualified_name} at {file_path}:{fn.start_line}-{fn.end_line}",
                "kind": "review",
                "target": f"{file_path}:{fn.start_line}-{fn.end_line}",
            })
        for t in related_tests[:8]:
            checklist.append({
                "step": f"Run test {t['name']}",
                "kind": "test",
                "target": t["file"],
                "cmd": f"pytest {t['file']}::{t['name'].split('.')[-1]}",
            })
        if impact_report and impact_report.risk_level in ("high", "critical"):
            checklist.append({
                "step": "Run full test suite (high blast radius)",
                "kind": "integration",
                "target": "all",
                "cmd": "pytest",
            })
        if not checklist:
            checklist.append({
                "step": "No target function identified — run find_function or repo_overview",
                "kind": "review",
                "target": "repo",
            })

        return {
            "repo": repo_name,
            "task": task,
            "target_functions": [t.model_dump() for t in targets],
            "impact_report": impact_report.model_dump() if impact_report else None,
            "safe_edit_boundary": safe_boundary,
            "contracts_to_preserve": contracts,
            "related_tests": related_tests,
            "validation_recipe": validation,
            "validation_checklist": checklist,
            "risk_score": impact_report.risk_score if impact_report else None,
            "risk_level": impact_report.risk_level if impact_report else "unknown",
            "confidence": 0.85 if len(targets) >= 2 else (0.65 if targets else 0.2),
            "omitted_context": [] if impact_report else ["impact_report: no target function found"],
        }

    def _contracts(self, fns) -> list[str]:
        contracts = []
        for fn in fns:
            if fn.signature:
                contracts.append(
                    f"Preserve callable contract: {fn.qualified_name}({', '.join(p['name'] for p in fn.parameters_json)})"
                )
            if fn.return_type:
                contracts.append(f"Preserve return type for {fn.qualified_name}: {fn.return_type}")
            if fn.descriptor and fn.descriptor.raises:
                contracts.append(f"Preserve error contract for {fn.qualified_name}: raises {fn.descriptor.raises}")
        return contracts

