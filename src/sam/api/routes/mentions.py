"""Mentions endpoints (consolidated + backward-compat aliases)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from sam.api import dependencies as deps
from sam.api.schemas import MentionsResponse
from sam.config import get_settings

router = APIRouter(prefix="/api/v1", tags=["mentions"])


async def _get_mentions_for_platform(
    platform: str,
    background_tasks: BackgroundTasks,
    title: str,
    title_id: UUID | None,
    limit: int,
    offset: int,
) -> MentionsResponse:
    """Shared implementation for all platform mention endpoints."""
    settings = get_settings()
    db_result = await deps.get_mentions_from_db(
        title=title,
        title_id=title_id,
        platform=platform,
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
                    deps.refresh_mentions_background,
                    title=title,
                    title_id=db_result.title_id,
                    platform=platform,
                    limit=limit,
                )
            return MentionsResponse(
                title=title,
                platform=platform,
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

    mentions, _sentiment_by_source_id, _posts, collected_at = await deps.collect_mentions_live(
        platform=platform,
        title=title,
        limit=limit,
    )

    if db_result is not None and db_result.title_id and offset == 0:
        background_tasks.add_task(
            deps.refresh_mentions_background,
            title=title,
            title_id=db_result.title_id,
            platform=platform,
            limit=limit,
        )

    return MentionsResponse(
        title=title,
        platform=platform,
        mentions=mentions,
        total_count=len(mentions),
        collected_at=collected_at.isoformat(),
    )


@router.get("/mentions/{platform}", response_model=MentionsResponse)
async def get_platform_mentions(
    platform: str,
    background_tasks: BackgroundTasks,
    title: str = Query(..., description="Title to search for"),
    title_id: UUID | None = Query(None, description="Exact title UUID (preferred when available)"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> MentionsResponse:
    """Get mentions for a title from a specific platform (reddit, youtube, bluesky)."""
    platform = platform.lower()
    if platform not in deps.VALID_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported platform '{platform}'. Must be one of: {sorted(deps.VALID_PLATFORMS)}",
        )
    effective_limit = min(limit, deps.PLATFORM_DEFAULT_LIMITS.get(platform, limit))
    return await _get_mentions_for_platform(
        platform=platform,
        background_tasks=background_tasks,
        title=title,
        title_id=title_id,
        limit=effective_limit,
        offset=offset,
    )


# Backward-compatible aliases so existing clients/tests still work.


@router.get("/mentions/reddit", response_model=MentionsResponse)
async def get_reddit_mentions(
    background_tasks: BackgroundTasks,
    title: str = Query(...),
    title_id: UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> MentionsResponse:
    """Get Reddit mentions for a title."""
    return await _get_mentions_for_platform(
        "reddit",
        background_tasks,
        title,
        title_id,
        limit,
        offset,
    )


@router.get("/mentions/youtube", response_model=MentionsResponse)
async def get_youtube_mentions(
    background_tasks: BackgroundTasks,
    title: str = Query(...),
    title_id: UUID | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> MentionsResponse:
    """Get YouTube mentions for a title (videos and comments)."""
    return await _get_mentions_for_platform(
        "youtube",
        background_tasks,
        title,
        title_id,
        limit,
        offset,
    )


@router.get("/mentions/bluesky", response_model=MentionsResponse)
async def get_bluesky_mentions(
    background_tasks: BackgroundTasks,
    title: str = Query(...),
    title_id: UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> MentionsResponse:
    """Get Bluesky posts for a title."""
    return await _get_mentions_for_platform(
        "bluesky",
        background_tasks,
        title,
        title_id,
        limit,
        offset,
    )
