"""In-memory SQL & schema caching (Solution 2).

Two APIs over the same store: `get_sql_cache`/`set_sql_cache` for raw dicts (used
where one tool reaches into another tool's cache entry), and
`get_cached_model`/`set_cached_model` for the Pydantic results the logic layer
actually produces.
"""

from __future__ import annotations

import time
from typing import Any, TypeVar

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger("superset_mcp")

from superset_mcp.config import SQL_CACHE_TTL
from superset_mcp.models.base import CachedResult

_sql_cache: dict[str, tuple[float, dict[str, Any]]] = {}

TResult = TypeVar("TResult", bound=CachedResult)


def check_db_modified_and_invalidate() -> None:
    """In-memory cache invalidation handled via explicit write tool calls and TTL."""
    pass


def get_sql_cache(key: str) -> dict[str, Any] | None:
    check_db_modified_and_invalidate()
    now = time.time()
    if key in _sql_cache:
        ts, cached_data = _sql_cache[key]
        if now - ts < SQL_CACHE_TTL:
            res = dict(cached_data)
            res["cached"] = True
            try:
                logger.opt(colors=True).info("<yellow>[RAM CACHE HIT]</yellow> key='<cyan>{}</cyan>'", key[:120])
            except Exception:
                logger.info("[RAM CACHE HIT] key='{}'", key[:120])
            return res
        else:
            _sql_cache.pop(key, None)
    return None


def set_sql_cache(key: str, data: dict[str, Any]) -> None:
    if len(_sql_cache) > 500:
        _sql_cache.clear()
    _sql_cache[key] = (time.time(), data)


def invalidate_sql_cache(*keys: str) -> None:
    """Drops specific cached reads a write tool just made stale.

    Without this, e.g. update_chart could save a change and then get_chart on the
    same chart_id would still return the pre-update config for up to
    SQL_CACHE_TTL seconds - the assistant reporting its own edit as unchanged.
    """
    for key in keys:
        _sql_cache.pop(key, None)


def clear_sql_cache() -> None:
    _sql_cache.clear()


def get_cached_model(key: str, model_cls: type[TResult]) -> TResult | None:
    """Cache hit rebuilt as its Result model, with `cached` already flipped to True."""
    cached = get_sql_cache(key)
    if cached is None:
        return None
    return model_cls.model_validate(cached)


def set_cached_model(key: str, result: CachedResult) -> None:
    set_sql_cache(key, result.model_dump())
