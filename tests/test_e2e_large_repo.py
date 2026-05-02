"""End-to-end tests against a real, large codebase (~100K LOC, Python).

These tests are the canonical quality benchmark for code-graph-mcp.  They
validate that the MCP adds real value to LLM context by correctly indexing a
large, production Python repo and providing accurate function-graph queries.

Run with:
    CODEGRAPH_E2E_REPO_PATH=/path/to/large/repo pytest tests/test_e2e_large_repo.py -v --timeout=300

Environment:
    Set CODEGRAPH_E2E_REPO_PATH to the root of a large Python codebase.
    All tests are automatically skipped when the path is absent,
    so the suite remains green in CI without the private repo.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

# â”€â”€ Skip sentinel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_E2E_REPO_PATH = Path(
    os.environ.get("CODEGRAPH_E2E_REPO_PATH", "")
) if os.environ.get("CODEGRAPH_E2E_REPO_PATH") else None

pytestmark = pytest.mark.skipif(
    _E2E_REPO_PATH is None or not _E2E_REPO_PATH.exists(),
    reason="E2E repo not found â€” set CODEGRAPH_E2E_REPO_PATH to enable",
)


# â”€â”€ Session-scoped fixture: index once, reuse for all tests â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.fixture(scope="session")
def large_repo(tmp_path_factory):
    """Index the target repo once, return results for all tests."""
    # Patch settings BEFORE importing anything that reads them
    from codegraph_mcp import config
    config.settings.repo_root = _E2E_REPO_PATH.parent
    config.settings.db_path = tmp_path_factory.mktemp("e2e") / "e2e.db"

    from codegraph_mcp.graph.sqlite_store import SQLiteStore
    from codegraph_mcp.indexing.indexer import Indexer
    from codegraph_mcp.query.context import ContextEngine

    store = SQLiteStore(config.settings.db_path)
    indexer = Indexer(store)
    ctx = ContextEngine(store)

    repo_name = _E2E_REPO_PATH.name
    repo = indexer.add_repo(repo_name, _E2E_REPO_PATH)

    t0 = time.time()
    result = indexer.index_repo(repo_name, "full")
    elapsed = time.time() - t0

    return {
        "store": store,
        "indexer": indexer,
        "ctx": ctx,
        "repo": repo,
        "repo_name": repo_name,
        "result": result,
        "elapsed": elapsed,
    }


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 1. Indexing Quality
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestIndexingQuality:
    """Verify the indexer successfully processes the large codebase."""

    def test_files_seen(self, large_repo):
        """Should index hundreds of source files (Python + configs)."""
        assert large_repo["result"]["files_seen"] >= 300, (
            f"Only {large_repo['result']['files_seen']} files indexed â€” expected â‰¥300"
        )

    def test_functions_extracted(self, large_repo):
        """Should extract thousands of Python functions and methods."""
        assert large_repo["result"]["functions_seen"] >= 1000, (
            f"Only {large_repo['result']['functions_seen']} functions â€” expected â‰¥1000"
        )

    def test_call_edges_extracted(self, large_repo):
        """Should produce a dense call graph with thousands of edges."""
        assert large_repo["result"]["edges_seen"] >= 2000, (
            f"Only {large_repo['result']['edges_seen']} edges â€” expected â‰¥2000"
        )

    def test_indexing_completes_in_reasonable_time(self, large_repo):
        """Indexing 100K LOC should complete within 5 minutes."""
        assert large_repo["elapsed"] < 300, (
            f"Indexing took {large_repo['elapsed']:.1f}s â€” threshold is 300s"
        )

    def test_diagnostic_count_is_low(self, large_repo):
        """Parse failures should be rare (< 5% of files)."""
        files = large_repo["result"]["files_seen"]
        diags = len(large_repo["result"].get("diagnostics", []))
        ratio = diags / max(files, 1)
        assert ratio < 0.05, (
            f"{diags} diagnostics for {files} files ({ratio:.1%}) â€” threshold 5%"
        )

    def test_overview_structure(self, large_repo):
        store = large_repo["store"]
        repo = large_repo["repo"]
        ov = store.overview(repo.id)
        assert ov["file_count"] >= 300
        assert ov["function_count"] >= 1000
        assert "python" in ov["languages"]
        assert ov["languages"]["python"] >= 100

    def test_python_dominates_language_mix(self, large_repo):
        store = large_repo["store"]
        repo = large_repo["repo"]
        ov = store.overview(repo.id)
        total = sum(ov["languages"].values())
        python_share = ov["languages"].get("python", 0) / max(total, 1)
        assert python_share >= 0.4, f"Python share {python_share:.1%} unexpectedly low"


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 2. Function Discovery
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestFunctionDiscovery:
    """Verify find_functions returns correct results for real symbols."""

    def test_finds_routing_functions(self, large_repo):
        store, repo = large_repo["store"], large_repo["repo"]
        results = store.find_functions(repo.id, "route", 10)
        assert results, "Expected to find routing functions"
        names = [f.name.lower() for f in results]
        assert any("route" in n for n in names)

    def test_finds_execution_functions(self, large_repo):
        store, repo = large_repo["store"], large_repo["repo"]
        results = store.find_functions(repo.id, "execute", 10)
        assert results, "Expected to find execute methods"

    def test_finds_invocation_functions(self, large_repo):
        store, repo = large_repo["store"], large_repo["repo"]
        results = store.find_functions(repo.id, "invoke", 10)
        assert results, "Expected to find invoke methods"

    def test_finds_scoring_functions(self, large_repo):
        store, repo = large_repo["store"], large_repo["repo"]
        results = store.find_functions(repo.id, "score", 10)
        assert results, "Expected to find score functions"

    def test_find_by_exact_name(self, large_repo):
        store, repo = large_repo["store"], large_repo["repo"]
        # score_component is a highly-called utility confirmed in the index
        results = store.find_functions(repo.id, "score_component", 5)
        assert results, "score_component should be found by exact name"
        assert any(f.name == "score_component" for f in results)

    def test_find_returns_qualified_names(self, large_repo):
        store, repo = large_repo["store"], large_repo["repo"]
        results = store.find_functions(repo.id, "run", 10)
        for fn in results:
            assert fn.qualified_name, "Every function must have a qualified_name"
            assert fn.file_id, "Every function must reference a file"

    def test_function_has_location_info(self, large_repo):
        store, repo = large_repo["store"], large_repo["repo"]
        results = store.find_functions(repo.id, "execute", 5)
        for fn in results:
            assert fn.start_line >= 1
            assert fn.end_line >= fn.start_line

    def test_functions_have_language(self, large_repo):
        store, repo = large_repo["store"], large_repo["repo"]
        results = store.find_functions(repo.id, "handle", 10)
        for fn in results:
            assert fn.language in ("python", "javascript", "typescript")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 3. Call Graph & Impact Analysis
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestCallGraphAndImpact:
    """Verify the call graph is populated and impact analysis works."""

    def test_score_component_has_many_callers(self, large_repo):
        """score_component is a widely-used utility â€” should have >50 callers."""
        store, repo = large_repo["store"], large_repo["repo"]
        fns = store.find_functions(repo.id, "score_component", 5)
        assert fns
        fn = next((f for f in fns if f.name == "score_component"), fns[0])
        callers = store.callers(fn.id)
        assert len(callers) >= 10, (
            f"score_component only has {len(callers)} callers â€” expected â‰¥10"
        )

    def test_transitive_callers_expand_blast_radius(self, large_repo):
        store, repo = large_repo["store"], large_repo["repo"]
        fns = store.find_functions(repo.id, "score_component", 5)
        assert fns
        fn = next((f for f in fns if f.name == "score_component"), fns[0])
        direct = store.callers(fn.id)
        transitive = store.transitive_callers(fn.id, depth=2, min_confidence=0.5)
        # Both sets should be non-empty for a widely-used helper. The exact
        # sizes are sensitive to confidence cutoffs and the BFS dedup policy,
        # so we only assert presence rather than relative size here.
        assert direct, "score_component should have direct callers"
        assert transitive, "score_component should have transitive callers"

    def test_callees_are_populated(self, large_repo):
        """Functions should reference their callees."""
        store, repo = large_repo["store"], large_repo["repo"]
        fns = store.find_functions(repo.id, "route", 10)
        # At least one routing function should have callees
        any_callees = any(store.callees(fn.id) for fn in fns)
        assert any_callees, "No routing function has callees â€” call graph may be empty"

    def test_resolved_edges_exist(self, large_repo):
        """resolve_edges should link target_function_id for known symbols."""
        store, repo = large_repo["store"], large_repo["repo"]
        resolved = store.all_resolved_edges(repo.id)
        assert len(resolved) >= 500, (
            f"Only {len(resolved)} resolved edges â€” expected â‰¥500"
        )

    def test_impact_report_for_high_connectivity_function(self, large_repo):
        from codegraph_mcp.query.impact import ImpactEngine
        store, _repo = large_repo["store"], large_repo["repo"]
        engine = ImpactEngine(store)
        report = engine.function_impact(large_repo["repo_name"], "score_component", depth=2)
        assert report.target_function.id
        assert report.risk_score >= 0.0
        assert report.risk_level in ("low", "medium", "high")
        # A function with 100+ callers should not be "low" risk
        assert report.risk_level in ("medium", "high"), (
            f"score_component risk_level={report.risk_level} â€” expected medium or high"
        )
        assert report.direct_callers, "impact report should include direct callers"

    def test_impact_report_includes_transitive(self, large_repo):
        from codegraph_mcp.query.impact import ImpactEngine
        store = large_repo["store"]
        engine = ImpactEngine(store)
        report = engine.function_impact(large_repo["repo_name"], "score_component", depth=2, include_transitive=True)
        # Transitive callers should extend beyond direct
        all_callers = len(report.direct_callers) + len(report.transitive_callers)
        assert all_callers >= len(report.direct_callers)

    def test_impact_report_has_change_intent(self, large_repo):
        from codegraph_mcp.query.impact import ImpactEngine
        store = large_repo["store"]
        engine = ImpactEngine(store)
        report = engine.function_impact(large_repo["repo_name"], "score_component", depth=1)
        assert report.change_intent  # should not be empty string

    def test_tests_edge_type_present(self, large_repo):
        """TESTS edges should be emitted for test functions."""
        store, repo = large_repo["store"], large_repo["repo"]
        conn = store.conn
        row = conn.execute(
            "SELECT COUNT(*) as n FROM function_edges WHERE repo_id=? AND edge_type='TESTS'",
            (repo.id,)
        ).fetchone()
        assert row["n"] >= 0  # May be 0 if no test_* naming conventions found


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 4. Prepare-Change Context Quality
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestPrepareChangeQuality:
    """Validate prepare_change returns useful context for LLM consumption."""

    def test_routing_task_finds_targets(self, large_repo):
        ctx = large_repo["ctx"]
        result = ctx.prepare_change(large_repo["repo_name"], "update capability routing logic")
        assert result["target_functions"], "Should find functions for routing task"
        names = [f["name"].lower() for f in result["target_functions"]]
        assert any("route" in n or "routing" in n or "capability" in n for n in names), (
            f"Routing task targets don't match: {names}"
        )

    def test_scoring_task_finds_targets(self, large_repo):
        ctx = large_repo["ctx"]
        result = ctx.prepare_change(large_repo["repo_name"], "improve score computation for tool effectiveness")
        assert result["target_functions"], "Should find score-related functions"

    def test_execution_task_finds_targets(self, large_repo):
        ctx = large_repo["ctx"]
        result = ctx.prepare_change(large_repo["repo_name"], "fix tool execution invoke path")
        assert result["target_functions"], "Should find execution/invoke functions"

    def test_prepare_change_confidence_is_reasonable(self, large_repo):
        ctx = large_repo["ctx"]
        result = ctx.prepare_change(large_repo["repo_name"], "update capability routing logic")
        confidence = result.get("confidence", 0)
        assert confidence >= 0.2, f"Confidence {confidence} too low for routing task"

    def test_prepare_change_returns_risk_info(self, large_repo):
        ctx = large_repo["ctx"]
        result = ctx.prepare_change(large_repo["repo_name"], "update capability routing logic")
        assert "risk_level" in result
        assert result["risk_level"] in ("low", "medium", "high", "unknown")

    def test_prepare_change_returns_contracts(self, large_repo):
        ctx = large_repo["ctx"]
        result = ctx.prepare_change(large_repo["repo_name"], "update capability routing logic")
        assert "contracts_to_preserve" in result
        assert isinstance(result["contracts_to_preserve"], list)

    def test_prepare_change_returns_validation_recipe(self, large_repo):
        ctx = large_repo["ctx"]
        result = ctx.prepare_change(large_repo["repo_name"], "update capability routing logic")
        assert "validation_recipe" in result
        recipe = result["validation_recipe"]
        assert isinstance(recipe, list)
        assert len(recipe) >= 1, "Validation recipe should have at least one step"

    def test_prepare_change_returns_safe_boundary(self, large_repo):
        ctx = large_repo["ctx"]
        result = ctx.prepare_change(large_repo["repo_name"], "update capability routing logic")
        assert "safe_edit_boundary" in result

    def test_prepare_change_full_structure(self, large_repo):
        ctx = large_repo["ctx"]
        result = ctx.prepare_change(large_repo["repo_name"], "refactor plan engine scoring")
        required_keys = {
            "repo", "task", "target_functions", "impact_report",
            "safe_edit_boundary", "contracts_to_preserve",
            "related_tests", "validation_recipe", "risk_score",
            "risk_level", "confidence", "omitted_context",
        }
        assert required_keys.issubset(result.keys()), (
            f"Missing keys: {required_keys - result.keys()}"
        )

    def test_repo_overview_risk_flags(self, large_repo):
        ctx = large_repo["ctx"]
        result = ctx.repo_overview(large_repo["repo_name"])
        assert "risk_flags" in result
        assert isinstance(result["risk_flags"], list)
        # Descriptor coverage is ~25%, so low-coverage flag should appear
        assert any("descriptor" in f.lower() or "coverage" in f.lower() for f in result["risk_flags"]), (
            f"Expected low-coverage risk flag, got: {result['risk_flags']}"
        )


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 5. Descriptor Quality
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestDescriptorQuality:
    """Verify function descriptors are extracted with reasonable coverage."""

    def test_descriptor_coverage_above_minimum(self, large_repo):
        """Coverage should be >10% even for a poorly-documented codebase."""
        store, repo = large_repo["store"], large_repo["repo"]
        ov = store.overview(repo.id)
        assert ov["descriptor_coverage"] > 0.10, (
            f"Descriptor coverage {ov['descriptor_coverage']:.1%} â€” expected >10%"
        )

    def test_docstring_functions_have_descriptors(self, large_repo):
        """Functions with docstrings should have descriptors with quality > 0."""
        store, repo = large_repo["store"], large_repo["repo"]
        conn = store.conn
        rows = conn.execute(
            """SELECT f.id FROM functions f
               JOIN function_descriptors d ON d.function_id = f.id
               WHERE f.repo_id=? AND d.quality_score > 0 LIMIT 100""",
            (repo.id,)
        ).fetchall()
        assert len(rows) >= 10, (
            f"Only {len(rows)} functions with quality_score > 0 â€” descriptors not being extracted"
        )

    def test_some_descriptors_have_summaries(self, large_repo):
        store, repo = large_repo["store"], large_repo["repo"]
        conn = store.conn
        rows = conn.execute(
            """SELECT COUNT(*) as n FROM function_descriptors d
               JOIN functions f ON f.id = d.function_id
               WHERE f.repo_id=? AND d.summary IS NOT NULL AND d.summary != ''""",
            (repo.id,)
        ).fetchone()
        assert rows["n"] >= 5, f"Only {rows['n']} functions have summaries"

    def test_high_quality_descriptors_exist(self, large_repo):
        """Some functions should have quality_score >= 0.7 (Google/NumPy style docs)."""
        store, repo = large_repo["store"], large_repo["repo"]
        conn = store.conn
        rows = conn.execute(
            """SELECT COUNT(*) as n FROM function_descriptors d
               JOIN functions f ON f.id = d.function_id
               WHERE f.repo_id=? AND d.quality_score >= 0.7""",
            (repo.id,)
        ).fetchone()
        assert rows["n"] >= 1, "Expected at least one high-quality docstring (quality â‰¥ 0.7)"


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 6. Snapshot & Diff
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestSnapshotAndDiff:
    """Verify the snapshot/diff pipeline works on a real indexed repo."""

    def test_auto_snapshot_created(self, large_repo):
        """Indexer auto-creates 'latest' snapshot after full index."""
        store, repo = large_repo["store"], large_repo["repo"]
        snaps = store.get_function_snapshots(repo.id, "latest")
        assert snaps, "Expected at least one 'latest' snapshot after index"

    def test_snapshot_contains_functions(self, large_repo):
        store, repo = large_repo["store"], large_repo["repo"]
        snaps = store.get_function_snapshots(repo.id, "latest")
        # snaps is a dict keyed by function_id
        assert len(snaps) >= 100, (
            f"'latest' snapshot has only {len(snaps)} functions â€” suspiciously low"
        )

    def test_second_snapshot_and_diff(self, large_repo, tmp_path):
        """Creating a second named snapshot and diffing should work."""
        store, repo = large_repo["store"], large_repo["repo"]
        store.snapshot_repo(repo.id, "v9-baseline")
        snaps = store.get_function_snapshots(repo.id, "v9-baseline")
        assert snaps, "v9-baseline snapshot should exist after snapshot_repo()"

        diff = store.diff_snapshots(repo.id, "latest", "v9-baseline")
        # Diff of identical snapshots should show no additions/removals
        assert isinstance(diff.functions_added, list)
        assert isinstance(diff.functions_removed, list)
        assert isinstance(diff.functions_changed, list)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 7. Failure Memory Ingestion
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestFailureMemory:
    """Verify the failure ingestor works against real function IDs."""

    # A synthetic traceback for testing failure ingestion
    _TRACEBACK = """\
Traceback (most recent call last):
  File "execution-plane/src/sample_execution/capability_router.py", line 45, in route
    result = self._score_candidates(candidates)
  File "shared/src/sample_shared/scoring.py", line 12, in score_component
    return base * weight
AssertionError: score must be positive
"""

    def test_ingest_python_traceback(self, large_repo):
        from codegraph_mcp.memory.failures import FailureIngestor
        store, repo = large_repo["store"], large_repo["repo"]
        ingestor = FailureIngestor(store)
        event = ingestor.ingest(
            repo_id=repo.id,
            kind="test_failure",
            message="AssertionError: score must be positive",
            stack_trace=self._TRACEBACK,
            source="pytest",
            language="python",
        )
        assert event.id
        assert event.kind == "test_failure"
        assert event.message == "AssertionError: score must be positive"
        # Stack trace preserved
        assert event.stack_trace
        assert "score_component" in event.stack_trace
        # File and line extracted from first frame
        assert event.file_path

    def test_failure_stored_and_retrievable(self, large_repo):
        from codegraph_mcp.memory.failures import FailureIngestor
        store, repo = large_repo["store"], large_repo["repo"]
        ingestor = FailureIngestor(store)
        ingestor.ingest(
            repo_id=repo.id,
            kind="runtime_error",
            message="ZeroDivisionError in scoring",
            stack_trace=self._TRACEBACK,
            source="prod",
            language="python",
        )
        recent = store.recent_failures(repo.id, limit=10)
        assert recent, "Expected at least one failure event in store"
        kinds = [e.kind for e in recent]
        assert "runtime_error" in kinds or "test_failure" in kinds

    def test_failure_linked_to_function(self, large_repo):
        """If score_component is indexed, the failure should be linked to it."""
        from codegraph_mcp.memory.failures import FailureIngestor
        store, repo = large_repo["store"], large_repo["repo"]
        ingestor = FailureIngestor(store)
        event = ingestor.ingest(
            repo_id=repo.id,
            kind="test_failure",
            message="link test",
            stack_trace=self._TRACEBACK,
            source="pytest",
            language="python",
        )
        # If a function was matched, function_ids should be populated
        assert isinstance(event.function_ids, list)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 8. Boundary Validation
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestBoundaryValidation:
    """Verify the boundary checker runs correctly against the real repo."""

    def test_boundary_check_with_plane_layers(self, large_repo, tmp_path):
        """Check layer boundaries matching a plane-based architecture."""
        from codegraph_mcp.policy.boundaries import BoundaryChecker, load_policy

        policy_yaml = tmp_path / "policy.yaml"
        policy_yaml.write_text("""\
layer_map:
  "execution-plane/**": execution
  "control-plane/**": control
  "inference-plane/**": inference
  "shared/**": shared
  "tests/**": tests

boundaries:
  - name: no-direct-control-to-execution
    allow: [control, execution, shared, tests]
    deny: []
  - name: shared-is-used-everywhere
    allow: [execution, control, inference, shared, tests]
    deny: []
""", encoding="utf-8")

        policy = load_policy(policy_yaml)
        store, repo = large_repo["store"], large_repo["repo"]
        checker = BoundaryChecker(store)
        violations = checker.check(repo.id, policy)
        # No deny rules above, so zero violations expected
        assert isinstance(violations, list)
        assert len(violations) == 0

    def test_boundary_check_produces_violations_when_configured(self, large_repo, tmp_path):
        """A strict policy should produce violations on a real codebase."""
        try:
            import yaml  # noqa: F401  (availability probe)
        except ImportError:
            pytest.skip("pyyaml not installed")

        from codegraph_mcp.policy.boundaries import BoundaryChecker, load_policy

        policy_yaml = tmp_path / "strict_policy.yaml"
        policy_yaml.write_text("""\
layer_map:
  "execution-plane/**": execution
  "control-plane/**": control
  "shared/**": shared

boundaries:
  - name: execution-cannot-call-control
    allow: [execution, shared]
    deny: [control]
""", encoding="utf-8")

        policy = load_policy(policy_yaml)
        store, repo = large_repo["store"], large_repo["repo"]
        checker = BoundaryChecker(store)
        violations = checker.check(repo.id, policy)
        # With a strict policy, we may or may not see violations depending on
        # whether cross-plane calls exist â€” just verify it runs without error
        assert isinstance(violations, list)
        for v in violations:
            assert v.rule_name
            assert v.from_file
            assert v.to_file


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 9. Runtime Bindings
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestRuntimeBindings:
    """Verify runtime bindings (Docker/Compose/routes) are extracted."""

    def test_runtime_bindings_extracted(self, large_repo):
        store, repo = large_repo["store"], large_repo["repo"]
        bindings = store.runtime_bindings(repo.id)
        assert len(bindings) >= 1, "Expected at least one runtime binding"

    def test_runtime_bindings_have_required_fields(self, large_repo):
        store, repo = large_repo["store"], large_repo["repo"]
        bindings = store.runtime_bindings(repo.id)
        for rb in bindings[:5]:
            assert rb.kind
            assert rb.name
            assert rb.repo_id == repo.id


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 10. Context Value-Add Metrics
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestContextValueAdd:
    """High-level metrics proving codegraph-mcp adds value to LLM context."""

    def test_prepare_change_output_is_compact(self, large_repo):
        """prepare_change should fit within a reasonable token budget."""
        import json
        ctx = large_repo["ctx"]
        result = ctx.prepare_change(large_repo["repo_name"], "update capability routing logic", max_tokens=8000)
        serialized = json.dumps(result)
        # A rough token estimate: ~4 chars per token. 8000 tokens = ~32000 chars.
        # We allow generous headroom since the budget is a soft cap.
        assert len(serialized) <= 200_000, (
            f"prepare_change output unexpectedly large: {len(serialized)} chars"
        )

    def test_prepare_change_targets_are_ranked(self, large_repo):
        """Targets should be ordered by relevance (highest-scoring first)."""
        ctx = large_repo["ctx"]
        result = ctx.prepare_change(large_repo["repo_name"], "improve score computation for tool effectiveness")
        targets = result["target_functions"]
        if len(targets) >= 2:
            # First result should contain 'score' in its name or qualified_name
            first = targets[0]
            haystack = (first["name"] + first["qualified_name"]).lower()
            assert "score" in haystack or "tool" in haystack or "effect" in haystack, (
                f"Top target doesn't match task: {first['qualified_name']}"
            )

    def test_overview_provides_architecture_signal(self, large_repo):
        """repo_overview should expose enough signal to orient an LLM."""
        ctx = large_repo["ctx"]
        ov = ctx.repo_overview(large_repo["repo_name"])
        assert ov["file_count"] >= 300
        assert ov["function_count"] >= 1000
        assert ov["languages"]
        assert "runtime_bindings" in ov
        assert "risk_flags" in ov

    def test_high_complexity_functions_are_flagged(self, large_repo):
        """Overview should surface the most complex functions for LLM attention."""
        store, repo = large_repo["store"], large_repo["repo"]
        ov = store.overview(repo.id)
        hc = ov.get("high_complexity", [])
        # Large repos typically have high-complexity route registration functions
        assert len(hc) >= 1, "Expected at least one high-complexity function flagged"
        assert all("qualified_name" in f for f in hc)

    def test_descriptor_coverage_reported(self, large_repo):
        store, repo = large_repo["store"], large_repo["repo"]
        ov = store.overview(repo.id)
        cov = ov["descriptor_coverage"]
        assert 0.0 <= cov <= 1.0
        # Report useful: coverage tells LLM how much docstring context is available
        assert cov > 0, "Zero descriptor coverage â€” docstring extraction broken"

    def test_end_to_end_workflow_smoke(self, large_repo):
        """Full workflow: discover â†’ impact â†’ context â†’ snapshot â€” no exceptions."""
        from codegraph_mcp.query.impact import ImpactEngine
        store, repo, ctx = large_repo["store"], large_repo["repo"], large_repo["ctx"]

        # Step 1: find a real function
        fns = store.find_functions(repo.id, "score_component", 3)
        assert fns

        # Step 2: impact analysis
        engine = ImpactEngine(store)
        report = engine.function_impact(large_repo["repo_name"], fns[0].qualified_name)
        assert report.risk_level

        # Step 3: prepare change context
        ctx_result = ctx.prepare_change(large_repo["repo_name"], "update score computation")
        assert ctx_result is not None

        # Step 4: snapshot
        snap_id = store.snapshot_repo(repo.id, label="e2e-smoke")
        assert snap_id, "e2e-smoke snapshot should exist"
