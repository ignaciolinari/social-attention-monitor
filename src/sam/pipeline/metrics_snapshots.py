"""Metrics snapshot computation + persistence helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from sam.processors.metrics import EngagementMetrics, MentionData, get_calculator
from sam.storage.repository import (
    get_latest_metrics_snapshot,
    get_mentions_in_window,
    upsert_metrics_snapshot,
)


async def compute_and_upsert_metrics_snapshot(
    session: AsyncSession,
    *,
    title_id: uuid.UUID,
    snapshot_time: datetime,
    window_hours: int,
) -> None:
    """Compute metrics for a title/window and upsert into `metrics_snapshots`."""
    calc = get_calculator()
    window_start = snapshot_time - timedelta(hours=window_hours)

    mentions = await get_mentions_in_window(
        session,
        title_id=title_id,
        window_start=window_start,
        window_end=snapshot_time,
    )

    previous = await get_latest_metrics_snapshot(
        session,
        title_id=title_id,
        window_hours=window_hours,
        before=snapshot_time,
    )

    previous_metrics: EngagementMetrics | None = None
    if previous is not None:
        prev_positive = float(previous.positive_ratio or 0.0)
        prev_negative = float(previous.negative_ratio or 0.0)

        # Reconstruct per-platform breakdown from the stored columns.
        prev_breakdown: dict[str, int] = {}
        if previous.reddit_mentions:
            prev_breakdown["reddit"] = previous.reddit_mentions
        if previous.youtube_mentions:
            prev_breakdown["youtube"] = previous.youtube_mentions
        if previous.bluesky_mentions:
            prev_breakdown["bluesky"] = previous.bluesky_mentions

        previous_metrics = EngagementMetrics(
            mention_count=previous.mention_count,
            unique_authors=previous.unique_authors,
            total_engagement=previous.total_engagement or 0,
            mention_velocity=float(previous.mention_velocity or 0.0),
            velocity_change=float(previous.velocity_change or 0.0),
            avg_sentiment=float(previous.avg_sentiment or 0.0),
            sentiment_volatility=float(previous.sentiment_volatility or 0.0),
            positive_ratio=prev_positive,
            negative_ratio=prev_negative,
            attention_index=float(previous.attention_index or 0.0),
            hype_acceleration=float(previous.hype_acceleration or 0.0),
            window_start=window_start,
            window_end=snapshot_time,
            platform_breakdown=prev_breakdown,
        )

    mention_dicts = cast(
        list[MentionData],
        [
            {
                # Use collected_at for windowing: this is when the pipeline
                # ingested the mention, not when the content was published.
                "created_at": m.collected_at,
                "platform": m.platform,
                "author": m.author,
                "metrics": m.metrics or {},
                "sentiment": m.sentiment or {},
            }
            for m in mentions
        ],
    )

    computed = calc.calculate(
        mention_dicts,
        window_hours=window_hours,
        previous_metrics=previous_metrics,
        window_end=snapshot_time,
    )

    await upsert_metrics_snapshot(
        session,
        title_id=title_id,
        snapshot_time=snapshot_time,
        window_hours=window_hours,
        metrics={
            "mention_count": computed.mention_count,
            "unique_authors": computed.unique_authors,
            "reddit_mentions": computed.platform_breakdown.get("reddit", 0),
            "youtube_mentions": computed.platform_breakdown.get("youtube", 0),
            "bluesky_mentions": computed.platform_breakdown.get("bluesky", 0),
            "total_engagement": computed.total_engagement,
            "mention_velocity": computed.mention_velocity,
            "velocity_change": computed.velocity_change,
            "avg_sentiment": computed.avg_sentiment,
            "sentiment_volatility": computed.sentiment_volatility,
            "positive_ratio": computed.positive_ratio,
            "negative_ratio": computed.negative_ratio,
            "attention_index": computed.attention_index,
            "hype_acceleration": computed.hype_acceleration,
            "raw_metrics": {
                "window_start": computed.window_start.isoformat(),
                "window_end": computed.window_end.isoformat(),
                "platform_breakdown": computed.platform_breakdown,
            },
        },
    )
