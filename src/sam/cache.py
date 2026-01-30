"""Redis cache helpers (async)."""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as redis
from loguru import logger

from sam.config import get_settings

_redis: redis.Redis | None = None


def get_redis() -> redis.Redis | None:
    """Get (or create) a Redis client, or None if not configured."""
    global _redis
    settings = get_settings()
    if not settings.redis.url:
        return None
    if _redis is None:
        _redis = redis.from_url(settings.redis.url, decode_responses=True)  # type: ignore[no-untyped-call]
        logger.info("[redis] client initialized")
    return _redis


async def close_redis() -> None:
    """Close the Redis client (if any)."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
        logger.info("[redis] client closed")


async def cache_get_json(key: str) -> Any | None:
    r = get_redis()
    if r is None:
        return None
    val = await r.get(key)
    if val is None:
        return None
    try:
        return json.loads(val)
    except Exception:
        return None


async def cache_set_json(key: str, value: Any, *, ttl_seconds: int) -> None:
    r = get_redis()
    if r is None:
        return
    await r.set(key, json.dumps(value), ex=ttl_seconds)

