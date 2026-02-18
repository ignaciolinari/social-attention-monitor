"""
FastAPI Application

Main API server for Social Attention Monitor.
"""

import asyncio
import contextlib
import json
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.status import HTTP_422_UNPROCESSABLE_CONTENT
from starlette.websockets import WebSocketState

from sam import __version__
from sam.alerts import (
    AlertManager,
    acknowledge_alert,
    count_alerts,
    count_unacknowledged_alerts,
    get_alert_counts_by_severity,
    get_recent_alerts,
    get_unacknowledged_count,
)
from sam.cache import cache_get_json, cache_set_json, close_redis, get_redis
from sam.collectors.base import BaseCollector
from sam.collectors.bluesky import BlueskyCollector
from sam.collectors.reddit import RedditCollector
from sam.collectors.tmdb import TMDBCollector
from sam.collectors.youtube import YouTubeCollector
from sam.config import get_settings
from sam.logging import setup_logging
from sam.processors.sentiment import (
    SentimentResult,
    analyze_sentiment,
    analyze_sentiment_batch_with_translation,
)
from sam.quota import aggregate_youtube_quota_from_db
from sam.storage.database import get_session
from sam.storage.models import Title as TitleModel
from sam.storage.repository import (
    escape_like,
    get_latest_mention_collected_at,
    get_mentions_count,
    get_mentions_for_title,
    get_metrics_timeseries,
    get_pipeline_health_stats,
    get_title_by_id,
    get_title_by_name,
    get_trending_by_attention_index,
    insert_mentions,
    list_titles,
)


# Pydantic schemas
class ErrorResponse(BaseModel):
    """Consistent API error schema."""

    error: str
    detail: Any | None = None


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    timestamp: str
    demo_mode: bool
    reddit_configured: bool
    youtube_configured: bool
    tmdb_configured: bool
    bluesky_configured: bool
    reddit_enabled: bool = False
    youtube_enabled: bool = True
    bluesky_enabled: bool = True
    youtube_reachable: bool | None = None
    tmdb_reachable: bool | None = None
    bluesky_reachable: bool | None = None
    database_ok: bool
    redis_ok: bool


class PipelineRunInfo(BaseModel):
    """Pipeline run information."""

    job_name: str
    status: str
    started_at: str | None
    finished_at: str | None
    error: str | None
    stats: dict[str, Any] = Field(default_factory=dict)


class PipelineSentimentStats(BaseModel):
    """Sentiment/translation observability stats from latest collector run."""

    translate_attempted: int = 0
    translate_count: int = 0
    translate_failures: int = 0
    translate_skipped_english: int = 0
    sentiment_ms_total: float = 0.0


class ApiQuotaInfo(BaseModel):
    """API quota usage for a single platform."""

    date: str
    total_units: int
    total_calls: int
    calls_by_endpoint: dict[str, int]
    daily_budget: int | None = None
    budget_used_pct: float | None = None
    budget_remaining: int | None = None


class PipelineQuotaResponse(BaseModel):
    """API quota usage summary across all platforms."""

    youtube: ApiQuotaInfo
    last_run_at: str | None = None


class PipelineHealthResponse(BaseModel):
    """Pipeline health statistics."""

    timestamp: str
    active_titles: int
    total_mentions: int
    mentions_last_24h: dict[str, int]
    newest_mention_age_seconds: dict[str, float | None]
    newest_mention_at: dict[str, str | None]
    latest_pipeline_runs: list[PipelineRunInfo]
    sentiment_stats: PipelineSentimentStats | None = None
    api_quota: dict[str, ApiQuotaInfo] = Field(default_factory=dict)


class TitleResponse(BaseModel):
    """Title metadata response."""

    tmdb_id: int
    title: str
    media_type: str
    release_date: str | None
    overview: str
    popularity: float
    vote_average: float


class MentionResponse(BaseModel):
    """Social media mention response."""

    platform: str
    source_id: str
    content: str
    author: str | None
    url: str | None
    created_at: str
    metrics: dict[str, Any]
    sentiment: dict[str, Any] | None


class TrendingResponse(BaseModel):
    """Trending titles response."""

    titles: list[TitleResponse]
    collected_at: str


class MentionsResponse(BaseModel):
    """Mentions collection response."""

    title: str
    platform: str
    mentions: list[MentionResponse]
    total_count: int
    next_offset: int | None = None
    collected_at: str


class DbTitleResponse(BaseModel):
    """Title from our DB."""

    id: str
    tmdb_id: int
    title: str
    media_type: str
    release_date: str | None
    popularity: float | None


class TitlesResponse(BaseModel):
    titles: list[DbTitleResponse]
    total_count: int
    next_offset: int | None = None


class MetricsSnapshotResponse(BaseModel):
    title_id: str
    snapshot_time: str
    window_hours: int

    mention_count: int
    unique_authors: int
    reddit_mentions: int
    youtube_mentions: int
    bluesky_mentions: int = 0

    mention_velocity: float | None
    velocity_change: float | None

    avg_sentiment: float | None
    sentiment_volatility: float | None
    positive_ratio: float | None
    negative_ratio: float | None = None

    attention_index: float | None
    hype_acceleration: float | None
    raw_metrics: dict[str, Any] | None = None


class TrendingMetricsItem(BaseModel):
    title: DbTitleResponse
    metrics: MetricsSnapshotResponse


class TrendingMetricsResponse(BaseModel):
    window_hours: int
    collected_at: str
    items: list[TrendingMetricsItem]


class MetricsTimeseriesResponse(BaseModel):
    title: DbTitleResponse
    window_hours: int
    since: str
    until: str
    points: list[MetricsSnapshotResponse]


class AlertResponse(BaseModel):
    """Alert response schema."""

    id: str
    title_id: str
    alert_type: str
    severity: str
    message: str
    details: dict[str, Any] | None
    created_at: str
    acknowledged_at: str | None


class AlertsListResponse(BaseModel):
    """List of alerts response."""

    alerts: list[AlertResponse]
    total_count: int
    next_offset: int | None = None
    unacknowledged_count_total: int
    unacknowledged_count: int


class AlertCountsResponse(BaseModel):
    """Alert counts by severity."""

    counts: dict[str, int]
    total: int
    unacknowledged: int
    unacknowledged_in_window: int


class AlertAckResponse(BaseModel):
    """Alert acknowledgment response."""

    acknowledged: bool
    alert_id: str


class CollectorPlatformStatus(BaseModel):
    """Status of a single collector platform."""

    platform: str
    enabled: bool
    api_configured: bool
    toggleable: bool = True
    message: str | None = None


class CollectorStatusResponse(BaseModel):
    """Status of all collector platforms."""

    collectors: list[CollectorPlatformStatus]


# ============================================================================
# WebSocket Connection Manager
# ============================================================================


class ConnectionManager:
    """
    Manages WebSocket connections for real-time updates.

    Supports:
    - Multiple concurrent connections
    - Topic-based subscriptions (alerts, metrics, all)
    - Broadcast to all or filtered subscribers
    - Periodic cleanup of dead connections
    """

    def __init__(self) -> None:
        self.active_connections: dict[str, WebSocket] = {}
        self.subscriptions: dict[str, set[str]] = defaultdict(set)  # topic -> connection_ids
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task[None] | None = None

    async def connect(self, websocket: WebSocket, connection_id: str) -> None:
        """Accept a new WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            self.active_connections[connection_id] = websocket
        logger.info(f"[ws] Client connected: {connection_id}")

    async def disconnect(self, connection_id: str) -> None:
        """Remove a disconnected client."""
        async with self._lock:
            self.active_connections.pop(connection_id, None)
            for topic in list(self.subscriptions.keys()):
                self.subscriptions[topic].discard(connection_id)
        logger.info(f"[ws] Client disconnected: {connection_id}")

    async def subscribe(self, connection_id: str, topic: str) -> None:
        """Subscribe a connection to a topic."""
        async with self._lock:
            self.subscriptions[topic].add(connection_id)
        logger.debug(f"[ws] {connection_id} subscribed to {topic}")

    async def unsubscribe(self, connection_id: str, topic: str) -> None:
        """Unsubscribe a connection from a topic."""
        async with self._lock:
            self.subscriptions[topic].discard(connection_id)

    async def broadcast(self, message: dict[str, Any], topic: str = "all") -> int:
        """
        Broadcast a message to all subscribers of a topic.

        Returns the number of successful sends.
        """
        sent = 0
        to_send: list[tuple[str, WebSocket]] = []
        async with self._lock:
            subscribers = self.subscriptions.get(topic, set()) | self.subscriptions.get(
                "all", set()
            )
            for conn_id in subscribers:
                websocket = self.active_connections.get(conn_id)
                if websocket is not None:
                    to_send.append((conn_id, websocket))

        dead: list[str] = []
        for conn_id, websocket in to_send:
            try:
                await websocket.send_json(message)
                sent += 1
            except Exception as e:
                logger.warning(f"[ws] Failed to send to {conn_id}: {e}")
                dead.append(conn_id)

        if dead:
            async with self._lock:
                for conn_id in dead:
                    self.active_connections.pop(conn_id, None)
                    for t in self.subscriptions:
                        self.subscriptions[t].discard(conn_id)
        return sent

    @property
    def connection_count(self) -> int:
        """Get current number of active connections."""
        return len(self.active_connections)

    async def snapshot_status(self) -> dict[str, Any]:
        """Return a lock-safe snapshot of current connections/subscriptions."""
        async with self._lock:
            return {
                "active_connections": len(self.active_connections),
                "subscriptions": {
                    topic: len(conn_ids) for topic, conn_ids in self.subscriptions.items()
                },
            }

    async def start_cleanup_task(self, interval_seconds: int = 60) -> None:
        """Start periodic cleanup of dead connections."""
        if self._cleanup_task is not None:
            return

        async def _cleanup_loop() -> None:
            while True:
                await asyncio.sleep(interval_seconds)
                await self._cleanup_dead_connections()

        self._cleanup_task = asyncio.create_task(_cleanup_loop())
        logger.info(f"[ws] Started cleanup task (interval={interval_seconds}s)")

    async def stop_cleanup_task(self) -> None:
        """Stop the periodic cleanup task."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None
            logger.info("[ws] Stopped cleanup task")

    async def _cleanup_dead_connections(self) -> None:
        """Remove connections that are no longer alive."""
        dead_connections: list[str] = []

        async with self._lock:
            for conn_id, websocket in list(self.active_connections.items()):
                try:
                    # Check if connection is still open by inspecting state
                    if websocket.client_state != WebSocketState.CONNECTED:
                        dead_connections.append(conn_id)
                except Exception:
                    dead_connections.append(conn_id)

            for conn_id in dead_connections:
                self.active_connections.pop(conn_id, None)
                for topic in self.subscriptions:
                    self.subscriptions[topic].discard(conn_id)

        if dead_connections:
            logger.info(f"[ws] Cleaned up {len(dead_connections)} dead connections")


# Global connection manager
ws_manager = ConnectionManager()


async def broadcast_alert(alert: dict[str, Any]) -> None:
    """Broadcast a new alert to WebSocket subscribers."""
    await ws_manager.broadcast(
        {"type": "alert", "data": alert, "timestamp": datetime.now(UTC).isoformat()},
        topic="alerts",
    )


async def broadcast_metrics_update(title_id: str, metrics: dict[str, Any]) -> None:
    """Broadcast a metrics update to WebSocket subscribers."""
    await ws_manager.broadcast(
        {
            "type": "metrics_update",
            "data": {"title_id": title_id, "metrics": metrics},
            "timestamp": datetime.now(UTC).isoformat(),
        },
        topic="metrics",
    )


@dataclass
class DbMentionsResult:
    mentions: list[MentionResponse]
    total_count: int
    next_offset: int | None
    title_id: UUID
    last_collected_at: datetime | None


def _title_from_row(row: Any) -> DbTitleResponse:
    return DbTitleResponse(
        id=str(row.id),
        tmdb_id=int(row.tmdb_id),
        title=str(row.title),
        media_type=str(row.media_type),
        release_date=row.release_date.isoformat() if getattr(row, "release_date", None) else None,
        popularity=getattr(row, "popularity", None),
    )


async def _get_mentions_from_db(
    *,
    title: str,
    title_id: UUID | None,
    platform: str,
    limit: int,
    offset: int,
) -> DbMentionsResult | None:
    async with get_session() as session:
        title_row = (
            await get_title_by_id(session, title_id)
            if title_id is not None
            else await get_title_by_name(session, title)
        )
        if not title_row:
            return None

        last_collected_at = await get_latest_mention_collected_at(
            session, title_id=title_row.id, platform=platform
        )

        total_count = await get_mentions_count(session, title_id=title_row.id, platform=platform)
        if total_count == 0:
            return DbMentionsResult([], 0, None, title_row.id, last_collected_at)

        mentions = await get_mentions_for_title(
            session,
            title_id=title_row.id,
            platform=platform,
            limit=limit,
            offset=offset,
        )

        has_more = total_count > offset + limit
        mentions_page = mentions

        results: list[MentionResponse] = []
        for m in mentions_page:
            results.append(
                MentionResponse(
                    platform=m.platform,
                    source_id=m.source_id,
                    content=(m.content[:500] + "...")
                    if m.content and len(m.content) > 500
                    else (m.content or ""),
                    author=m.author,
                    url=m.url,
                    created_at=m.created_at.isoformat(),
                    metrics=m.metrics or {},
                    sentiment=m.sentiment,
                )
            )

        next_offset = offset + limit if has_more else None
        return DbMentionsResult(results, total_count, next_offset, title_row.id, last_collected_at)


async def _persist_mentions(
    *,
    title_id: UUID,
    platform: str,
    posts: list[Any],
    sentiment_by_source_id: dict[str, dict[str, Any]],
    collected_at: datetime | None = None,
) -> None:
    async with get_session() as session:
        await insert_mentions(
            session,
            title_id=title_id,
            platform=platform,
            posts=posts,
            sentiment_by_source_id=sentiment_by_source_id,
            collected_at=collected_at,
        )


def _sentiment_payload(sr: SentimentResult) -> dict[str, Any]:
    return sr.to_dict()


def _translate_before_sentiment_enabled() -> bool:
    """Read runtime setting for translate-before-sentiment behavior."""
    setting_value = getattr(get_settings(), "translate_before_sentiment", False)
    return setting_value if isinstance(setting_value, bool) else False


def _analyze_texts_for_sentiment(texts: list[str], *, translate: bool) -> list[SentimentResult]:
    """Analyze sentiments, optionally translating texts to English first."""
    sentiments, _stats = analyze_sentiment_batch_with_translation(
        texts,
        translate=translate,
        log_context="api",
    )
    return sentiments


async def _collect_mentions_live(
    *,
    platform: str,
    title: str,
    limit: int,
) -> tuple[list[MentionResponse], dict[str, dict[str, Any]], list[Any], datetime]:
    collector: BaseCollector | None = None
    if platform == "reddit":
        collector = _reddit_collector
    elif platform == "youtube":
        collector = _youtube_collector
    elif platform == "bluesky":
        collector = _bluesky_collector
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}")

    if not collector:
        raise HTTPException(status_code=503, detail=f"{platform} collector not initialized")

    result = await collector.collect(query=title, limit=limit)
    if not result.success:
        raise HTTPException(status_code=500, detail=result.error)

    # Use batch sentiment to avoid N sequential VADER calls.
    # Offload CPU-bound VADER work to a thread to keep the event loop responsive.
    translate_before_sentiment = _translate_before_sentiment_enabled()
    sentiment_results = await asyncio.to_thread(
        _analyze_texts_for_sentiment,
        [post.content for post in result.posts],
        translate=translate_before_sentiment,
    )

    mentions: list[MentionResponse] = []
    sentiment_by_source_id: dict[str, dict[str, Any]] = {}
    for post, sentiment_result in zip(result.posts, sentiment_results, strict=True):
        sentiment_payload = _sentiment_payload(sentiment_result)
        sentiment_by_source_id[post.source_id] = sentiment_payload

        mentions.append(
            MentionResponse(
                platform=post.platform,
                source_id=post.source_id,
                content=post.content[:500] + "..." if len(post.content) > 500 else post.content,
                author=post.author,
                url=post.url,
                created_at=post.created_at.isoformat(),
                metrics=post.metrics,
                sentiment=sentiment_payload,
            )
        )

    return mentions, sentiment_by_source_id, result.posts, result.collected_at


async def _refresh_mentions_background(
    *,
    title: str,
    title_id: UUID,
    platform: str,
    limit: int,
) -> None:
    try:
        collector: BaseCollector | None = None
        if platform == "reddit":
            collector = _reddit_collector
        elif platform == "youtube":
            collector = _youtube_collector
        elif platform == "bluesky":
            collector = _bluesky_collector
        else:
            logger.warning(f"[api] Unsupported platform for refresh: {platform}")
            return

        if not collector:
            logger.warning(f"[api] {platform} collector not initialized for refresh")
            return

        result = await collector.collect(query=title, limit=limit)
        if not result.success:
            logger.warning(f"[api] {platform} refresh failed: {result.error}")
            return

        # Use batch sentiment (consistent with the foreground path and the
        # pipeline runner) and persist all sentiment fields.
        # Offload CPU-bound VADER work to a thread to keep the event loop responsive.
        translate_before_sentiment = _translate_before_sentiment_enabled()
        sentiment_results = await asyncio.to_thread(
            _analyze_texts_for_sentiment,
            [post.content for post in result.posts],
            translate=translate_before_sentiment,
        )
        sentiment_by_source_id: dict[str, dict[str, Any]] = {}
        for post, sr in zip(result.posts, sentiment_results, strict=True):
            sentiment_by_source_id[post.source_id] = _sentiment_payload(sr)

        await _persist_mentions(
            title_id=title_id,
            platform=platform,
            posts=result.posts,
            sentiment_by_source_id=sentiment_by_source_id,
            collected_at=result.collected_at,
        )
    except Exception as exc:
        logger.exception(f"[api] {platform} background refresh failed: {exc}")


# Collectors (initialized on startup)
_reddit_collector: RedditCollector | None = None
_youtube_collector: YouTubeCollector | None = None
_bluesky_collector: BlueskyCollector | None = None
_tmdb_collector: TMDBCollector | None = None

# Runtime overrides for collector enabled state.
# Keys: "reddit", "youtube", "bluesky".  Values override the env-var defaults.
# In-memory cache is updated on toggle; Redis is used for cross-process sharing.
_collector_enabled_overrides: dict[str, bool] = {}

_TOGGLEABLE_PLATFORMS = {"youtube", "bluesky"}


def _api_keys_configured(platform: str) -> bool:
    """Check if the API keys for *platform* are set (ignoring the enabled toggle)."""
    from sam.config import _is_effectively_set

    s = get_settings()
    if platform == "reddit":
        return _is_effectively_set(s.reddit.client_id) and _is_effectively_set(
            s.reddit.client_secret
        )
    if platform == "youtube":
        return _is_effectively_set(s.youtube.api_key)
    if platform == "bluesky":
        return _is_effectively_set(s.bluesky.identifier) and _is_effectively_set(
            s.bluesky.app_password
        )
    return False


async def is_collector_enabled(platform: str) -> bool:
    """Check if a collector is enabled (in-memory override > Redis > env default)."""
    # 1) In-memory override (same process)
    if platform in _collector_enabled_overrides:
        return _collector_enabled_overrides[platform]
    # 2) Redis override (cross-process, from dashboard toggle)
    from sam.cache import collector_toggle_get

    redis_val = await collector_toggle_get(platform)
    if redis_val is not None:
        return redis_val
    # 3) Env var default
    s = get_settings()
    return getattr(getattr(s, platform, None), "enabled", False)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler."""
    global _reddit_collector, _youtube_collector, _bluesky_collector, _tmdb_collector

    settings = get_settings()

    # Initialize collectors
    logger.info(f"[api] Starting SAM API v{__version__} (demo_mode={settings.demo_mode})")

    _reddit_collector = RedditCollector()
    _youtube_collector = YouTubeCollector()
    _bluesky_collector = BlueskyCollector()
    _tmdb_collector = TMDBCollector()

    # Start WebSocket cleanup task
    await ws_manager.start_cleanup_task(settings.ws_cleanup_interval_seconds)

    yield

    # Cleanup
    logger.info("[api] Shutting down...")
    await ws_manager.stop_cleanup_task()
    if _reddit_collector:
        await _reddit_collector.close()
    if _youtube_collector:
        await _youtube_collector.close()
    if _bluesky_collector:
        await _bluesky_collector.close()
    if _tmdb_collector:
        await _tmdb_collector.close()
    await close_redis()


# Create app
setup_logging()
app = FastAPI(
    title="Social Attention Monitor API",
    description="Real-time social attention tracking for film & TV releases",
    version=__version__,
    lifespan=lifespan,
)


# Error handlers (consistent schema)
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(error="http_error", detail=exc.detail).model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception(f"[api] unhandled error: {exc}")
    # Avoid leaking internal details (SQL, connection strings, stack traces)
    # in production.  Only expose the raw message in development mode.
    _settings = get_settings()
    detail = str(exc) if _settings.is_development else "An unexpected error occurred."
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(error="internal_server_error", detail=detail).model_dump(),
    )


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=HTTP_422_UNPROCESSABLE_CONTENT,
        content=ErrorResponse(error="validation_error", detail=str(exc)).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=HTTP_422_UNPROCESSABLE_CONTENT,
        content=ErrorResponse(error="request_validation_error", detail=exc.errors()).model_dump(),
    )


# Rate limiter (simple in-memory sliding window)
class RateLimiter:
    """
    Simple in-memory rate limiter using sliding window.

    Thread-safe for async usage (single-threaded event loop).
    For production with multiple workers, use Redis-based limiting.

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


_rate_limiter = RateLimiter(requests_per_minute=120)  # 2 req/sec average


# CORS middleware
settings = get_settings()
cors_origins = settings.cors_allow_origins_list
if not settings.is_development and cors_origins == ["*"]:
    # Production safety: default to no wildcard CORS.
    cors_origins = []

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next: Any) -> Any:
    """Apply rate limiting to API endpoints (skip health checks)."""
    # Skip rate limiting for health endpoints
    if request.url.path in ("/health", "/api/v1/pipeline/health"):
        return await call_next(request)

    # Use IP as key (or X-Forwarded-For if behind proxy)
    client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if not client_ip:
        client_ip = request.client.host if request.client else "unknown"

    allowed, remaining = _rate_limiter.is_allowed(client_ip)

    if not allowed:
        retry_after = _rate_limiter.get_retry_after(client_ip)
        return JSONResponse(
            status_code=429,
            content=ErrorResponse(
                error="rate_limit_exceeded",
                detail=f"Too many requests. Retry after {retry_after} seconds.",
            ).model_dump(),
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(_rate_limiter.requests_per_minute),
                "X-RateLimit-Remaining": "0",
            },
        )

    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(_rate_limiter.requests_per_minute)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response


@app.get("/health", response_model=HealthResponse)
async def health_check(
    external: bool = Query(False, description="Check external API reachability"),
) -> HealthResponse:
    """Health check endpoint."""
    database_ok = False
    redis_ok = False
    tmdb_reachable: bool | None = None
    youtube_reachable: bool | None = None
    bluesky_reachable: bool | None = None

    # DB check
    try:
        async with get_session() as session:
            await session.execute(text("SELECT 1"))
        database_ok = True
    except Exception:
        database_ok = False

    # Redis check
    try:
        r = get_redis()
        redis_ok = bool(await cast(Awaitable[bool], r.ping())) if r is not None else False
    except Exception:
        redis_ok = False

    # Optional external API reachability checks
    if external:
        timeout = httpx.Timeout(2.0)
        if settings.tmdb.is_configured:
            try:
                headers = {}
                params: dict[str, Any] = {}
                if settings.tmdb.access_token:
                    headers["Authorization"] = f"Bearer {settings.tmdb.access_token}"
                else:
                    params["api_key"] = settings.tmdb.api_key
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.get(
                        "https://api.themoviedb.org/3/configuration", headers=headers, params=params
                    )
                tmdb_reachable = resp.status_code == 200
            except Exception:
                tmdb_reachable = False
        else:
            tmdb_reachable = False

        if _api_keys_configured("youtube"):
            try:
                # Use videos.list with a well-known video ID (1 quota unit)
                # instead of search.list (100 quota units) to avoid burning
                # budget on health checks.
                params = {
                    "part": "id",
                    "id": "dQw4w9WgXcQ",
                    "key": settings.youtube.api_key,
                }
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.get(
                        "https://www.googleapis.com/youtube/v3/videos", params=params
                    )
                youtube_reachable = resp.status_code == 200
            except Exception:
                youtube_reachable = False
        else:
            youtube_reachable = False

        if _api_keys_configured("bluesky"):
            try:
                # Unauthenticated public endpoint — no credentials needed.
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.get(
                        "https://public.api.bsky.app/xrpc/app.bsky.actor.searchActors",
                        params={"q": "test", "limit": 1},
                    )
                bluesky_reachable = resp.status_code == 200
            except Exception:
                bluesky_reachable = False
        else:
            bluesky_reachable = False

    return HealthResponse(
        status="healthy",
        version=__version__,
        timestamp=datetime.now(UTC).isoformat(),
        demo_mode=settings.demo_mode,
        reddit_configured=_api_keys_configured("reddit"),
        youtube_configured=_api_keys_configured("youtube"),
        tmdb_configured=settings.tmdb.is_configured,
        bluesky_configured=_api_keys_configured("bluesky"),
        reddit_enabled=await is_collector_enabled("reddit"),
        youtube_enabled=await is_collector_enabled("youtube"),
        bluesky_enabled=await is_collector_enabled("bluesky"),
        youtube_reachable=youtube_reachable,
        tmdb_reachable=tmdb_reachable,
        bluesky_reachable=bluesky_reachable,
        database_ok=database_ok,
        redis_ok=redis_ok,
    )


@app.get("/api/v1/pipeline/health", response_model=PipelineHealthResponse)
async def pipeline_health() -> PipelineHealthResponse:
    """
    Pipeline health endpoint.

    Returns:
    - newest mention age (per platform)
    - per-platform mention counts (last 24h)
    - latest pipeline run status
    - processing lag indicators
    """
    cache_key = "sam:pipeline:health"
    cached = await cache_get_json(cache_key)
    if isinstance(cached, dict) and "timestamp" in cached:
        return PipelineHealthResponse(**cached)

    api_quota: dict[str, ApiQuotaInfo] = {}
    async with get_session() as session:
        stats = await get_pipeline_health_stats(session)

        # Use DB aggregation so this endpoint is correct across processes.
        quota = await aggregate_youtube_quota_from_db(session)
        api_quota["youtube"] = ApiQuotaInfo(**quota.to_api_dict())

    sentiment_stats: PipelineSentimentStats | None = None
    for run in stats["latest_pipeline_runs"]:
        if run.get("job_name") != "collector-cycle":
            continue
        run_stats = run.get("stats")
        if not isinstance(run_stats, dict):
            continue
        sentiment_stats = PipelineSentimentStats(
            translate_attempted=int(run_stats.get("translate_attempted", 0) or 0),
            translate_count=int(run_stats.get("translate_count", 0) or 0),
            translate_failures=int(run_stats.get("translate_failures", 0) or 0),
            translate_skipped_english=int(run_stats.get("translate_skipped_english", 0) or 0),
            sentiment_ms_total=float(run_stats.get("sentiment_ms_total", 0.0) or 0.0),
        )
        break

    payload = PipelineHealthResponse(
        timestamp=stats["timestamp"],
        active_titles=stats["active_titles"],
        total_mentions=stats["total_mentions"],
        mentions_last_24h=stats["mentions_last_24h"],
        newest_mention_age_seconds=stats["newest_mention_age_seconds"],
        newest_mention_at=stats["newest_mention_at"],
        latest_pipeline_runs=[PipelineRunInfo(**run) for run in stats["latest_pipeline_runs"]],
        sentiment_stats=sentiment_stats,
        api_quota=api_quota,
    )
    await cache_set_json(
        cache_key, payload.model_dump(), ttl_seconds=settings.cache_ttl_pipeline_health
    )
    return payload


@app.get("/api/v1/pipeline/quota", response_model=PipelineQuotaResponse)
async def pipeline_quota() -> PipelineQuotaResponse:
    """
    API quota usage summary.

    Aggregates quota usage from **all** pipeline runs today (PT), not just the
    latest one.  Each ``--once`` run persists its own usage in
    ``pipeline_runs.stats.api_quota``; this endpoint sums them to get the true
    daily total.  YouTube Data API v3 daily budget: 10,000 units.
    """
    quota = await aggregate_youtube_quota_from_db()

    return PipelineQuotaResponse(
        youtube=ApiQuotaInfo(**quota.to_api_dict()),
        last_run_at=quota.last_run_at,
    )


@app.get("/api/v1/collectors/status", response_model=CollectorStatusResponse)
async def collectors_status() -> CollectorStatusResponse:
    """Get enabled/disabled status of all collector platforms."""
    platforms = []
    for name in ("reddit", "youtube", "bluesky"):
        enabled = await is_collector_enabled(name)
        api_ok = _api_keys_configured(name)
        toggleable = name in _TOGGLEABLE_PLATFORMS
        message = None
        if name == "reddit":
            message = (
                "Reddit API access denied. Set REDDIT_ENABLED=true in .env "
                "with valid API keys to enable."
            )
        platforms.append(
            CollectorPlatformStatus(
                platform=name,
                enabled=enabled,
                api_configured=api_ok,
                toggleable=toggleable,
                message=message,
            )
        )

    return CollectorStatusResponse(collectors=platforms)


@app.put("/api/v1/collectors/{platform}/toggle")
async def toggle_collector(
    platform: str,
    enabled: bool = Query(..., description="Enable or disable the collector"),
) -> CollectorPlatformStatus:
    """
    Toggle a collector on or off at runtime.

    Reddit cannot be toggled from the dashboard — it requires setting
    REDDIT_ENABLED=true in .env with valid API keys.
    """
    if platform not in ("reddit", "youtube", "bluesky"):
        raise HTTPException(status_code=404, detail=f"Unknown platform: {platform}")

    if platform == "reddit":
        raise HTTPException(
            status_code=403,
            detail=(
                "Reddit cannot be toggled from the dashboard. "
                "Set REDDIT_ENABLED=true in .env with valid API keys to enable."
            ),
        )

    _collector_enabled_overrides[platform] = enabled
    # Persist to Redis so the scheduler process sees it too
    from sam.cache import collector_toggle_set

    await collector_toggle_set(platform, enabled)
    logger.info(f"[api] Collector '{platform}' toggled to enabled={enabled}")

    api_ok = _api_keys_configured(platform)

    return CollectorPlatformStatus(
        platform=platform,
        enabled=enabled,
        api_configured=api_ok,
        toggleable=True,
    )


@app.get("/api/v1/trending", response_model=TrendingResponse)
async def get_trending(
    media_type: str = Query("all", description="Filter by media type: all, movie, tv"),
    limit: int = Query(20, ge=1, le=50, description="Number of titles to return"),
) -> TrendingResponse:
    """Get trending movies and TV shows."""
    if not _tmdb_collector:
        raise HTTPException(status_code=503, detail="TMDB collector not initialized")

    cache_key = f"sam:trending:{media_type}:{limit}:demo={settings.demo_mode}"
    cached = await cache_get_json(cache_key)
    if isinstance(cached, dict) and "titles" in cached:
        return TrendingResponse(**cached)

    titles = await _tmdb_collector.get_trending(
        media_type=media_type,
        limit=limit,
    )

    payload = TrendingResponse(
        titles=[
            TitleResponse(
                tmdb_id=t.tmdb_id,
                title=t.title,
                media_type=t.media_type,
                release_date=t.release_date.isoformat() if t.release_date else None,
                overview=t.overview[:200] + "..." if len(t.overview) > 200 else t.overview,
                popularity=t.popularity,
                vote_average=t.vote_average,
            )
            for t in titles
        ],
        collected_at=datetime.now(UTC).isoformat(),
    )
    await cache_set_json(cache_key, payload.model_dump(), ttl_seconds=settings.cache_ttl_trending)
    return payload


@app.get("/api/v1/search")
async def search_titles(
    query: str = Query(..., min_length=1, description="Search query"),
    media_type: str = Query("multi", description="Search type: multi, movie, tv"),
    limit: int = Query(10, ge=1, le=20),
) -> TrendingResponse:
    """Search for movies and TV shows."""
    if not _tmdb_collector:
        raise HTTPException(status_code=503, detail="TMDB collector not initialized")

    cache_key = f"sam:search:{media_type}:{limit}:{query}:demo={settings.demo_mode}"
    cached = await cache_get_json(cache_key)
    if isinstance(cached, dict) and "titles" in cached:
        return TrendingResponse(**cached)

    titles = await _tmdb_collector.search(
        query=query,
        media_type=media_type,
        limit=limit,
    )

    payload = TrendingResponse(
        titles=[
            TitleResponse(
                tmdb_id=t.tmdb_id,
                title=t.title,
                media_type=t.media_type,
                release_date=t.release_date.isoformat() if t.release_date else None,
                overview=t.overview[:200] + "..." if len(t.overview) > 200 else t.overview,
                popularity=t.popularity,
                vote_average=t.vote_average,
            )
            for t in titles
        ],
        collected_at=datetime.now(UTC).isoformat(),
    )
    await cache_set_json(cache_key, payload.model_dump(), ttl_seconds=settings.cache_ttl_search)
    return payload


@app.get("/api/v1/mentions/reddit", response_model=MentionsResponse)
async def get_reddit_mentions(
    background_tasks: BackgroundTasks,
    title: str = Query(..., description="Title to search for"),
    title_id: UUID | None = Query(None, description="Exact title UUID (preferred when available)"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> MentionsResponse:
    """Get Reddit mentions for a title."""
    db_result = await _get_mentions_from_db(
        title=title,
        title_id=title_id,
        platform="reddit",
        limit=limit,
        offset=offset,
    )
    if db_result is not None:
        if db_result.total_count > 0 or offset > 0:
            if (
                offset == 0
                and db_result.title_id
                and (
                    db_result.last_collected_at is None
                    or db_result.last_collected_at
                    < datetime.now(UTC) - timedelta(minutes=settings.mentions_refresh_stale_minutes)
                )
            ):
                background_tasks.add_task(
                    _refresh_mentions_background,
                    title=title,
                    title_id=db_result.title_id,
                    platform="reddit",
                    limit=limit,
                )
            return MentionsResponse(
                title=title,
                platform="reddit",
                mentions=db_result.mentions,
                total_count=db_result.total_count,
                next_offset=db_result.next_offset,
                collected_at=datetime.now(UTC).isoformat(),
            )
    elif offset > 0:
        raise HTTPException(
            status_code=404,
            detail="Title not found in DB for paginated results",
        )

    mentions, sentiment_by_source_id, posts, collected_at = await _collect_mentions_live(
        platform="reddit",
        title=title,
        limit=limit,
    )

    if db_result is not None:
        await _persist_mentions(
            title_id=db_result.title_id,
            platform="reddit",
            posts=posts,
            sentiment_by_source_id=sentiment_by_source_id,
            collected_at=collected_at,
        )

    return MentionsResponse(
        title=title,
        platform="reddit",
        mentions=mentions,
        total_count=len(mentions),
        collected_at=collected_at.isoformat(),
    )


@app.get("/api/v1/mentions/youtube", response_model=MentionsResponse)
async def get_youtube_mentions(
    background_tasks: BackgroundTasks,
    title: str = Query(..., description="Title to search for"),
    title_id: UUID | None = Query(None, description="Exact title UUID (preferred when available)"),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
) -> MentionsResponse:
    """Get YouTube videos for a title."""
    db_result = await _get_mentions_from_db(
        title=title,
        title_id=title_id,
        platform="youtube",
        limit=limit,
        offset=offset,
    )
    if db_result is not None:
        if db_result.total_count > 0 or offset > 0:
            if (
                offset == 0
                and db_result.title_id
                and (
                    db_result.last_collected_at is None
                    or db_result.last_collected_at
                    < datetime.now(UTC) - timedelta(minutes=settings.mentions_refresh_stale_minutes)
                )
            ):
                background_tasks.add_task(
                    _refresh_mentions_background,
                    title=title,
                    title_id=db_result.title_id,
                    platform="youtube",
                    limit=limit,
                )
            return MentionsResponse(
                title=title,
                platform="youtube",
                mentions=db_result.mentions,
                total_count=db_result.total_count,
                next_offset=db_result.next_offset,
                collected_at=datetime.now(UTC).isoformat(),
            )
    elif offset > 0:
        raise HTTPException(
            status_code=404,
            detail="Title not found in DB for paginated results",
        )

    mentions, sentiment_by_source_id, posts, collected_at = await _collect_mentions_live(
        platform="youtube",
        title=title,
        limit=limit,
    )

    if db_result is not None:
        await _persist_mentions(
            title_id=db_result.title_id,
            platform="youtube",
            posts=posts,
            sentiment_by_source_id=sentiment_by_source_id,
            collected_at=collected_at,
        )

    return MentionsResponse(
        title=title,
        platform="youtube",
        mentions=mentions,
        total_count=len(mentions),
        collected_at=collected_at.isoformat(),
    )


@app.get("/api/v1/mentions/bluesky", response_model=MentionsResponse)
async def get_bluesky_mentions(
    background_tasks: BackgroundTasks,
    title: str = Query(..., description="Title to search for"),
    title_id: UUID | None = Query(None, description="Exact title UUID (preferred when available)"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> MentionsResponse:
    """Get Bluesky posts for a title."""
    db_result = await _get_mentions_from_db(
        title=title,
        title_id=title_id,
        platform="bluesky",
        limit=limit,
        offset=offset,
    )
    if db_result is not None:
        if db_result.total_count > 0 or offset > 0:
            if (
                offset == 0
                and db_result.title_id
                and (
                    db_result.last_collected_at is None
                    or db_result.last_collected_at
                    < datetime.now(UTC) - timedelta(minutes=settings.mentions_refresh_stale_minutes)
                )
            ):
                background_tasks.add_task(
                    _refresh_mentions_background,
                    title=title,
                    title_id=db_result.title_id,
                    platform="bluesky",
                    limit=limit,
                )
            return MentionsResponse(
                title=title,
                platform="bluesky",
                mentions=db_result.mentions,
                total_count=db_result.total_count,
                next_offset=db_result.next_offset,
                collected_at=datetime.now(UTC).isoformat(),
            )
    elif offset > 0:
        raise HTTPException(
            status_code=404,
            detail="Title not found in DB for paginated results",
        )

    mentions, sentiment_by_source_id, posts, collected_at = await _collect_mentions_live(
        platform="bluesky",
        title=title,
        limit=limit,
    )

    if db_result is not None:
        await _persist_mentions(
            title_id=db_result.title_id,
            platform="bluesky",
            posts=posts,
            sentiment_by_source_id=sentiment_by_source_id,
            collected_at=collected_at,
        )

    return MentionsResponse(
        title=title,
        platform="bluesky",
        mentions=mentions,
        total_count=len(mentions),
        collected_at=collected_at.isoformat(),
    )


@app.get("/api/v1/sentiment/analyze")
async def analyze_text_sentiment(
    text: str = Query(..., min_length=1, max_length=5000, description="Text to analyze"),
) -> dict[str, Any]:
    """Analyze sentiment of arbitrary text."""
    result = analyze_sentiment(text)
    sentiment_payload = _sentiment_payload(result)
    return {
        "text": text[:100] + "..." if len(text) > 100 else text,
        "sentiment": sentiment_payload,
        "model": result.model,
    }


@app.get("/api/v1/db/titles", response_model=TitlesResponse)
async def db_list_titles(
    q: str | None = Query(None, description="Substring search in DB titles"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> TitlesResponse:
    async with get_session() as session:
        # Compute the true total count for the same filter.
        count_stmt = select(func.count()).where(TitleModel.is_active.is_(True))
        if q:
            count_stmt = count_stmt.where(
                TitleModel.title.ilike(f"%{escape_like(q)}%", escape="\\")
            )
        count_result = await session.execute(count_stmt)
        total_count = int(count_result.scalar_one())

        # Fetch one extra to compute next_offset.
        rows = await list_titles(session, query=q, limit=limit + 1, offset=offset)
        has_more = len(rows) > limit
        page = rows[:limit]
        next_offset = offset + limit if has_more else None

        return TitlesResponse(
            titles=[
                DbTitleResponse(
                    id=str(t.id),
                    tmdb_id=t.tmdb_id,
                    title=t.title,
                    media_type=t.media_type,
                    release_date=t.release_date.isoformat() if t.release_date else None,
                    popularity=t.popularity,
                )
                for t in page
            ],
            total_count=total_count,
            next_offset=next_offset,
        )


@app.get("/api/v1/metrics/trending", response_model=TrendingMetricsResponse)
async def metrics_trending(
    window_hours: int = Query(24, ge=1, le=168),
    limit: int = Query(10, ge=1, le=50),
) -> TrendingMetricsResponse:
    cache_key = f"sam:metrics:trending:{window_hours}:{limit}"
    cached = await cache_get_json(cache_key)
    if isinstance(cached, dict) and "items" in cached:
        return TrendingMetricsResponse(**cached)

    async with get_session() as session:
        rows = await get_trending_by_attention_index(
            session, window_hours=window_hours, limit=limit
        )

    now = datetime.now(UTC)
    payload = TrendingMetricsResponse(
        window_hours=window_hours,
        collected_at=now.isoformat(),
        items=[
            TrendingMetricsItem(
                title=DbTitleResponse(
                    id=str(t.id),
                    tmdb_id=t.tmdb_id,
                    title=t.title,
                    media_type=t.media_type,
                    release_date=t.release_date.isoformat() if t.release_date else None,
                    popularity=t.popularity,
                ),
                metrics=MetricsSnapshotResponse(
                    title_id=str(m.title_id),
                    snapshot_time=m.snapshot_time.isoformat(),
                    window_hours=m.window_hours,
                    mention_count=m.mention_count,
                    unique_authors=m.unique_authors,
                    reddit_mentions=m.reddit_mentions,
                    youtube_mentions=m.youtube_mentions,
                    bluesky_mentions=getattr(m, "bluesky_mentions", 0) or 0,
                    mention_velocity=m.mention_velocity,
                    velocity_change=m.velocity_change,
                    avg_sentiment=m.avg_sentiment,
                    sentiment_volatility=m.sentiment_volatility,
                    positive_ratio=m.positive_ratio,
                    negative_ratio=getattr(m, "negative_ratio", None),
                    attention_index=m.attention_index,
                    hype_acceleration=m.hype_acceleration,
                    raw_metrics=getattr(m, "raw_metrics", None),
                ),
            )
            for t, m in rows
        ],
    )
    await cache_set_json(cache_key, payload.model_dump(), ttl_seconds=settings.cache_ttl_metrics)
    return payload


@app.get("/api/v1/metrics/timeseries", response_model=MetricsTimeseriesResponse)
async def metrics_timeseries(
    title_id: UUID = Query(
        ..., description="Title UUID from /api/v1/db/titles or /api/v1/metrics/trending"
    ),
    window_hours: int = Query(1, ge=1, le=168),
    hours: int = Query(24, ge=1, le=24 * 30),
) -> MetricsTimeseriesResponse:
    # Add a 1-hour buffer to `until` because _snapshot_hour() rounds up to the
    # next hour boundary.  Without this, snapshots from the current collection
    # cycle are invisible until the clock passes the snapshot hour.
    now = datetime.now(UTC)
    until = now + timedelta(hours=1)
    since = now - timedelta(hours=hours)

    async with get_session() as session:
        res = await session.execute(
            text(
                "SELECT id, tmdb_id, title, media_type, release_date, popularity "
                "FROM titles WHERE id = :id"
            ),
            {"id": str(title_id)},
        )
        row = res.first()
        if row is None:
            raise HTTPException(status_code=404, detail="Title not found")

        title_resp = _title_from_row(row)

        points = await get_metrics_timeseries(
            session,
            title_id=UUID(title_resp.id),
            window_hours=window_hours,
            since=since,
            until=until,
        )

    return MetricsTimeseriesResponse(
        title=title_resp,
        window_hours=window_hours,
        since=since.isoformat(),
        until=until.isoformat(),
        points=[
            MetricsSnapshotResponse(
                title_id=str(p.title_id),
                snapshot_time=p.snapshot_time.isoformat(),
                window_hours=p.window_hours,
                mention_count=p.mention_count,
                unique_authors=p.unique_authors,
                reddit_mentions=p.reddit_mentions,
                youtube_mentions=p.youtube_mentions,
                bluesky_mentions=getattr(p, "bluesky_mentions", 0) or 0,
                mention_velocity=p.mention_velocity,
                velocity_change=p.velocity_change,
                avg_sentiment=p.avg_sentiment,
                sentiment_volatility=p.sentiment_volatility,
                positive_ratio=p.positive_ratio,
                negative_ratio=getattr(p, "negative_ratio", None),
                attention_index=p.attention_index,
                hype_acceleration=p.hype_acceleration,
                raw_metrics=getattr(p, "raw_metrics", None),
            )
            for p in points
        ],
    )


# ============================================================================
# Alerts API
# ============================================================================


@app.get("/api/v1/alerts", response_model=AlertsListResponse)
async def list_alerts(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    title_id: UUID | None = Query(None, description="Filter by title"),
    severity: str | None = Query(None, description="Filter by severity: info, warning, critical"),
    hours: int = Query(24, ge=1, le=24 * 30, description="Look back period in hours"),
) -> AlertsListResponse:
    """Get recent alerts with optional filtering."""
    since = datetime.now(UTC) - timedelta(hours=hours)

    async with get_session() as session:
        total_matching = await count_alerts(
            session,
            title_id=title_id,
            severity=severity,
            since=since,
        )
        unack_total = await get_unacknowledged_count(session)
        unack_filtered = await count_unacknowledged_alerts(
            session,
            title_id=title_id,
            severity=severity,
            since=since,
        )
        alerts = await get_recent_alerts(
            session,
            limit=limit,
            offset=offset,
            title_id=title_id,
            severity=severity,
            since=since,
        )

    has_more = (offset + len(alerts)) < total_matching

    return AlertsListResponse(
        alerts=[
            AlertResponse(
                id=str(a.id),
                title_id=str(a.title_id),
                alert_type=a.alert_type,
                severity=a.severity,
                message=a.message,
                details=a.details,
                created_at=a.created_at.isoformat(),
                acknowledged_at=a.acknowledged_at.isoformat() if a.acknowledged_at else None,
            )
            for a in alerts
        ],
        total_count=total_matching,
        next_offset=offset + limit if has_more else None,
        unacknowledged_count_total=unack_total,
        unacknowledged_count=unack_filtered,
    )


@app.get("/api/v1/alerts/counts", response_model=AlertCountsResponse)
async def alert_counts(
    hours: int = Query(24, ge=1, le=24 * 30, description="Look back period in hours"),
) -> AlertCountsResponse:
    """Get alert counts by severity."""
    since = datetime.now(UTC) - timedelta(hours=hours)

    async with get_session() as session:
        counts = await get_alert_counts_by_severity(session, since=since)
        unack_count = await get_unacknowledged_count(session)
        unack_in_window = await count_unacknowledged_alerts(session, since=since)

    return AlertCountsResponse(
        counts=counts,
        total=sum(counts.values()),
        unacknowledged=unack_count,
        unacknowledged_in_window=unack_in_window,
    )


@app.post("/api/v1/alerts/{alert_id}/acknowledge", response_model=AlertAckResponse)
async def ack_alert(alert_id: UUID) -> AlertAckResponse:
    """Acknowledge an alert."""
    async with get_session() as session:
        success = await acknowledge_alert(session, alert_id=alert_id)
        if not success:
            raise HTTPException(status_code=404, detail="Alert not found")

    return AlertAckResponse(acknowledged=True, alert_id=str(alert_id))


@app.post("/api/v1/alerts/run-detection")
async def run_alert_detection(
    window_hours: int = Query(1, ge=1, le=24),
    history_points: int = Query(24, ge=5, le=168),
) -> dict[str, Any]:
    """
    Manually trigger anomaly detection cycle.

    This is primarily for testing/debugging. In production,
    detection runs automatically via the scheduler.
    """
    manager = AlertManager()

    async with get_session() as session:
        detected, created_alerts = await manager.run_detection_cycle(
            session, window_hours=window_hours, history_points=history_points
        )

    # Broadcast after commit (session context exited)
    for alert in created_alerts:
        await broadcast_alert(alert)

    return {
        "anomalies_detected": detected,
        "alerts_created": len(created_alerts),
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ============================================================================
# WebSocket Endpoints
# ============================================================================


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for real-time updates.

    Clients can subscribe to topics:
    - "alerts": Receive new alert notifications
    - "metrics": Receive metrics updates
    - "all": Receive all updates

    Send JSON messages to subscribe/unsubscribe:
    {"action": "subscribe", "topic": "alerts"}
    {"action": "unsubscribe", "topic": "alerts"}
    {"action": "ping"}
    """
    import uuid as uuid_mod

    connection_id = str(uuid_mod.uuid4())[:8]

    await ws_manager.connect(websocket, connection_id)

    # Auto-subscribe to "all" by default
    await ws_manager.subscribe(connection_id, "all")

    # Send welcome message
    await websocket.send_json(
        {
            "type": "connected",
            "connection_id": connection_id,
            "subscribed": ["all"],
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )

    try:
        while True:
            try:
                data = await websocket.receive_json()
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            action = data.get("action")

            if action == "ping":
                await websocket.send_json(
                    {"type": "pong", "timestamp": datetime.now(UTC).isoformat()}
                )

            elif action == "subscribe":
                topic = data.get("topic", "all")
                if topic in ("alerts", "metrics", "all"):
                    await ws_manager.subscribe(connection_id, topic)
                    await websocket.send_json(
                        {
                            "type": "subscribed",
                            "topic": topic,
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                    )
                else:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": f"Unknown topic: {topic}. Valid: alerts, metrics, all",
                        }
                    )

            elif action == "unsubscribe":
                topic = data.get("topic")
                if topic:
                    await ws_manager.unsubscribe(connection_id, topic)
                    await websocket.send_json(
                        {
                            "type": "unsubscribed",
                            "topic": topic,
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                    )

            elif action == "status":
                status = await ws_manager.snapshot_status()
                await websocket.send_json(
                    {
                        "type": "status",
                        "connections": status["active_connections"],
                        "subscriptions": status["subscriptions"],
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )

            else:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": f"Unknown action: {action}. Valid: subscribe, unsubscribe, ping, status",
                    }
                )

    except WebSocketDisconnect:
        await ws_manager.disconnect(connection_id)
    except Exception as e:
        logger.warning(f"[ws] Connection {connection_id} error: {e}")
        await ws_manager.disconnect(connection_id)


@app.get("/api/v1/ws/status")
async def websocket_status() -> dict[str, Any]:
    """Get WebSocket connection status."""
    status = await ws_manager.snapshot_status()
    return {
        "active_connections": status["active_connections"],
        "subscriptions": status["subscriptions"],
        "timestamp": datetime.now(UTC).isoformat(),
    }
