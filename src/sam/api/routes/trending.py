"""TMDB trending and search endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query

from sam.api import dependencies as deps
from sam.api.schemas import TitleResponse, TrendingResponse
from sam.config import get_settings

router = APIRouter(prefix="/api/v1", tags=["trending"])


@router.get("/trending", response_model=TrendingResponse)
async def get_trending(
    media_type: str = Query("all", description="Filter by media type: all, movie, tv"),
    limit: int = Query(20, ge=1, le=50, description="Number of titles to return"),
) -> TrendingResponse:
    """Get trending movies and TV shows."""
    settings = get_settings()
    if not deps.tmdb_collector:
        raise HTTPException(status_code=503, detail="TMDB collector not initialized")

    cache_key = f"sam:trending:{media_type}:{limit}:demo={settings.demo_mode}"
    cached = await deps.cache_get_json(cache_key)
    if isinstance(cached, dict) and "titles" in cached:
        return TrendingResponse(**cached)

    titles = await deps.tmdb_collector.get_trending(media_type=media_type, limit=limit)

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
    await deps.cache_set_json(
        cache_key, payload.model_dump(), ttl_seconds=settings.cache_ttl_trending
    )
    return payload


@router.get("/search")
async def search_titles(
    query: str = Query(..., min_length=1, max_length=200, description="Search query"),
    media_type: str = Query("multi", description="Search type: multi, movie, tv"),
    limit: int = Query(10, ge=1, le=20),
) -> TrendingResponse:
    """Search for movies and TV shows."""
    settings = get_settings()
    if not deps.tmdb_collector:
        raise HTTPException(status_code=503, detail="TMDB collector not initialized")

    cache_key = f"sam:search:{media_type}:{limit}:{query}:demo={settings.demo_mode}"
    cached = await deps.cache_get_json(cache_key)
    if isinstance(cached, dict) and "titles" in cached:
        return TrendingResponse(**cached)

    titles = await deps.tmdb_collector.search(query=query, media_type=media_type, limit=limit)

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
    await deps.cache_set_json(
        cache_key, payload.model_dump(), ttl_seconds=settings.cache_ttl_search
    )
    return payload
