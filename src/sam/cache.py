"""Redis cache helpers (async)."""

from __future__ import annotations

import contextlib
import json
import threading
import time
from typing import Any

import redis.asyncio as redis
from loguru import logger

from sam.config import get_settings

_redis: redis.Redis | None = None
_redis_lock = threading.Lock()

# Rate-limit Redis-down warnings to avoid log spam (one warning per 5 min).
_REDIS_WARN_INTERVAL = 300  # seconds
_last_redis_warning: float = 0.0


def _warn_redis_error(operation: str, exc: Exception) -> None:
    """Log a Redis failure warning, rate-limited to avoid flooding."""
    global _last_redis_warning
    now = time.monotonic()
    if now - _last_redis_warning >= _REDIS_WARN_INTERVAL:
        _last_redis_warning = now
        logger.warning(f"[redis] {operation} failed — Redis may be unavailable: {exc}")


def get_redis() -> redis.Redis | None:
    """Get (or create) a Redis client, or None if not configured."""
    global _redis
    settings = get_settings()
    if not settings.redis.url:
        return None
    if _redis is None:
        with _redis_lock:
            if _redis is None:
                _redis = redis.from_url(  # type: ignore[no-untyped-call,unused-ignore]
                    settings.redis.url,
                    decode_responses=True,
                )
                logger.info("[redis] client initialized")
    return _redis


async def close_redis() -> None:
    """Close the Redis client (if any)."""
    global _redis
    if _redis is not None:
        try:
            await _redis.aclose()
            logger.info("[redis] client closed")
        except Exception as exc:
            logger.debug(f"[redis] close failed (likely event loop teardown): {exc}")
        finally:
            _redis = None


async def cache_get_json(key: str) -> Any | None:
    r = get_redis()
    if r is None:
        return None
    try:
        val = await r.get(key)
    except Exception as exc:
        _warn_redis_error("cache_get_json", exc)
        return None
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
    try:
        await r.set(key, json.dumps(value), ex=ttl_seconds)
    except Exception as exc:
        _warn_redis_error("cache_set_json", exc)


# ---------------------------------------------------------------------------
# Collector toggles (shared between API and scheduler via Redis)
# ---------------------------------------------------------------------------

_COLLECTOR_TOGGLE_PREFIX = "sam:collector:"
_ALERTS_CHANNEL = "sam:alerts"
_METRICS_CHANNEL = "sam:metrics"


async def collector_toggle_set(platform: str, enabled: bool) -> None:
    """Persist a collector enabled override in Redis."""
    r = get_redis()
    if r is None:
        return
    key = f"{_COLLECTOR_TOGGLE_PREFIX}{platform}:enabled"
    try:
        await r.set(key, json.dumps(enabled))
    except Exception as exc:
        _warn_redis_error("collector_toggle_set", exc)


async def collector_toggle_get(platform: str) -> bool | None:
    """Read a collector enabled override from Redis.

    Returns ``None`` if no override exists (caller should fall back to env var).
    """
    r = get_redis()
    if r is None:
        return None
    key = f"{_COLLECTOR_TOGGLE_PREFIX}{platform}:enabled"
    try:
        val = await r.get(key)
    except Exception as exc:
        _warn_redis_error("collector_toggle_get", exc)
        return None
    if val is None:
        return None
    try:
        return bool(json.loads(val))
    except Exception:
        return None


def collector_toggle_get_sync(platform: str) -> bool | None:
    """Synchronous version of :func:`collector_toggle_get` for CLI use.

    Opens a short-lived sync Redis connection, reads the override key,
    and returns ``True``/``False`` or ``None`` if no override exists.
    """
    import redis as sync_redis

    settings = get_settings()
    if not settings.redis.url:
        return None
    try:
        r = sync_redis.from_url(  # type: ignore[no-untyped-call,unused-ignore]
            settings.redis.url,
            decode_responses=True,
        )
        key = f"{_COLLECTOR_TOGGLE_PREFIX}{platform}:enabled"
        val = r.get(key)
        r.close()
        if val is None:
            return None
        return bool(json.loads(str(val)))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Alerts pub/sub (cross-process real-time fanout)
# ---------------------------------------------------------------------------


def alerts_channel() -> str:
    """Return the Redis channel used for alert fanout."""
    return _ALERTS_CHANNEL


def metrics_channel() -> str:
    """Return the Redis channel used for metrics fanout."""
    return _METRICS_CHANNEL


async def publish_alert_event(alert: dict[str, Any]) -> bool:
    """Publish an alert event for API/websocket relay processes."""
    r = get_redis()
    if r is None:
        return False
    try:
        await r.publish(_ALERTS_CHANNEL, json.dumps(alert))
        return True
    except Exception as exc:
        _warn_redis_error("publish_alert_event", exc)
        return False


async def publish_metrics_event(payload: dict[str, Any]) -> bool:
    """Publish a metrics update for API/websocket relay processes."""
    r = get_redis()
    if r is None:
        return False
    try:
        await r.publish(_METRICS_CHANNEL, json.dumps(payload))
        return True
    except Exception as exc:
        _warn_redis_error("publish_metrics_event", exc)
        return False


_SYSTEM_HEALTH_THROTTLE_PREFIX = "sam:system-health:broadcast:"
_SYSTEM_HEALTH_THROTTLE_SECONDS = 900  # 15 minutes per alert type


async def publish_system_health_throttled(alert: dict[str, Any]) -> bool:
    """Publish system health alert only if not recently broadcast for this type.

    Throttles by alert_type to avoid spamming WebSocket clients every cycle
    when the same issue persists (e.g. Redis degraded, no ingest).
    """
    alert_type = alert.get("alert_type") or alert.get("id", "unknown")
    if isinstance(alert_type, str) and alert_type.startswith("system-"):
        alert_type = alert_type.replace("system-", "")
    key = f"{_SYSTEM_HEALTH_THROTTLE_PREFIX}{alert_type}"
    r = get_redis()
    if r is None:
        return await publish_alert_event(alert)
    try:
        acquired = await r.set(key, "1", ex=_SYSTEM_HEALTH_THROTTLE_SECONDS, nx=True)
        if not acquired:
            return False  # Recently broadcast, skip
        published = await publish_alert_event(alert)
        if published:
            return True
        with contextlib.suppress(Exception):
            await r.delete(key)
        return False
    except Exception as exc:
        _warn_redis_error("publish_system_health_throttled", exc)
        with contextlib.suppress(Exception):
            await r.delete(key)
        return await publish_alert_event(alert)
