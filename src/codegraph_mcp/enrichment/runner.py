"""Enrichment runner — fetches unenriched functions, batches to enricher, stores results."""
from __future__ import annotations

import logging

from codegraph_mcp.enrichment.base import BaseEnricher
from codegraph_mcp.graph.sqlite_store import SQLiteStore

log = logging.getLogger(__name__)


def _fn_to_dict(fn) -> dict:
    """Convert FunctionNode to enricher-friendly dict."""
    desc = fn.descriptor
    return {
        "function_id": fn.id,
        "name": fn.name,
        "qualified_name": fn.qualified_name,
        "signature": fn.signature,
        "summary": desc.summary if desc else None,
        "docstring": desc.raw if desc else None,
        "complexity": fn.complexity or 0,
        "loc": fn.loc or 0,
        "decorators": fn.decorators or [],
        "is_async": fn.is_async,
        "is_test": fn.is_test,
    }


def run_enrichment(
    store: SQLiteStore,
    repo_id: str,
    enricher: BaseEnricher,
    batch_size: int = 15,
    max_functions: int | None = None,
    force: bool = False,
) -> dict:
    """
    Run enrichment on a repo.

    Args:
        store: SQLiteStore instance
        repo_id: repository ID
        enricher: enricher backend (HeuristicEnricher, AnthropicEnricher, etc.)
        batch_size: functions per LLM call
        max_functions: cap total functions to enrich (None = all)
        force: re-enrich even if already enriched

    Returns: stats dict
    """
    if force:
        store.conn.execute(
            "UPDATE function_descriptors SET enrichment_source='none', enriched_at=NULL "
            "WHERE function_id IN (SELECT id FROM functions WHERE repo_id=?)",
            (repo_id,),
        )
        store.conn.commit()

    fns = store.functions_needing_enrichment(repo_id, limit=max_functions or 10_000)
    if not fns:
        log.info("No functions need enrichment (repo_id=%s)", repo_id)
        return {"enriched": 0, "skipped": 0, "errors": 0, "total_queried": 0}

    if max_functions:
        fns = fns[:max_functions]

    log.info("Enriching %d functions in batches of %d", len(fns), batch_size)
    enriched_count = 0
    error_count = 0
    all_results = []

    for i in range(0, len(fns), batch_size):
        batch_fns = fns[i: i + batch_size]
        batch_dicts = [_fn_to_dict(fn) for fn in batch_fns]
        try:
            results = enricher.enrich_batch(batch_dicts)
            rows = [
                {
                    "function_id": r.function_id,
                    "purpose": r.purpose,
                    "category": r.category,
                    "importance": r.importance,
                    "tags": r.tags,
                    "source": r.source,
                }
                for r in results
            ]
            store.update_enrichment_bulk(rows)
            enriched_count += len(results)
            all_results.extend(results)
            log.debug("Batch %d/%d: enriched %d", i // batch_size + 1,
                      (len(fns) + batch_size - 1) // batch_size, len(results))
        except Exception as exc:
            log.warning("Enrichment batch %d failed: %s", i // batch_size, exc)
            error_count += batch_size

    # Rebuild FTS for repo
    try:
        fts_count = store.rebuild_fts_for_repo(repo_id)
        log.info("FTS rebuilt: %d entries", fts_count)
    except Exception as exc:
        log.warning("FTS rebuild failed: %s", exc)

    return {
        "enriched": enriched_count,
        "errors": error_count,
        "skipped": len(fns) - enriched_count - error_count,
        "total_queried": len(fns),
    }


def make_enricher(backend: str, **kwargs) -> BaseEnricher:
    """
    Factory. backend: "heuristic" | "anthropic" | "openai" | "custom_http"

    kwargs for anthropic: api_key, model
    kwargs for openai: api_key, base_url, model
    kwargs for custom_http: url
    """
    if backend == "heuristic":
        from codegraph_mcp.enrichment.heuristic import HeuristicEnricher
        return HeuristicEnricher()
    elif backend == "anthropic":
        from codegraph_mcp.enrichment.llm_enricher import AnthropicEnricher
        return AnthropicEnricher(**kwargs)
    elif backend in ("openai", "vllm", "ollama"):
        from codegraph_mcp.enrichment.llm_enricher import OpenAIEnricher
        return OpenAIEnricher(**kwargs)
    elif backend in ("custom_http", "exodia"):  # exodia kept as undocumented alias
        from codegraph_mcp.enrichment.llm_enricher import CustomHttpEnricher
        return CustomHttpEnricher(**kwargs)
    else:
        raise ValueError(f"Unknown enrichment backend: {backend!r}. "
                         f"Choose from: heuristic, anthropic, openai, vllm, ollama, custom_http")
