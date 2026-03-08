"""Metrics trending, timeseries, Prometheus text, and JSON endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
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
from sam.storage.models import PipelineRun

router = APIRouter(tags=["metrics"])


def _snapshot_from_row(m: Any) -> MetricsSnapshotResponse:
    """Build a ``MetricsSnapshotResponse`` from a metrics ORM row."""
    raw_metrics = getattr(m, "raw_metrics", None)
    mentions_capped = (
        bool(raw_metrics.get("mentions_capped")) if isinstance(raw_metrics, dict) else False
    )
    mentions_fetch_limit: int | None = None
    if isinstance(raw_metrics, dict):
        raw_limit = raw_metrics.get("mentions_fetch_limit")
        if isinstance(raw_limit, int):
            mentions_fetch_limit = raw_limit
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
        mentions_capped=mentions_capped,
        mentions_fetch_limit=mentions_fetch_limit,
        is_approximate=mentions_capped,
        raw_metrics=raw_metrics,
    )


def _collector_prometheus_text(runs: list[PipelineRun]) -> str:
    status_counts: dict[tuple[str, str], int] = {}
    last_started: dict[str, datetime] = {}
    mentions_by_platform = {"reddit": 0, "youtube": 0, "bluesky": 0}
    youtube_quota_units = 0
    youtube_quota_calls = 0

    for run in runs:
        job_name = run.job_name or "unknown"
        status = run.status or "unknown"
        status_counts[(job_name, status)] = status_counts.get((job_name, status), 0) + 1
        started_at = run.started_at
        if started_at is not None:
            current = last_started.get(job_name)
            if current is None or started_at > current:
                last_started[job_name] = started_at

        stats = run.stats or {}
        mentions_by_platform["reddit"] += int(stats.get("reddit_mentions_inserted", 0) or 0)
        mentions_by_platform["youtube"] += int(stats.get("youtube_mentions_inserted", 0) or 0)
        mentions_by_platform["youtube"] += int(stats.get("youtube_comments_inserted", 0) or 0)
        mentions_by_platform["bluesky"] += int(stats.get("bluesky_mentions_inserted", 0) or 0)

        api_quota = stats.get("api_quota") if isinstance(stats, dict) else None
        if isinstance(api_quota, dict):
            youtube = api_quota.get("youtube", {})
            if isinstance(youtube, dict):
                youtube_quota_units += int(youtube.get("total_units", 0) or 0)
                youtube_quota_calls += int(youtube.get("total_calls", 0) or 0)

    lines = [
        "# TYPE sam_pipeline_runs_total counter",
        "# TYPE sam_collector_mentions_inserted_total counter",
        "# TYPE sam_youtube_quota_units_total counter",
        "# TYPE sam_youtube_quota_calls_total counter",
        "# TYPE sam_pipeline_last_started_timestamp_seconds gauge",
    ]
    for (job_name, status), count in sorted(status_counts.items()):
        lines.append(f'sam_pipeline_runs_total{{job_name="{job_name}",status="{status}"}} {count}')
    for platform, count in mentions_by_platform.items():
        lines.append(f'sam_collector_mentions_inserted_total{{platform="{platform}"}} {count}')
    lines.append(f"sam_youtube_quota_units_total {youtube_quota_units}")
    lines.append(f"sam_youtube_quota_calls_total {youtube_quota_calls}")
    for job_name, started_at in sorted(last_started.items()):
        lines.append(
            "sam_pipeline_last_started_timestamp_seconds"
            f'{{job_name="{job_name}"}} {int(started_at.timestamp())}'
        )
    return "\n".join(lines)


@router.get("/api/v1/metrics/trending", response_model=TrendingMetricsResponse)
async def metrics_trending(
    window_hours: int = Query(24, ge=1, le=168),
    limit: int = Query(10, ge=1, le=50),
) -> TrendingMetricsResponse:
    settings = get_settings()
    now = datetime.now(UTC)
    cache_key = (
        f"sam:metrics:trending:{window_hours}:{limit}:{settings.fresh_snapshot_window_hours}"
    )
    cached = await deps.cache_get_json(cache_key)
    if isinstance(cached, dict) and "items" in cached:
        return TrendingMetricsResponse(**cached)

    async with deps.get_session() as session:
        rows = await deps.get_trending_by_attention_index(
            session,
            window_hours=window_hours,
            limit=limit,
            fresh_since=now - timedelta(hours=settings.fresh_snapshot_window_hours),
        )

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
                    is_active=bool(t.is_active),
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
    async with deps.get_session() as session:
        result = await session.execute(select(PipelineRun).order_by(PipelineRun.started_at.desc()))
        runs = list(result.scalars().all())

    local_metrics = prometheus_text().rstrip()
    collector_metrics = _collector_prometheus_text(runs).rstrip()
    content = "\n".join(part for part in (local_metrics, collector_metrics) if part) + "\n"
    return Response(content=content, media_type="text/plain; charset=utf-8")


@router.get("/api/v1/metrics/app")
async def get_metrics_json() -> dict[str, Any]:
    """Expose application metrics as JSON for internal dashboards."""
    return metrics_snapshot()
