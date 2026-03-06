"""Compare titles endpoint — parallel timeseries for multi-title analysis."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from sam.api import dependencies as deps
from sam.api.routes.metrics_routes import _snapshot_from_row
from sam.api.schemas import CompareResponse

router = APIRouter(prefix="/api/v1/metrics", tags=["compare"])


@router.get("/compare", response_model=CompareResponse)
async def compare_titles(
    title_ids: str = Query(
        ...,
        description="Comma-separated title UUIDs (2-5 titles)",
    ),
    window_hours: int = Query(24, ge=1, le=168),
    hours: int = Query(168, ge=1, le=24 * 30),
) -> dict[str, Any]:
    """Return parallel timeseries data for multiple titles.

    Enables side-by-side comparison of attention index, velocity, and
    sentiment across titles.
    """
    raw_ids = [s.strip() for s in title_ids.split(",") if s.strip()]
    if len(raw_ids) < 2:
        raise HTTPException(status_code=400, detail="At least 2 title_ids required")
    if len(raw_ids) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 title_ids allowed")

    try:
        parsed_ids = [UUID(tid) for tid in raw_ids]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid UUID: {exc}") from exc

    now = datetime.now(UTC)
    since = now - timedelta(hours=hours)
    until = now + timedelta(hours=1)

    series: list[dict[str, Any]] = []
    missing_ids: list[str] = []

    async with deps.get_session() as session:
        for tid in parsed_ids:
            title_row = await deps.get_title_by_id(session, tid)
            if title_row is None:
                missing_ids.append(str(tid))
                continue

            points = await deps.get_metrics_timeseries(
                session,
                title_id=tid,
                window_hours=window_hours,
                since=since,
                until=until,
            )

            series.append(
                {
                    "title": deps.title_from_row(title_row).model_dump(),
                    "points": [_snapshot_from_row(p).model_dump() for p in points],
                }
            )

    return {
        "window_hours": window_hours,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "series": series,
        "missing_ids": missing_ids,
    }
