"""Pipeline health, quota, and run-history endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query

from sam.api import dependencies as deps
from sam.api.schemas import (
    ApiQuotaInfo,
    PipelineHealthResponse,
    PipelineQuotaResponse,
    PipelineRunDetail,
    PipelineRunInfo,
    PipelineRunsResponse,
    PipelineSentimentStats,
)
from sam.config import get_settings

router = APIRouter(prefix="/api/v1/pipeline", tags=["pipeline"])


@router.get("/health", response_model=PipelineHealthResponse)
async def pipeline_health() -> PipelineHealthResponse:
    """Pipeline health: newest mention age, per-platform counts, latest run status."""
    settings = get_settings()
    cache_key = f"sam:pipeline:health:{int(settings.demo_mode)}"
    cached = await deps.cache_get_json(cache_key)
    if isinstance(cached, dict) and "timestamp" in cached:
        return PipelineHealthResponse(**cached)

    api_quota: dict[str, ApiQuotaInfo] = {}
    async with deps.get_session() as session:
        stats = await deps.get_pipeline_health_stats(session)
        quota = await deps.aggregate_youtube_quota_from_db(session)
        api_quota["youtube"] = ApiQuotaInfo(**quota.to_api_dict())

    sentiment_stats: PipelineSentimentStats | None = None
    for run in stats["latest_pipeline_runs"]:
        if run.get("job_name") != "collector-cycle":
            continue
        run_stats = run.get("stats")
        if not isinstance(run_stats, dict):
            continue
        sentiment_stats = PipelineSentimentStats(
            translate_attempted=int(run_stats.get("translate_attempted", 0) or 0),
            translate_count=int(run_stats.get("translate_count", 0) or 0),
            translate_failures=int(run_stats.get("translate_failures", 0) or 0),
            translate_skipped_english=int(run_stats.get("translate_skipped_english", 0) or 0),
            sentiment_ms_total=float(run_stats.get("sentiment_ms_total", 0.0) or 0.0),
        )
        break

    payload = PipelineHealthResponse(
        timestamp=stats["timestamp"],
        active_titles=stats["active_titles"],
        total_mentions=stats["total_mentions"],
        mentions_last_24h=stats["mentions_last_24h"],
        newest_mention_age_seconds=stats["newest_mention_age_seconds"],
        newest_mention_at=stats["newest_mention_at"],
        latest_pipeline_runs=[PipelineRunInfo(**run) for run in stats["latest_pipeline_runs"]],
        sentiment_stats=sentiment_stats,
        api_quota=api_quota,
    )
    await deps.cache_set_json(
        cache_key, payload.model_dump(), ttl_seconds=settings.cache_ttl_pipeline_health
    )
    return payload


@router.get("/quota", response_model=PipelineQuotaResponse)
async def pipeline_quota() -> PipelineQuotaResponse:
    """API quota usage summary (YouTube daily budget)."""
    quota = await deps.aggregate_youtube_quota_from_db()
    return PipelineQuotaResponse(
        youtube=ApiQuotaInfo(**quota.to_api_dict()),
        last_run_at=quota.last_run_at,
    )


@router.get("/runs", response_model=PipelineRunsResponse)
async def pipeline_runs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, description="Filter by status"),
) -> PipelineRunsResponse:
    """Paginated pipeline run history."""
    async with deps.get_session() as session:
        data = await deps.get_pipeline_runs(session, limit=limit, offset=offset, status=status)
    return PipelineRunsResponse(
        runs=[PipelineRunDetail(**r) for r in data["runs"]],
        total=data["total"],
        limit=data["limit"],
        offset=data["offset"],
    )
