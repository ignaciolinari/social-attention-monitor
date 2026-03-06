"""Language breakdown endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from sam.api import dependencies as deps
from sam.api.schemas import LanguageBreakdownResponse
from sam.config import get_settings
from sam.storage.models import Mention

router = APIRouter(prefix="/api/v1/metrics", tags=["language"])


@router.get("/language-breakdown", response_model=LanguageBreakdownResponse)
async def language_breakdown(
    title_id: UUID = Query(..., description="Title UUID"),
    hours: int = Query(24, ge=1, le=24 * 30, description="Time window in hours"),
) -> dict[str, Any]:
    """Aggregate mentions by detected language with per-language sentiment.

    Returns the distribution of mention languages and average sentiment
    for each language, enabling geographic/linguistic segmentation analysis.
    """
    async with deps.get_session() as session:
        title = await deps.get_title_by_id(session, title_id)
        if title is None:
            raise HTTPException(status_code=404, detail="Title not found")

        settings = get_settings()
        cache_key = f"sam:lang-breakdown:{title_id}:{hours}"
        cached = await deps.cache_get_json(cache_key)
        if isinstance(cached, dict) and "languages" in cached:
            return cached

        since = datetime.now(UTC) - timedelta(hours=hours)

        stmt = (
            select(
                Mention.detected_language,
                func.count().label("mention_count"),
                func.avg(
                    func.cast(
                        Mention.sentiment["compound"].astext,
                        sa.Float,
                    )
                ).label("avg_sentiment"),
            )
            .where(
                Mention.title_id == title_id,
                Mention.collected_at >= since,
                Mention.detected_language.isnot(None),
            )
            .group_by(Mention.detected_language)
            .order_by(func.count().desc())
        )
        result = await session.execute(stmt)
        rows = result.all()

    languages = [
        {
            "language": row.detected_language,
            "mention_count": row.mention_count,
            "avg_sentiment": (
                round(float(row.avg_sentiment), 4) if row.avg_sentiment is not None else None
            ),
        }
        for row in rows
    ]

    payload = {
        "title_id": str(title_id),
        "hours": hours,
        "collected_at": datetime.now(UTC).isoformat(),
        "languages": languages,
    }
    await deps.cache_set_json(cache_key, payload, ttl_seconds=settings.cache_ttl_metrics)
    return payload
