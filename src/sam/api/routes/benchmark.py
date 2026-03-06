"""Historical benchmark endpoint."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from sam.api import dependencies as deps
from sam.api.routes.metrics_routes import _snapshot_from_row
from sam.api.schemas import BenchmarkResponse
from sam.storage.models import Title

router = APIRouter(prefix="/api/v1/metrics", tags=["benchmark"])


@router.get("/benchmark", response_model=BenchmarkResponse)
async def benchmark_title(
    title_id: UUID = Query(..., description="Title UUID to benchmark"),
    comparison_type: Literal["movie", "tv"] = Query(
        "movie", description="media_type of comparison titles"
    ),
    days: int = Query(7, ge=1, le=90, description="Days from release to compare"),
    window_hours: int = Query(24, ge=1, le=168),
    comparison_limit: int = Query(20, ge=1, le=50, description="Max comparison titles"),
) -> dict[str, Any]:
    """Compare a title's first-N-days trajectory against averaged peers.

    Returns the target title's daily attention trajectory plus the averaged
    trajectory of all other titles of the same media_type, enabling
    "vs. average" benchmarking.
    """
    async with deps.get_session() as session:
        target = await deps.get_title_by_id(session, title_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Title not found")

        if not target.release_date:
            raise HTTPException(
                status_code=400, detail="Title has no release date for benchmarking"
            )

        # Target title trajectory
        since = target.release_date
        until = target.release_date + timedelta(days=days)

        target_points = await deps.get_metrics_timeseries(
            session,
            title_id=title_id,
            window_hours=window_hours,
            since=since,
            until=until,
        )

        # Find comparison titles (same media_type, different title, have release_date)
        comp_stmt = (
            select(Title)
            .where(
                Title.media_type == comparison_type,
                Title.id != title_id,
                Title.release_date.isnot(None),
                Title.is_active.is_(True),
            )
            .order_by(Title.popularity.desc())
            .limit(comparison_limit)
        )
        comp_result = await session.execute(comp_stmt)
        comp_titles = comp_result.scalars().all()

        avg_rows = await deps.get_average_benchmark_trajectory(
            session,
            target_title_id=title_id,
            comparison_type=comparison_type,
            window_hours=window_hours,
            days=days,
            comparison_limit=comparison_limit,
        )
        comparison_titles_with_data = await deps.get_benchmark_contributors_count(
            session,
            target_title_id=title_id,
            comparison_type=comparison_type,
            window_hours=window_hours,
            days=days,
            comparison_limit=comparison_limit,
        )
        avg_by_day = {int(row["day"]): row for row in avg_rows}
        avg_trajectory: list[dict[str, Any]] = []
        for day in range(days):
            row = avg_by_day.get(day)
            avg_trajectory.append(
                {
                    "day": day,
                    "avg_attention_index": (
                        float(row["avg_attention_index"])
                        if row and row.get("avg_attention_index") is not None
                        else None
                    ),
                    "sample_count": int(row["sample_count"]) if row else 0,
                }
            )

    return {
        "title": deps.title_from_row(target).model_dump(),
        "days": days,
        "window_hours": window_hours,
        "target_points": [_snapshot_from_row(p).model_dump() for p in target_points],
        "avg_trajectory": avg_trajectory,
        "comparison_count": len(comp_titles),
        "comparison_titles_with_data": comparison_titles_with_data,
    }
