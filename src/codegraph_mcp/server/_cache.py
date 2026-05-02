"""Simple in-process result cache for read-only MCP tools.

Keyed by (tool_name, args_hash). Invalidated on any index write.
Thread-safe for the asyncio single-thread model FastMCP uses.
"""
from __future__ import annotations

import hashlib
import json

_CACHE: dict[str, str] = {}
_MAX = 256


def cache_key(tool: str, **kwargs: object) -> str:
    payload = json.dumps({"_t": tool, **kwargs}, sort_keys=True, default=str)
    return hashlib.md5(payload.encode()).hexdigest()


def cache_get(key: str) -> str | None:
    return _CACHE.get(key)


def cache_set(key: str, value: str) -> str:
    if len(_CACHE) >= _MAX:
        for k in list(_CACHE.keys())[: _MAX // 4]:
            del _CACHE[k]
    _CACHE[key] = value
    return value


def cache_invalidate() -> None:
    _CACHE.clear()


def cache_size() -> int:
    return len(_CACHE)
