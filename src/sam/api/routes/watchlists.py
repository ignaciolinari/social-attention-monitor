"""Watchlists CRUD endpoints."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from sam.api import dependencies as deps
from sam.api.schemas import (
    WatchlistCreateRequest,
    WatchlistResponse,
    WatchlistsListResponse,
)
from sam.collectors.tmdb import TMDBCollector
from sam.config import get_settings
from sam.storage.models import Watchlist

router = APIRouter(prefix="/api/v1", tags=["watchlists"])


def _normalize_tmdb_ids(values: list[int]) -> list[int]:
    """Return unique positive TMDB IDs while preserving input order."""
    seen: set[int] = set()
    normalized: list[int] = []
    for value in values:
        if value < 1 or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _watchlist_response(w: Watchlist) -> WatchlistResponse:
    tmdb_ids = w.tmdb_ids if isinstance(w.tmdb_ids, list) else []
    return WatchlistResponse(
        id=str(w.id),
        name=w.name,
        tmdb_ids=tmdb_ids,
        created_at=w.created_at.isoformat() if w.created_at else "",
        updated_at=w.updated_at.isoformat() if w.updated_at else "",
    )


async def _validate_tmdb_ids_exist(tmdb_ids: list[int]) -> tuple[list[int], list[int]]:
    """Validate TMDB IDs when API access is available.

    Returns (valid_ids, unknown_ids). If TMDB is unavailable or probe errors
    occur, IDs are preserved to avoid rejecting writes during transient outages.
    """
    if not tmdb_ids:
        return [], []

    settings = get_settings()
    if not settings.demo_mode and not settings.tmdb.is_configured:
        return tmdb_ids, []

    collector = TMDBCollector(demo_mode=settings.demo_mode)
    try:

        async def _exists(tmdb_id: int) -> int | None:
            for attempt, media_type in enumerate(("movie", "tv")):
                details = await collector.get_details(
                    tmdb_id,
                    media_type=media_type,
                    suppress_not_found_error=attempt == 0,
                )
                if details is not None:
                    return tmdb_id
            return None

        results = await asyncio.gather(*[_exists(tid) for tid in tmdb_ids], return_exceptions=True)
    finally:
        with contextlib.suppress(Exception):
            await collector.close()

    valid_ids: list[int] = []
    unknown_ids: list[int] = []
    for tid, result in zip(tmdb_ids, results, strict=True):
        if isinstance(result, Exception):
            valid_ids.append(tid)
            continue
        if result is None:
            unknown_ids.append(tid)
            continue
        valid_ids.append(tid)
    return valid_ids, unknown_ids


@router.get("/watchlists", response_model=WatchlistsListResponse)
async def list_watchlists(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> WatchlistsListResponse:
    """List all watchlists."""
    async with deps.get_session() as session:
        count_result = await session.execute(select(func.count()).select_from(Watchlist))
        total = int(count_result.scalar_one())

        result = await session.execute(
            select(Watchlist).order_by(Watchlist.created_at.desc()).limit(limit).offset(offset)
        )
        rows = result.scalars().all()

    return WatchlistsListResponse(
        watchlists=[_watchlist_response(w) for w in rows],
        total_count=total,
    )


@router.post("/watchlists", response_model=WatchlistResponse, status_code=201)
async def create_watchlist(body: WatchlistCreateRequest) -> WatchlistResponse:
    """Create a new watchlist."""
    tmdb_ids = _normalize_tmdb_ids(body.tmdb_ids)
    tmdb_ids, unknown_ids = await _validate_tmdb_ids_exist(tmdb_ids)
    if unknown_ids:
        raise HTTPException(
            status_code=422,
            detail={"error": "unknown_tmdb_ids", "tmdb_ids": unknown_ids},
        )
    async with deps.get_session() as session:
        w = Watchlist(
            name=body.name,
            tmdb_ids=tmdb_ids,
        )
        session.add(w)
        await session.commit()
        await session.refresh(w)
    return _watchlist_response(w)


@router.put("/watchlists/{watchlist_id}", response_model=WatchlistResponse)
async def update_watchlist(
    watchlist_id: uuid.UUID,
    body: WatchlistCreateRequest,
) -> WatchlistResponse:
    """Update an existing watchlist."""
    tmdb_ids = _normalize_tmdb_ids(body.tmdb_ids)
    tmdb_ids, unknown_ids = await _validate_tmdb_ids_exist(tmdb_ids)
    if unknown_ids:
        raise HTTPException(
            status_code=422,
            detail={"error": "unknown_tmdb_ids", "tmdb_ids": unknown_ids},
        )
    async with deps.get_session() as session:
        w = await session.get(Watchlist, watchlist_id)
        if w is None:
            raise HTTPException(status_code=404, detail="Watchlist not found")
        w.name = body.name
        w.tmdb_ids = tmdb_ids
        w.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(w)
    return _watchlist_response(w)


@router.delete("/watchlists/{watchlist_id}")
async def delete_watchlist(watchlist_id: uuid.UUID) -> dict[str, Any]:
    """Delete a watchlist."""
    async with deps.get_session() as session:
        w = await session.get(Watchlist, watchlist_id)
        if w is None:
            raise HTTPException(status_code=404, detail="Watchlist not found")
        await session.delete(w)
        await session.commit()
    return {"deleted": True, "id": str(watchlist_id)}
