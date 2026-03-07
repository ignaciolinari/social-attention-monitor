"""Box office correlation endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query

from sam.api import dependencies as deps
from sam.api.schemas import BoxOfficeResponse
from sam.config import get_settings

router = APIRouter(prefix="/api/v1/metrics", tags=["box-office"])


@router.get("/box-office", response_model=BoxOfficeResponse)
async def box_office_correlation(
    window_hours: int = Query(24, ge=1, le=168),
    limit: int = Query(20, ge=1, le=50),
) -> dict[str, Any]:
    """Return titles with revenue/budget alongside latest attention index.

    Enables scatter-plot correlations between social attention and commercial
    performance.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    cache_key = f"sam:box-office:{window_hours}:{limit}:{settings.fresh_snapshot_window_hours}"
    cached = await deps.cache_get_json(cache_key)
    if isinstance(cached, dict) and "items" in cached:
        return cached

    async with deps.get_session() as session:
        rows = await deps.get_trending_by_attention_index(
            session,
            window_hours=window_hours,
            limit=limit,
            fresh_since=now - timedelta(hours=settings.fresh_snapshot_window_hours),
        )

    items: list[dict[str, Any]] = []
    for t, m in rows:
        items.append(
            {
                "title_id": str(t.id),
                "title": t.title,
                "media_type": t.media_type,
                "release_date": t.release_date.isoformat() if t.release_date else None,
                "revenue": t.revenue,
                "budget": t.budget,
                "attention_index": m.attention_index,
                "mention_count": m.mention_count,
                "avg_sentiment": m.avg_sentiment,
            }
        )

    payload = {
        "window_hours": window_hours,
        "collected_at": now.isoformat(),
        "items": items,
    }
    await deps.cache_set_json(cache_key, payload, ttl_seconds=settings.cache_ttl_metrics)
    return payload
