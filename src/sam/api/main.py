"""
FastAPI Application

Main API server for Social Attention Monitor.
"""

from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.status import HTTP_422_UNPROCESSABLE_ENTITY

from sam import __version__
from sam.cache import cache_get_json, cache_set_json, close_redis, get_redis
from sam.collectors.reddit import RedditCollector
from sam.collectors.tmdb import TMDBCollector
from sam.collectors.youtube import YouTubeCollector
from sam.config import get_settings
from sam.logging import setup_logging
from sam.processors.sentiment import analyze_sentiment
from sam.storage.database import get_session
from sam.storage.repository import (
    get_mentions_for_title,
    get_metrics_timeseries,
    get_title_by_name,
    get_trending_by_attention_index,
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
    database_ok: bool
    redis_ok: bool


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

    mention_velocity: float | None
    velocity_change: float | None

    avg_sentiment: float | None
    sentiment_volatility: float | None
    positive_ratio: float | None

    attention_index: float | None
    hype_acceleration: float | None


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
    platform: str,
    limit: int,
    offset: int,
) -> tuple[list[MentionResponse], int, int | None] | None:
    async with get_session() as session:
        title_row = await get_title_by_name(session, title)
        if not title_row:
            return None

        mentions = await get_mentions_for_title(
            session,
            title_id=title_row.id,
            platform=platform,
            limit=limit + 1,
            offset=offset,
        )

        if not mentions:
            return None

        has_more = len(mentions) > limit
        mentions_page = mentions[:limit]

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
        return results, len(mentions_page), next_offset


# Collectors (initialized on startup)
_reddit_collector: RedditCollector | None = None
_youtube_collector: YouTubeCollector | None = None
_tmdb_collector: TMDBCollector | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler."""
    global _reddit_collector, _youtube_collector, _tmdb_collector

    settings = get_settings()

    # Initialize collectors
    logger.info(f"[api] Starting SAM API v{__version__} (demo_mode={settings.demo_mode})")

    _reddit_collector = RedditCollector()
    _youtube_collector = YouTubeCollector()
    _tmdb_collector = TMDBCollector()

    yield

    # Cleanup
    logger.info("[api] Shutting down...")
    if _youtube_collector:
        await _youtube_collector.close()
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
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(error="internal_server_error", detail=str(exc)).model_dump(),
    )


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(error="validation_error", detail=str(exc)).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(error="request_validation_error", detail=exc.errors()).model_dump(),
    )


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


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    database_ok = False
    redis_ok = False

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

    return HealthResponse(
        status="healthy",
        version=__version__,
        timestamp=datetime.now(UTC).isoformat(),
        demo_mode=settings.demo_mode,
        database_ok=database_ok,
        redis_ok=redis_ok,
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
    await cache_set_json(cache_key, payload.model_dump(), ttl_seconds=300)
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
    await cache_set_json(cache_key, payload.model_dump(), ttl_seconds=300)
    return payload


@app.get("/api/v1/mentions/reddit", response_model=MentionsResponse)
async def get_reddit_mentions(
    title: str = Query(..., description="Title to search for"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> MentionsResponse:
    """Get Reddit mentions for a title."""
    db_result = await _get_mentions_from_db(
        title=title, platform="reddit", limit=limit, offset=offset
    )
    if db_result is not None:
        db_mentions, total_count, next_offset = db_result
        return MentionsResponse(
            title=title,
            platform="reddit",
            mentions=db_mentions,
            total_count=total_count,
            next_offset=next_offset,
            collected_at=datetime.now(UTC).isoformat(),
        )

    if not _reddit_collector:
        raise HTTPException(status_code=503, detail="Reddit collector not initialized")

    result = await _reddit_collector.collect(query=title, limit=limit)

    if not result.success:
        raise HTTPException(status_code=500, detail=result.error)

    mentions = []
    for post in result.posts:
        # Analyze sentiment
        sentiment_result = analyze_sentiment(post.content)

        mentions.append(
            MentionResponse(
                platform=post.platform,
                source_id=post.source_id,
                content=post.content[:500] + "..." if len(post.content) > 500 else post.content,
                author=post.author,
                url=post.url,
                created_at=post.created_at.isoformat(),
                metrics=post.metrics,
                sentiment={
                    "compound": sentiment_result.compound,
                    "label": sentiment_result.label,
                },
            )
        )

    return MentionsResponse(
        title=title,
        platform="reddit",
        mentions=mentions,
        total_count=len(mentions),
        collected_at=result.collected_at.isoformat(),
    )


@app.get("/api/v1/mentions/youtube", response_model=MentionsResponse)
async def get_youtube_mentions(
    title: str = Query(..., description="Title to search for"),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
) -> MentionsResponse:
    """Get YouTube videos for a title."""
    db_result = await _get_mentions_from_db(
        title=title, platform="youtube", limit=limit, offset=offset
    )
    if db_result is not None:
        db_mentions, total_count, next_offset = db_result
        return MentionsResponse(
            title=title,
            platform="youtube",
            mentions=db_mentions,
            total_count=total_count,
            next_offset=next_offset,
            collected_at=datetime.now(UTC).isoformat(),
        )

    if not _youtube_collector:
        raise HTTPException(status_code=503, detail="YouTube collector not initialized")

    result = await _youtube_collector.collect(query=title, limit=limit)

    if not result.success:
        raise HTTPException(status_code=500, detail=result.error)

    mentions = []
    for post in result.posts:
        sentiment_result = analyze_sentiment(post.content)

        mentions.append(
            MentionResponse(
                platform=post.platform,
                source_id=post.source_id,
                content=post.content[:500] + "..." if len(post.content) > 500 else post.content,
                author=post.author,
                url=post.url,
                created_at=post.created_at.isoformat(),
                metrics=post.metrics,
                sentiment={
                    "compound": sentiment_result.compound,
                    "label": sentiment_result.label,
                },
            )
        )

    return MentionsResponse(
        title=title,
        platform="youtube",
        mentions=mentions,
        total_count=len(mentions),
        collected_at=result.collected_at.isoformat(),
    )


@app.get("/api/v1/sentiment/analyze")
async def analyze_text_sentiment(
    text: str = Query(..., min_length=1, max_length=5000, description="Text to analyze"),
) -> dict[str, Any]:
    """Analyze sentiment of arbitrary text."""
    result = analyze_sentiment(text)
    return {
        "text": text[:100] + "..." if len(text) > 100 else text,
        "sentiment": {
            "compound": result.compound,
            "positive": result.positive,
            "negative": result.negative,
            "neutral": result.neutral,
            "label": result.label,
        },
        "model": result.model,
    }


@app.get("/api/v1/db/titles", response_model=TitlesResponse)
async def db_list_titles(
    q: str | None = Query(None, description="Substring search in DB titles"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> TitlesResponse:
    async with get_session() as session:
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
            total_count=len(page),
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
                    mention_velocity=m.mention_velocity,
                    velocity_change=m.velocity_change,
                    avg_sentiment=m.avg_sentiment,
                    sentiment_volatility=m.sentiment_volatility,
                    positive_ratio=m.positive_ratio,
                    attention_index=m.attention_index,
                    hype_acceleration=m.hype_acceleration,
                ),
            )
            for t, m in rows
        ],
    )
    await cache_set_json(cache_key, payload.model_dump(), ttl_seconds=60)
    return payload


@app.get("/api/v1/metrics/timeseries", response_model=MetricsTimeseriesResponse)
async def metrics_timeseries(
    title_id: UUID = Query(
        ..., description="Title UUID from /api/v1/db/titles or /api/v1/metrics/trending"
    ),
    window_hours: int = Query(1, ge=1, le=168),
    hours: int = Query(24, ge=1, le=24 * 30),
) -> MetricsTimeseriesResponse:
    until = datetime.now(UTC)
    since = until - timedelta(hours=hours)

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
                mention_velocity=p.mention_velocity,
                velocity_change=p.velocity_change,
                avg_sentiment=p.avg_sentiment,
                sentiment_volatility=p.sentiment_volatility,
                positive_ratio=p.positive_ratio,
                attention_index=p.attention_index,
                hype_acceleration=p.hype_acceleration,
            )
            for p in points
        ],
    )
