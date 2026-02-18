"""Metrics snapshot computation + persistence helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from sam.processors.metrics import EngagementMetrics, MentionData, get_calculator
from sam.storage.models import Mention
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
    primary_model_counts = _aggregate_primary_sentiment_models(mentions)
    secondary_sentiment = _aggregate_secondary_sentiment(mentions)

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
                "sentiment_primary_model": _dominant_model(primary_model_counts),
                "sentiment_primary_model_counts": primary_model_counts,
                "sentiment_secondary": secondary_sentiment,
            },
        },
    )


def _aggregate_primary_sentiment_models(mentions: list[Mention]) -> dict[str, int]:
    """Count primary sentiment model occurrences in mention payloads."""
    counts: dict[str, int] = {}
    for mention in mentions:
        sentiment = mention.sentiment
        if not sentiment or not isinstance(sentiment, dict):
            continue
        model = sentiment.get("model")
        if isinstance(model, str) and model:
            normalized_model = "vader" if model == "both" else model
            counts[normalized_model] = counts.get(normalized_model, 0) + 1
    return counts


def _dominant_model(model_counts: dict[str, int]) -> str | None:
    """Return the most frequent model name from counts."""
    if not model_counts:
        return None
    return max(model_counts.items(), key=lambda item: item[1])[0]


def _aggregate_secondary_sentiment(mentions: list[Mention]) -> dict[str, Any] | None:
    """
    Aggregate sentiment stats for the secondary model (e.g. RoBERTa) if present.

    Returns dict with avg_sentiment, positive_ratio, etc. or None if no secondary data.
    """
    scores_by_model: dict[str, list[float]] = {}
    pos_count_by_model: dict[str, int] = {}
    neg_count_by_model: dict[str, int] = {}
    total_by_model: dict[str, int] = {}

    for m in mentions:
        if not m.sentiment or not isinstance(m.sentiment, dict):
            continue

        # Check if we have secondary sentiment data stored in 'extra'
        # The structure matches SentimentResult.extra -> {"roberta": {...}}
        extra = m.sentiment.get("extra")
        if not extra or not isinstance(extra, dict):
            continue

        for model_name, secondary in extra.items():
            if not isinstance(model_name, str) or not model_name:
                continue
            if not secondary or not isinstance(secondary, dict):
                continue

            compound = secondary.get("compound")
            if compound is None:
                continue
            try:
                score = float(compound)
            except (TypeError, ValueError):
                continue

            scores_by_model.setdefault(model_name, []).append(score)
            total_by_model[model_name] = total_by_model.get(model_name, 0) + 1
            if score >= 0.05:
                pos_count_by_model[model_name] = pos_count_by_model.get(model_name, 0) + 1
            elif score <= -0.05:
                neg_count_by_model[model_name] = neg_count_by_model.get(model_name, 0) + 1

    if not total_by_model:
        return None

    per_model_summary: dict[str, dict[str, Any]] = {}
    for model_name, count in total_by_model.items():
        scores = scores_by_model.get(model_name, [])
        avg_sentiment = (sum(scores) / count) if count > 0 else 0.0
        pos_count = pos_count_by_model.get(model_name, 0)
        neg_count = neg_count_by_model.get(model_name, 0)
        per_model_summary[model_name] = {
            "avg_sentiment": avg_sentiment,
            "positive_ratio": pos_count / count if count > 0 else 0.0,
            "negative_ratio": neg_count / count if count > 0 else 0.0,
            "count": count,
        }

    preferred_model = (
        "roberta" if "roberta" in per_model_summary else _dominant_model(total_by_model)
    )
    if preferred_model is None:
        return None
    preferred_summary = per_model_summary[preferred_model]

    payload: dict[str, Any] = {
        "model": preferred_model,
        "avg_sentiment": preferred_summary["avg_sentiment"],
        "positive_ratio": preferred_summary["positive_ratio"],
        "negative_ratio": preferred_summary["negative_ratio"],
        "count": preferred_summary["count"],
    }
    if len(per_model_summary) > 1:
        payload["models"] = per_model_summary

    return payload
