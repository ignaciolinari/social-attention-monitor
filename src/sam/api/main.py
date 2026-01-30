"""
FastAPI Application

Main API server for Social Attention Monitor.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel

from sam import __version__
from sam.collectors.reddit import RedditCollector
from sam.collectors.tmdb import TMDBCollector
from sam.collectors.youtube import YouTubeCollector
from sam.config import get_settings
from sam.logging import setup_logging
from sam.processors.sentiment import analyze_sentiment
from sam.storage.database import get_session
from sam.storage.repository import get_mentions_for_title, get_title_by_name


# Pydantic schemas
class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    timestamp: str
    demo_mode: bool


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
    collected_at: str


async def _get_mentions_from_db(
    *,
    title: str,
    platform: str,
    limit: int,
) -> list[MentionResponse] | None:
    async with get_session() as session:
        title_row = await get_title_by_name(session, title)
        if not title_row:
            return None

        mentions = await get_mentions_for_title(
            session,
            title_id=title_row.id,
            platform=platform,
            limit=limit,
        )

        if not mentions:
            return None

        results: list[MentionResponse] = []
        for m in mentions:
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

        return results


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


# Create app
setup_logging()
app = FastAPI(
    title="Social Attention Monitor API",
    description="Real-time social attention tracking for film & TV releases",
    version=__version__,
    lifespan=lifespan,
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
    return HealthResponse(
        status="healthy",
        version=__version__,
        timestamp=datetime.now(UTC).isoformat(),
        demo_mode=settings.demo_mode,
    )


@app.get("/api/v1/trending", response_model=TrendingResponse)
async def get_trending(
    media_type: str = Query("all", description="Filter by media type: all, movie, tv"),
    limit: int = Query(20, ge=1, le=50, description="Number of titles to return"),
) -> TrendingResponse:
    """Get trending movies and TV shows."""
    if not _tmdb_collector:
        raise HTTPException(status_code=503, detail="TMDB collector not initialized")

    titles = await _tmdb_collector.get_trending(
        media_type=media_type,
        limit=limit,
    )

    return TrendingResponse(
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


@app.get("/api/v1/search")
async def search_titles(
    query: str = Query(..., min_length=1, description="Search query"),
    media_type: str = Query("multi", description="Search type: multi, movie, tv"),
    limit: int = Query(10, ge=1, le=20),
) -> TrendingResponse:
    """Search for movies and TV shows."""
    if not _tmdb_collector:
        raise HTTPException(status_code=503, detail="TMDB collector not initialized")

    titles = await _tmdb_collector.search(
        query=query,
        media_type=media_type,
        limit=limit,
    )

    return TrendingResponse(
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


@app.get("/api/v1/mentions/reddit", response_model=MentionsResponse)
async def get_reddit_mentions(
    title: str = Query(..., description="Title to search for"),
    limit: int = Query(50, ge=1, le=100),
) -> MentionsResponse:
    """Get Reddit mentions for a title."""
    db_mentions = await _get_mentions_from_db(title=title, platform="reddit", limit=limit)
    if db_mentions is not None:
        return MentionsResponse(
            title=title,
            platform="reddit",
            mentions=db_mentions,
            total_count=len(db_mentions),
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
) -> MentionsResponse:
    """Get YouTube videos for a title."""
    db_mentions = await _get_mentions_from_db(title=title, platform="youtube", limit=limit)
    if db_mentions is not None:
        return MentionsResponse(
            title=title,
            platform="youtube",
            mentions=db_mentions,
            total_count=len(db_mentions),
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
