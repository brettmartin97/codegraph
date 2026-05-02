"""LLM-backed enricher supporting Anthropic, OpenAI-compatible, and custom HTTP backends."""
from __future__ import annotations

import json
import logging
import time

from codegraph_mcp.enrichment.base import BaseEnricher, EnrichmentResult
from codegraph_mcp.graph.models import ENRICHMENT_CATEGORIES

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a senior software architect analysing a codebase. For each function provided, output:
- purpose: one clear sentence describing what it does and why it exists
- category: exactly one of: routing, data-access, business-logic, utility, config, model,
             api-client, orchestration, testing, cli, ui, middleware, serialization,
             error-handling, security, background-job, other
- importance: float 0.0–1.0 (1 = core orchestration / entrypoint, 0 = trivial helper)
- tags: 2–5 lowercase hyphenated tags

Respond ONLY with a JSON array, one object per function, with keys:
function_id, purpose, category, importance (float), tags (array of strings).
"""


def _build_batch_prompt(batch: list[dict]) -> str:
    lines = []
    for item in batch:
        lines.append("---")
        lines.append(f"function_id: {item['function_id']}")
        lines.append(f"name: {item.get('qualified_name', item.get('name', '?'))}")
        if item.get("signature"):
            lines.append(f"signature: {item['signature'][:200]}")
        if item.get("summary"):
            lines.append(f"docstring: {item['summary'][:300]}")
        if item.get("decorators"):
            lines.append(f"decorators: {', '.join(item['decorators'][:5])}")
        lines.append(f"complexity: {item.get('complexity', 0)}  loc: {item.get('loc', 0)}")
    return "\n".join(lines)


def _parse_response(text: str, batch: list[dict]) -> list[EnrichmentResult]:
    """Extract JSON array from LLM response, return EnrichmentResult list."""
    text = text.strip()
    # Strip markdown fences
    if "```" in text:
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
        text = "\n".join(lines)
    # Find JSON array
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        log.warning("Could not find JSON array in LLM response")
        return []
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        log.warning("JSON parse error: %s", exc)
        return []
    results = []
    valid_cats = set(ENRICHMENT_CATEGORIES + ["background-job"])
    for item in parsed:
        if not isinstance(item, dict):
            continue
        fid = item.get("function_id", "")
        if not fid:
            continue
        cat = item.get("category", "other")
        if cat not in valid_cats:
            cat = "other"
        results.append(EnrichmentResult(
            function_id=fid,
            purpose=(item.get("purpose") or "")[:300],
            category=cat,
            importance=max(0.0, min(1.0, float(item.get("importance", 0.3)))),
            tags=[str(t)[:50] for t in (item.get("tags") or [])[:6]],
            source="",  # set by caller
        ))
    return results


class AnthropicEnricher(BaseEnricher):
    """Uses Anthropic API (claude-haiku for speed/cost)."""

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001", batch_size: int = 15):
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            raise ImportError("pip install anthropic  (or codegraph-mcp[llm-anthropic])")
        self.model = model
        self.batch_size = batch_size

    def enrich_batch(self, batch: list[dict]) -> list[EnrichmentResult]:
        prompt = _build_batch_prompt(batch)
        t0 = time.monotonic()
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text
        log.debug("Anthropic enrichment: %d fns in %.1fs", len(batch), time.monotonic() - t0)
        results = _parse_response(text, batch)
        for r in results:
            r.source = "anthropic"
        return results


class OpenAIEnricher(BaseEnricher):
    """OpenAI-compatible enricher. Works with OpenAI, vLLM, Ollama, Together, etc."""

    def __init__(self, api_key: str = "EMPTY", base_url: str = "https://api.openai.com/v1",
                 model: str = "gpt-4o-mini", batch_size: int = 15):
        try:
            import openai
            self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
        except ImportError:
            raise ImportError("pip install openai  (or codegraph-mcp[llm-openai])")
        self.model = model
        self.batch_size = batch_size

    def enrich_batch(self, batch: list[dict]) -> list[EnrichmentResult]:
        prompt = _build_batch_prompt(batch)
        t0 = time.monotonic()
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=2048,
            temperature=0.2,
        )
        text = resp.choices[0].message.content or ""
        log.debug("OpenAI enrichment: %d fns in %.1fs", len(batch), time.monotonic() - t0)
        results = _parse_response(text, batch)
        for r in results:
            r.source = "openai"
        return results


class CustomHttpEnricher(BaseEnricher):
    """Calls any /invoke-compatible HTTP endpoint. Zero extra deps (uses httpx).

    The endpoint should accept POST with JSON body:
        {"input": "<prompt>", "surface": "code", "strategy": "direct",
         "conversation_id": "<id>"}
    and return JSON with a "response" or "output" string field.
    """

    def __init__(self, url: str = "http://localhost:29000", batch_size: int = 12):
        try:
            import httpx
            self._client = httpx.Client(timeout=300)
        except ImportError:
            raise ImportError("pip install httpx")
        self.url = url.rstrip("/")
        self.batch_size = batch_size

    def enrich_batch(self, batch: list[dict]) -> list[EnrichmentResult]:
        prompt = SYSTEM_PROMPT + "\n\n" + _build_batch_prompt(batch)
        t0 = time.monotonic()
        resp = self._client.post(
            f"{self.url}/invoke",
            json={"input": prompt, "surface": "code", "strategy": "direct",
                  "conversation_id": "codegraph-enrichment"},
        )
        resp.raise_for_status()
        data = resp.json()
        text = data.get("response") or data.get("output") or ""
        log.debug("CustomHttp enrichment: %d fns in %.1fs", len(batch), time.monotonic() - t0)
        results = _parse_response(text, batch)
        for r in results:
            r.source = "custom_http"
        return results

    def close(self):
        self._client.close()
