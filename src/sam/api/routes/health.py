"""Health check endpoint."""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any, cast

import httpx
from fastapi import APIRouter, Query
from loguru import logger
from sqlalchemy import text

from sam import __version__
from sam.api import dependencies as deps
from sam.api.schemas import HealthResponse
from sam.config import get_settings

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check(
    external: bool = Query(False, description="Check external API reachability"),
) -> HealthResponse:
    """Health check endpoint."""
    settings = get_settings()
    database_ok = False
    redis_ok = False
    tmdb_reachable: bool | None = None
    youtube_reachable: bool | None = None
    bluesky_reachable: bool | None = None

    # DB check
    try:
        async with deps.get_session() as session:
            await session.execute(text("SELECT 1"))
        database_ok = True
    except Exception:
        logger.debug("Health check: database unreachable")
        database_ok = False

    # Redis check
    try:
        r = deps.get_redis()
        redis_ok = bool(await cast(Awaitable[bool], r.ping())) if r is not None else False
    except Exception:
        logger.debug("Health check: Redis unreachable")
        redis_ok = False

    # Optional external API reachability checks
    if external:
        timeout = httpx.Timeout(2.0)
        if settings.tmdb.is_configured:
            try:
                headers: dict[str, str] = {}
                params: dict[str, Any] = {}
                if settings.tmdb.access_token:
                    headers["Authorization"] = f"Bearer {settings.tmdb.access_token}"
                else:
                    params["api_key"] = settings.tmdb.api_key
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.get(
                        "https://api.themoviedb.org/3/configuration",
                        headers=headers,
                        params=params,
                    )
                tmdb_reachable = resp.status_code == 200
            except Exception:
                logger.debug("Health check: TMDB unreachable")
                tmdb_reachable = False
        else:
            tmdb_reachable = False

        if deps.api_keys_configured("youtube"):
            try:
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
                logger.debug("Health check: YouTube unreachable")
                youtube_reachable = False
        else:
            youtube_reachable = False

        if deps.api_keys_configured("bluesky"):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.get(
                        "https://public.api.bsky.app/xrpc/app.bsky.actor.searchActors",
                        params={"q": "test", "limit": 1},
                    )
                bluesky_reachable = resp.status_code == 200
            except Exception:
                logger.debug("Health check: Bluesky unreachable")
                bluesky_reachable = False
        else:
            bluesky_reachable = False

    return HealthResponse(
        status="healthy",
        version=__version__,
        timestamp=datetime.now(UTC).isoformat(),
        demo_mode=settings.demo_mode,
        reddit_configured=deps.api_keys_configured("reddit"),
        youtube_configured=deps.api_keys_configured("youtube"),
        tmdb_configured=settings.tmdb.is_configured,
        bluesky_configured=deps.api_keys_configured("bluesky"),
        reddit_enabled=await deps.is_collector_enabled("reddit"),
        youtube_enabled=await deps.is_collector_enabled("youtube"),
        bluesky_enabled=await deps.is_collector_enabled("bluesky"),
        youtube_reachable=youtube_reachable,
        tmdb_reachable=tmdb_reachable,
        bluesky_reachable=bluesky_reachable,
        database_ok=database_ok,
        redis_ok=redis_ok,
    )
