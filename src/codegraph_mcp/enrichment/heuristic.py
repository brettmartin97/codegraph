"""Heuristic enricher — no LLM, rule-based. Fast mode.

Infers category and tags from:
- Decorator patterns (FastAPI, Flask, Celery, Click, pytest)
- Naming conventions (test_*, *_manager, *_dao, *_handler)
- Import signals stored in function signature / qualified name
- Complexity thresholds for importance estimation
"""
from __future__ import annotations

import re

from codegraph_mcp.enrichment.base import BaseEnricher, EnrichmentResult

_ROUTING = re.compile(
    r"@(app|router|blueprint|bp)\.(get|post|put|patch|delete|route|websocket|head)\b"
    r"|@require_http_methods|@csrf_exempt", re.I
)
_DATA_ACCESS = re.compile(
    r"session\.(query|execute|add|delete|commit|flush)\b"
    r"|\.filter\(|\.filter_by\(|SELECT|INSERT|UPDATE|DELETE"
    r"|cursor\.execute|\.fetchone\b|\.fetchall\b"
    r"|Repository|Dao|\.save\(|\.find\b|\.find_by\b", re.I
)
_BACKGROUND = re.compile(r"@.*\.task\b|@shared_task|@celery|@dramatiq|@huey\b", re.I)
_CLI = re.compile(r"@click\.|@typer\.|@app\.command\b|@cli\.command\b", re.I)
_SERIALIZATION = re.compile(r"serialize|deserialize|to_dict|from_dict|to_json|from_json|marshal|unmarshal|schema", re.I)
_SECURITY = re.compile(r"auth|login|logout|password|token|jwt|session|permission|csrf|encrypt|decrypt|hash_|verify_", re.I)
_ERROR = re.compile(r"except\b|raise\b|Error|Exception|Fault|handle_error|on_error", re.I)
_MIDDLEWARE = re.compile(r"middleware|before_request|after_request|on_event|lifespan|dispatch\b", re.I)
_MODEL = re.compile(r"class.*\bModel\b|class.*\bSchema\b|class.*\bEntity\b|BaseModel|DeclarativeBase|TypedDict", re.I)
_CONFIG = re.compile(r"settings|config|configure|setup|initialize|init_app|load_env|getenv", re.I)
_ORCHESTRATION = re.compile(r"workflow|pipeline|orchestrat|coordinator|runner|executor|scheduler|agentic|loop\b", re.I)
_TESTING = re.compile(r"^test_|assert|mock|fixture|patch|capfd|monkeypatch", re.I)
_UI = re.compile(r"render|template|jinja|html|css|component|widget|dashboard|chart", re.I)


def _infer_category(item: dict) -> str:
    name = item.get("name", "")
    item.get("qualified_name", "")
    sig = item.get("signature", "") or ""
    decorators = " ".join(item.get("decorators", []))
    summary = item.get("summary", "") or ""
    body_hint = decorators + " " + sig + " " + summary

    if _TESTING.match(name):
        return "testing"
    if _ROUTING.search(decorators):
        return "routing"
    if _BACKGROUND.search(decorators):
        return "background-job"
    if _CLI.search(decorators):
        return "cli"
    if _MIDDLEWARE.search(name + body_hint):
        return "middleware"
    if _ORCHESTRATION.search(name + body_hint):
        return "orchestration"
    if _SECURITY.search(name):
        return "security"
    if _SERIALIZATION.search(name):
        return "serialization"
    if _DATA_ACCESS.search(body_hint + name):
        return "data-access"
    if _CONFIG.search(name):
        return "config"
    if _ERROR.search(name):
        return "error-handling"
    if _UI.search(name + body_hint):
        return "ui"
    if re.search(r"manager|service|handler|processor|coordinator", name, re.I):
        return "business-logic"
    if re.search(r"util|helper|tool|format|parse|convert|transform|clean|normalize", name, re.I):
        return "utility"
    if re.search(r"api|client|request|response|http|fetch|call_", name, re.I):
        return "api-client"
    return "other"


def _infer_tags(item: dict) -> list[str]:
    tags = set()
    name = item.get("name", "")
    sig = item.get("signature", "") or ""
    decorators = item.get("decorators", [])

    if item.get("is_async"):
        tags.add("async")
    if item.get("complexity", 0) >= 10:
        tags.add("high-complexity")
    elif item.get("complexity", 0) >= 5:
        tags.add("moderate-complexity")
    if item.get("is_test"):
        tags.add("test")
    if "self" in sig:
        tags.add("instance-method")
    if any("property" in d for d in decorators):
        tags.add("property")
    if any("classmethod" in d or "staticmethod" in d for d in decorators):
        tags.add("class-method")
    if re.search(r"__init__|__new__|__call__|__repr__|__str__", name):
        tags.add("dunder")
    if re.search(r"@.*route|@.*get|@.*post", " ".join(decorators), re.I):
        tags.add("http-endpoint")
    return sorted(tags)[:6]


def _infer_importance(item: dict) -> float:
    """0–1 heuristic importance: high complexity + many callers = more central."""
    score = 0.1
    complexity = item.get("complexity") or 0
    if complexity >= 15:
        score += 0.4
    elif complexity >= 8:
        score += 0.25
    elif complexity >= 4:
        score += 0.1
    loc = item.get("loc") or 0
    if loc >= 80:
        score += 0.2
    elif loc >= 30:
        score += 0.1
    name = item.get("name", "")
    if re.search(r"main|run|start|execute|invoke|dispatch|handle|process", name, re.I):
        score += 0.2
    if item.get("is_test"):
        score -= 0.1
    return min(1.0, max(0.0, round(score, 2)))


def _infer_purpose(item: dict) -> str:
    summary = item.get("summary")
    if summary and len(summary) > 10:
        return summary[:200]
    name = item.get("name", "")
    sig = item.get("signature", "") or ""
    readable = re.sub(r"([a-z])([A-Z])", r"\1 \2", name).replace("_", " ").lower()
    return f"{readable.capitalize()} ({sig[:80]})" if sig else readable.capitalize()


class HeuristicEnricher(BaseEnricher):
    """Rule-based enricher. Zero LLM calls. ~0.1ms per function."""

    def enrich_batch(self, batch: list[dict]) -> list[EnrichmentResult]:
        results = []
        for item in batch:
            results.append(EnrichmentResult(
                function_id=item["function_id"],
                purpose=_infer_purpose(item),
                category=_infer_category(item),
                importance=_infer_importance(item),
                tags=_infer_tags(item),
                source="heuristic",
            ))
        return results
