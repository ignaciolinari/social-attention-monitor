"""Metrics trending, timeseries, Prometheus text, and JSON endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from starlette.responses import Response

from sam.api import dependencies as deps
from sam.api.metrics import prometheus_text
from sam.api.metrics import snapshot as metrics_snapshot
from sam.api.schemas import (
    DbTitleResponse,
    MetricsSnapshotResponse,
    MetricsTimeseriesResponse,
    TrendingMetricsItem,
    TrendingMetricsResponse,
)
from sam.config import get_settings

router = APIRouter(tags=["metrics"])


def _snapshot_from_row(m: Any) -> MetricsSnapshotResponse:
    """Build a ``MetricsSnapshotResponse`` from a metrics ORM row."""
    return MetricsSnapshotResponse(
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
    )


@router.get("/api/v1/metrics/trending", response_model=TrendingMetricsResponse)
async def metrics_trending(
    window_hours: int = Query(24, ge=1, le=168),
    limit: int = Query(10, ge=1, le=50),
) -> TrendingMetricsResponse:
    settings = get_settings()
    cache_key = f"sam:metrics:trending:{window_hours}:{limit}"
    cached = await deps.cache_get_json(cache_key)
    if isinstance(cached, dict) and "items" in cached:
        return TrendingMetricsResponse(**cached)

    async with deps.get_session() as session:
        rows = await deps.get_trending_by_attention_index(
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
                metrics=_snapshot_from_row(m),
            )
            for t, m in rows
        ],
    )
    await deps.cache_set_json(
        cache_key, payload.model_dump(), ttl_seconds=settings.cache_ttl_metrics
    )
    return payload


@router.get("/api/v1/metrics/timeseries", response_model=MetricsTimeseriesResponse)
async def metrics_timeseries(
    title_id: UUID = Query(
        ..., description="Title UUID from /api/v1/db/titles or /api/v1/metrics/trending"
    ),
    window_hours: int = Query(1, ge=1, le=168),
    hours: int = Query(24, ge=1, le=24 * 30),
) -> MetricsTimeseriesResponse:
    now = datetime.now(UTC)
    until = now + timedelta(hours=1)
    since = now - timedelta(hours=hours)

    async with deps.get_session() as session:
        row = await deps.get_title_by_id(session, title_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Title not found")

        title_resp = deps.title_from_row(row)

        points = await deps.get_metrics_timeseries(
            session,
            title_id=title_id,
            window_hours=window_hours,
            since=since,
            until=until,
        )

    return MetricsTimeseriesResponse(
        title=title_resp,
        window_hours=window_hours,
        since=since.isoformat(),
        until=until.isoformat(),
        points=[_snapshot_from_row(p) for p in points],
    )


@router.get("/metrics")
async def get_metrics() -> Response:
    """Expose application metrics in Prometheus text exposition format."""
    return Response(content=prometheus_text(), media_type="text/plain; charset=utf-8")


@router.get("/api/v1/metrics/app")
async def get_metrics_json() -> dict[str, Any]:
    """Expose application metrics as JSON for internal dashboards."""
    return metrics_snapshot()
