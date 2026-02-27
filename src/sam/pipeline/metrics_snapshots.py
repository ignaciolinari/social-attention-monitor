"""Metrics snapshot computation + persistence helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, cast

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from sam.config import get_settings
from sam.processors.keywords import extract_hashtags, extract_keywords
from sam.processors.metrics import EngagementMetrics, MentionData, get_calculator
from sam.storage.models import Mention
from sam.storage.repository import (
    get_latest_metrics_snapshot,
    get_mentions_in_window,
    upsert_metrics_snapshot,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def compute_and_upsert_metrics_snapshot(
    session: AsyncSession,
    *,
    title_id: uuid.UUID,
    snapshot_time: datetime,
    window_hours: int,
) -> None:
    """Compute metrics for a single title/window and upsert into `metrics_snapshots`.

    Prefer :func:`compute_and_upsert_metrics_snapshots_multi` when computing
    multiple window sizes for the same title — it shares the mention query.
    """
    await compute_and_upsert_metrics_snapshots_multi(
        session,
        title_id=title_id,
        snapshot_time=snapshot_time,
        window_hours_list=[window_hours],
    )


async def compute_and_upsert_metrics_snapshots_multi(
    session: AsyncSession,
    *,
    title_id: uuid.UUID,
    snapshot_time: datetime,
    window_hours_list: list[int],
) -> int:
    """Compute and upsert metrics for *multiple* window sizes.

    Fetches mentions once (for the largest window) and filters in-memory
    for smaller windows, halving DB load compared to calling the single-
    window function repeatedly.

    Returns the number of snapshots upserted.
    """
    if not window_hours_list:
        return 0
    normalized_windows = sorted({int(w) for w in window_hours_list})
    if any(w <= 0 for w in normalized_windows):
        raise ValueError("window_hours_list must contain only positive integers")

    calc = get_calculator()
    settings = get_settings()
    enable_keywords = getattr(settings, "enable_keyword_extraction", True)

    # Fetch mentions for the largest window once.
    max_window = max(normalized_windows)
    max_window_start = snapshot_time - timedelta(hours=max_window)
    mention_fetch_limit = 10_000
    all_mentions = await get_mentions_in_window(
        session,
        title_id=title_id,
        window_start=max_window_start,
        window_end=snapshot_time,
        limit=mention_fetch_limit,
    )
    mentions_capped = len(all_mentions) >= mention_fetch_limit
    if mentions_capped:
        logger.warning(
            f"[metrics_snapshots] Mention window capped at {mention_fetch_limit} rows "
            f"for title_id={title_id}, window={max_window}h at {snapshot_time.isoformat()}"
        )

    count = 0
    for window_hours in normalized_windows:
        window_start = snapshot_time - timedelta(hours=window_hours)

        # Filter in-memory for windows smaller than the max.
        if window_hours < max_window:
            mentions = [m for m in all_mentions if m.collected_at >= window_start]
        else:
            mentions = all_mentions

        previous = await get_latest_metrics_snapshot(
            session,
            title_id=title_id,
            window_hours=window_hours,
            before=snapshot_time,
        )

        previous_metrics = _reconstruct_previous_metrics(
            previous, window_start=window_start, window_end=snapshot_time
        )

        mention_dicts = cast(
            list[MentionData],
            [
                {
                    "created_at": m.collected_at,
                    "platform": m.platform,
                    "author": m.author,
                    "source_type": m.source_type or "post",
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

        # Single-pass sentiment model aggregation (B8 optimisation).
        primary_model_counts, secondary_sentiment = _aggregate_sentiment_models(mentions)

        keyword_signals = _extract_keyword_signals(mentions) if enable_keywords else None

        raw_metrics: dict[str, Any] = {
            "window_start": computed.window_start.isoformat(),
            "window_end": computed.window_end.isoformat(),
            "mentions_fetch_limit": mention_fetch_limit,
            "mentions_capped": mentions_capped,
            "platform_breakdown": computed.platform_breakdown,
            "sentiment_primary_model": _dominant_model(primary_model_counts),
            "sentiment_primary_model_counts": primary_model_counts,
            "sentiment_secondary": secondary_sentiment,
            "engagement_weighted_sentiment": computed.engagement_weighted_sentiment,
            "sentiment_divergence": computed.sentiment_divergence,
            "sentiment_momentum": computed.sentiment_momentum,
            "audience_fatigue_index": computed.audience_fatigue_index,
            "viral_coefficient": computed.viral_coefficient,
            "author_diversity_score": computed.author_diversity_score,
            "repeat_author_ratio": computed.repeat_author_ratio,
            "creator_sentiment": computed.creator_sentiment,
            "audience_sentiment": computed.audience_sentiment,
        }
        if keyword_signals is not None:
            raw_metrics["keyword_signals"] = keyword_signals

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
                "raw_metrics": raw_metrics,
            },
        )
        count += 1

    return count


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _reconstruct_previous_metrics(
    previous: Any | None,
    *,
    window_start: datetime,
    window_end: datetime,
) -> EngagementMetrics | None:
    """Rebuild an :class:`EngagementMetrics` from a stored ``MetricsSnapshot``."""
    if previous is None:
        return None

    prev_breakdown: dict[str, int] = {}
    if previous.reddit_mentions:
        prev_breakdown["reddit"] = previous.reddit_mentions
    if previous.youtube_mentions:
        prev_breakdown["youtube"] = previous.youtube_mentions
    if previous.bluesky_mentions:
        prev_breakdown["bluesky"] = previous.bluesky_mentions

    raw = previous.raw_metrics or {}
    return EngagementMetrics(
        mention_count=previous.mention_count,
        unique_authors=previous.unique_authors,
        total_engagement=previous.total_engagement or 0,
        mention_velocity=float(previous.mention_velocity or 0.0),
        velocity_change=float(previous.velocity_change or 0.0),
        avg_sentiment=float(previous.avg_sentiment or 0.0),
        sentiment_volatility=float(previous.sentiment_volatility or 0.0),
        positive_ratio=float(previous.positive_ratio or 0.0),
        negative_ratio=float(previous.negative_ratio or 0.0),
        attention_index=float(previous.attention_index or 0.0),
        hype_acceleration=float(previous.hype_acceleration or 0.0),
        engagement_weighted_sentiment=float(raw.get("engagement_weighted_sentiment", 0.0)),
        sentiment_divergence=raw.get("sentiment_divergence", {}),
        sentiment_momentum=float(raw.get("sentiment_momentum", 0.0)),
        audience_fatigue_index=float(raw.get("audience_fatigue_index", 0.0)),
        viral_coefficient=float(raw.get("viral_coefficient", 0.0)),
        author_diversity_score=float(raw.get("author_diversity_score", 0.0)),
        repeat_author_ratio=float(raw.get("repeat_author_ratio", 0.0)),
        creator_sentiment=(float(v) if (v := raw.get("creator_sentiment")) is not None else None),
        audience_sentiment=(float(v) if (v := raw.get("audience_sentiment")) is not None else None),
        window_start=window_start,
        window_end=window_end,
        platform_breakdown=prev_breakdown,
    )


def _extract_keyword_signals(mentions: list[Mention]) -> dict[str, Any] | None:
    """Extract keyword/hashtag signals from mention content for snapshot payloads."""
    texts = [m.content for m in mentions if isinstance(m.content, str) and m.content.strip()]
    if not texts:
        return None

    keywords = extract_keywords(texts, top_n=15)
    hashtags = extract_hashtags(texts, top_n=20)
    if not keywords and not hashtags:
        return None

    return {
        "keywords": [{"term": term, "score": score} for term, score in keywords],
        "hashtags": [{"tag": tag, "count": count} for tag, count in hashtags],
    }


def _aggregate_sentiment_models(
    mentions: list[Mention],
) -> tuple[dict[str, int], dict[str, Any] | None]:
    """Single-pass aggregation of primary + secondary sentiment model stats.

    Returns ``(primary_model_counts, secondary_sentiment_payload)``.
    Replaces the previous ``_aggregate_primary_sentiment_models`` +
    ``_aggregate_secondary_sentiment`` two-pass approach (B8 optimisation).
    """
    # Primary model counts
    primary_counts: dict[str, int] = {}

    # Secondary model accumulators
    scores_by_model: dict[str, list[float]] = {}
    pos_count_by_model: dict[str, int] = {}
    neg_count_by_model: dict[str, int] = {}
    total_by_model: dict[str, int] = {}

    for m in mentions:
        sentiment = m.sentiment
        if not sentiment or not isinstance(sentiment, dict):
            continue

        # --- Primary ---
        model = sentiment.get("model")
        if isinstance(model, str) and model:
            normalized_model = "vader" if model == "both" else model
            primary_counts[normalized_model] = primary_counts.get(normalized_model, 0) + 1

        # --- Secondary ---
        extra = sentiment.get("extra")
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

    # Build secondary payload
    secondary_payload: dict[str, Any] | None = None
    if total_by_model:
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
        if preferred_model is not None:
            preferred_summary = per_model_summary[preferred_model]
            secondary_payload = {
                "model": preferred_model,
                "avg_sentiment": preferred_summary["avg_sentiment"],
                "positive_ratio": preferred_summary["positive_ratio"],
                "negative_ratio": preferred_summary["negative_ratio"],
                "count": preferred_summary["count"],
            }
            if len(per_model_summary) > 1:
                secondary_payload["models"] = per_model_summary

    return primary_counts, secondary_payload


def _dominant_model(model_counts: dict[str, int]) -> str | None:
    """Return the most frequent model name from counts."""
    if not model_counts:
        return None
    return max(model_counts.items(), key=lambda item: item[1])[0]
