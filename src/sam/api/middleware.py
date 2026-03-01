"""HTTP middleware components for the SAM API (rate-limiting, auth)."""

from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque
from typing import Any, Protocol

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.requests import Request

from sam.api.schemas import ErrorResponse
from sam.config import get_settings

# ---------------------------------------------------------------------------
# Rate limiter interface
# ---------------------------------------------------------------------------


class RateLimiterBackend(Protocol):
    """Minimal interface shared by in-memory and Redis backends."""

    requests_per_minute: int

    def is_allowed(self, key: str) -> tuple[bool, int]: ...
    def get_retry_after(self, key: str) -> int: ...


# ---------------------------------------------------------------------------
# In-memory sliding window (single-worker / development)
# ---------------------------------------------------------------------------


class RateLimiter:
    """
    Simple in-memory rate limiter using sliding window.

    Thread-safe for async usage (single-threaded event loop).
    For production with multiple workers, use :class:`RedisRateLimiter`.

    Uses ``collections.deque`` for O(1) amortised cleanup (timestamps are
    always appended in order, so expired entries are always at the left).
    Periodically evicts keys with empty deques to prevent unbounded memory
    growth from unique IPs that stop making requests.
    """

    _EVICT_EVERY = 1000  # run full eviction every N ``is_allowed`` calls

    def __init__(self, requests_per_minute: int = 60, window_seconds: int = 60):
        self.requests_per_minute = requests_per_minute
        self.window_seconds = window_seconds
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._call_count = 0

    def _cleanup_old(self, key: str, now: float) -> None:
        cutoff = now - self.window_seconds
        dq = self._requests[key]
        while dq and dq[0] <= cutoff:
            dq.popleft()

    def _maybe_evict(self) -> None:
        """Remove keys whose deques are empty to bound memory."""
        self._call_count += 1
        if self._call_count >= self._EVICT_EVERY:
            self._call_count = 0
            empty_keys = [k for k, dq in self._requests.items() if not dq]
            for k in empty_keys:
                del self._requests[k]

    def is_allowed(self, key: str) -> tuple[bool, int]:
        """Check if request is allowed. Returns (allowed, remaining)."""
        now = time.time()
        self._cleanup_old(key, now)
        self._maybe_evict()

        count = len(self._requests[key])
        remaining = max(0, self.requests_per_minute - count)

        if count >= self.requests_per_minute:
            return False, remaining

        self._requests[key].append(now)
        return True, remaining - 1

    def get_retry_after(self, key: str) -> int:
        """Get seconds until oldest request expires."""
        dq = self._requests.get(key)
        if not dq:
            return 0
        oldest = dq[0]
        return max(1, int(self.window_seconds - (time.time() - oldest)))


# ---------------------------------------------------------------------------
# Redis-backed sliding window (multi-worker / production)
# ---------------------------------------------------------------------------


class RedisRateLimiter:
    """Distributed rate limiter backed by Redis sorted sets.

    Each client IP gets a sorted set keyed ``sam:ratelimit:<ip>`` where
    members are unique request IDs (timestamps with microseconds) scored by
    their timestamp.  Expired members are pruned on each call via
    ``ZREMRANGEBYSCORE``.

    Falls back to an embedded :class:`RateLimiter` if the Redis call fails
    so that a transient outage does not cause a hard error.
    """

    def __init__(
        self,
        requests_per_minute: int = 60,
        window_seconds: int = 60,
    ):
        self.requests_per_minute = requests_per_minute
        self.window_seconds = window_seconds
        self._fallback = RateLimiter(requests_per_minute, window_seconds)
        self._redis: Any = None

    def _get_redis(self) -> Any:
        """Lazy import to avoid hard dep when Redis is not configured."""
        if self._redis is None:
            try:
                import redis as _redis_mod

                settings = get_settings()
                self._redis = _redis_mod.from_url(
                    settings.redis.url,
                    decode_responses=True,
                    socket_connect_timeout=1,
                    socket_timeout=1,
                )
                # Quick connectivity check
                self._redis.ping()
            except Exception as exc:
                logger.debug(f"[rate-limit] Redis unavailable, using in-memory fallback: {exc}")
                self._redis = None
        return self._redis

    def is_allowed(self, key: str) -> tuple[bool, int]:
        """Check if request is allowed, using Redis if reachable."""
        r = self._get_redis()
        if r is None:
            return self._fallback.is_allowed(key)

        try:
            return self._is_allowed_redis(r, key)
        except Exception as exc:
            logger.debug(f"[rate-limit] Redis error, falling back to in-memory: {exc}")
            self._redis = None  # reset for next attempt
            return self._fallback.is_allowed(key)

    def _is_allowed_redis(self, r: Any, key: str) -> tuple[bool, int]:
        redis_key = f"sam:ratelimit:{key}"
        now = time.time()
        cutoff = now - self.window_seconds

        pipe = r.pipeline(transaction=True)
        pipe.zremrangebyscore(redis_key, "-inf", cutoff)
        pipe.zcard(redis_key)
        pipe.zadd(redis_key, {f"{now}": now})
        pipe.expire(redis_key, self.window_seconds + 1)
        results = pipe.execute()

        count = results[1]  # zcard result before add
        remaining = max(0, self.requests_per_minute - count)

        if count >= self.requests_per_minute:
            # Remove the member we just optimistically added
            r.zrem(redis_key, f"{now}")
            return False, 0

        return True, remaining - 1

    def get_retry_after(self, key: str) -> int:
        """Get seconds until the oldest request in the window expires."""
        r = self._get_redis()
        if r is None:
            return self._fallback.get_retry_after(key)

        try:
            redis_key = f"sam:ratelimit:{key}"
            oldest = r.zrange(redis_key, 0, 0, withscores=True)
            if not oldest:
                return 0
            return max(1, int(self.window_seconds - (time.time() - oldest[0][1])))
        except Exception:
            return self._fallback.get_retry_after(key)


# ---------------------------------------------------------------------------
# Module-level singleton — auto-selects backend based on config
# ---------------------------------------------------------------------------


def _build_rate_limiter() -> RateLimiterBackend:
    """Create the best available rate limiter backend."""
    settings = get_settings()
    if settings.redis.url:
        logger.info("[rate-limit] Using Redis-backed distributed rate limiter")
        return RedisRateLimiter(requests_per_minute=120)
    logger.info("[rate-limit] Using in-memory rate limiter (single-worker only)")
    return RateLimiter(requests_per_minute=120)


# Lazy initialisation — created on first middleware hit.
_rate_limiter: RateLimiterBackend | None = None


def _get_rate_limiter() -> RateLimiterBackend:
    global _rate_limiter  # noqa: PLW0603
    if _rate_limiter is None:
        _rate_limiter = _build_rate_limiter()
    return _rate_limiter


# ---------------------------------------------------------------------------
# API Key Auth helpers
# ---------------------------------------------------------------------------

# Also protect any PUT / POST / DELETE that is not a health-check or
# an alert acknowledgment (which only changes per-alert state).
_AUTH_REQUIRED_METHODS = {"PUT", "DELETE"}


def _path_requires_auth(method: str, path: str) -> bool:
    """Check if a request path + method combination requires API key auth."""
    # Explicit path matches (normalized)
    if path.startswith("/api/v1/collectors/") and path.endswith("/toggle"):
        return True
    if path == "/api/v1/alerts/run-detection":
        return True
    # Alert acknowledge is a mutation (POST)
    if path.startswith("/api/v1/alerts/") and path.endswith("/acknowledge"):
        return True
    # Sentiment analyze is expensive — protect against abuse.
    if path == "/api/v1/sentiment/analyze":
        return True
    # PUT/DELETE on any API path
    return method in _AUTH_REQUIRED_METHODS and path.startswith("/api/")


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------


def register_middleware(app: FastAPI) -> None:
    """Attach rate-limiting and API-key-auth middleware to *app*.

    Call this **after** CORS middleware has been added so that the execution
    order is: CORS → rate-limit → auth → route handler.
    """

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next: Any) -> Any:
        """Apply rate limiting to API endpoints (skip health checks)."""
        if request.url.path in ("/health", "/api/v1/pipeline/health", "/metrics"):
            return await call_next(request)

        client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if not client_ip:
            client_ip = request.client.host if request.client else "unknown"

        limiter = _get_rate_limiter()
        allowed, remaining = limiter.is_allowed(client_ip)

        if not allowed:
            retry_after = limiter.get_retry_after(client_ip)
            return JSONResponse(
                status_code=429,
                content=ErrorResponse(
                    error="rate_limit_exceeded",
                    detail=f"Too many requests. Retry after {retry_after} seconds.",
                ).model_dump(),
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limiter.requests_per_minute),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limiter.requests_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response

    @app.middleware("http")
    async def api_key_auth_middleware(request: Request, call_next: Any) -> Any:
        """Enforce API key on mutation/admin endpoints when SAM_API_KEY is set."""
        settings = get_settings()
        api_key = settings.api_key
        if not api_key:
            return await call_next(request)

        if not _path_requires_auth(request.method, request.url.path):
            return await call_next(request)

        provided_key = ""
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            provided_key = auth_header[7:].strip()
        if not provided_key:
            provided_key = request.headers.get("X-API-Key", "").strip()

        if not provided_key or not hmac.compare_digest(provided_key, api_key):
            return JSONResponse(
                status_code=401,
                content=ErrorResponse(
                    error="unauthorized",
                    detail="Valid API key required for this endpoint.",
                ).model_dump(),
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)
