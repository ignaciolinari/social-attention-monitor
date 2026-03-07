"""Shared state and helpers used by multiple route modules.

This module owns the collector singletons, toggle overrides, and the
reusable mention-collection / persistence logic that several endpoints
depend on.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from fastapi import HTTPException
from loguru import logger

from sam.alerts.manager import (
    AlertManager,
    acknowledge_alert,
    count_alerts,
    count_unacknowledged_alerts,
    get_alert_counts_by_severity,
    get_recent_alerts,
    get_unacknowledged_count,
)
from sam.api.schemas import DbTitleResponse, MentionResponse
from sam.cache import cache_get_json, cache_set_json, close_redis, get_redis
from sam.collectors.base import BaseCollector, CollectedPost, collected_post_key
from sam.collectors.bluesky import BlueskyCollector
from sam.collectors.reddit import RedditCollector
from sam.collectors.tmdb import TMDBCollector
from sam.collectors.youtube import YouTubeCollector
from sam.config import Settings, get_settings
from sam.pipeline.enrichment import (
    analyze_texts_for_sentiment,
    build_enriched_sentiment_map,
    translate_before_sentiment_enabled,
)
from sam.processors.sentiment import SentimentResult, analyze_sentiment
from sam.quota import aggregate_youtube_quota_from_db
from sam.storage.database import get_session
from sam.storage.models import Title as TitleModel
from sam.storage.repository import (
    escape_like,
    get_average_benchmark_trajectory,
    get_benchmark_contributors_count,
    get_latest_mention_collected_at,
    get_mentions_count,
    get_mentions_for_title,
    get_metrics_timeseries,
    get_pipeline_health_stats,
    get_pipeline_runs,
    get_title_by_id,
    get_title_by_name,
    get_trending_by_attention_index,
    insert_mentions,
    list_titles,
)

# Re-exports consumed by route modules via ``deps.XXX`` — keep in ``__all__``
# so linters don't strip them as "unused".
__all__ = [
    "AlertManager",
    "acknowledge_alert",
    "aggregate_youtube_quota_from_db",
    "analyze_sentiment",
    "cache_get_json",
    "cache_set_json",
    "count_alerts",
    "count_unacknowledged_alerts",
    "escape_like",
    "get_average_benchmark_trajectory",
    "get_benchmark_contributors_count",
    "get_alert_counts_by_severity",
    "get_metrics_timeseries",
    "get_pipeline_health_stats",
    "get_pipeline_runs",
    "get_recent_alerts",
    "get_redis",
    "get_session",
    "get_title_by_id",
    "get_trending_by_attention_index",
    "get_unacknowledged_count",
    "list_titles",
    "Settings",
    "TitleModel",
]

# ---------------------------------------------------------------------------
# Collector singletons — initialised by ``init_collectors()``.
# ---------------------------------------------------------------------------

reddit_collector: RedditCollector | None = None
youtube_collector: YouTubeCollector | None = None
bluesky_collector: BlueskyCollector | None = None
tmdb_collector: TMDBCollector | None = None


async def init_collectors() -> None:
    """Create collector instances (called from the app lifespan)."""
    global reddit_collector, youtube_collector, bluesky_collector, tmdb_collector  # noqa: PLW0603
    reddit_collector = RedditCollector()
    youtube_collector = YouTubeCollector()
    bluesky_collector = BlueskyCollector()
    tmdb_collector = TMDBCollector()


async def close_collectors() -> None:
    """Tear down collector instances (called from the app lifespan)."""
    for c in (reddit_collector, youtube_collector, bluesky_collector, tmdb_collector):
        if c:
            with contextlib.suppress(Exception):
                await c.close()
    await close_redis()


# ---------------------------------------------------------------------------
# Collector toggle state
# ---------------------------------------------------------------------------

# Runtime overrides for collector enabled state.
# Keys: "reddit", "youtube", "bluesky".  Values override the env-var defaults.
# In-memory cache is updated on toggle; Redis is used for cross-process sharing.
collector_enabled_overrides: dict[str, bool] = {}

# Short-lived refresh dedupe lock so repeated stale-page views do not spawn
# overlapping background refresh tasks for the same (title, platform) pair.
_REFRESH_LOCK_TTL_SECONDS = 120

TOGGLEABLE_PLATFORMS = {"youtube", "bluesky"}


def api_keys_configured(platform: str) -> bool:
    """Check if the API keys for *platform* are set (ignoring the enabled toggle)."""
    from sam.config import _is_effectively_set

    s = get_settings()
    if platform == "reddit":
        return _is_effectively_set(s.reddit.client_id) and _is_effectively_set(
            s.reddit.client_secret
        )
    if platform == "youtube":
        return _is_effectively_set(s.youtube.api_key)
    if platform == "bluesky":
        return _is_effectively_set(s.bluesky.identifier) and _is_effectively_set(
            s.bluesky.app_password
        )
    return False


async def is_collector_enabled(platform: str) -> bool:
    """Check if a collector is enabled (Redis > in-memory > env default)."""
    from sam.cache import collector_toggle_get

    redis_val = await collector_toggle_get(platform)
    if redis_val is not None:
        return redis_val

    override = collector_enabled_overrides.get(platform)
    if override is not None:
        return override

    s = get_settings()
    return getattr(getattr(s, platform, None), "enabled", False)


def set_collector_override(platform: str, enabled: bool) -> None:
    """Set a local fallback override for collector state."""
    collector_enabled_overrides[platform] = enabled


# ---------------------------------------------------------------------------
# Mention helpers
# ---------------------------------------------------------------------------

PLATFORM_DEFAULT_LIMITS: dict[str, int] = {
    "reddit": 50,
    "youtube": 20,
    "bluesky": 50,
}
VALID_PLATFORMS = frozenset(PLATFORM_DEFAULT_LIMITS)


@dataclass
class DbMentionsResult:
    mentions: list[MentionResponse]
    total_count: int
    next_offset: int | None
    title_id: UUID
    last_collected_at: datetime | None


def title_from_row(row: TitleModel) -> DbTitleResponse:
    return DbTitleResponse(
        id=str(row.id),
        tmdb_id=int(row.tmdb_id),
        title=str(row.title),
        media_type=str(row.media_type),
        release_date=row.release_date.isoformat() if row.release_date else None,
        popularity=getattr(row, "popularity", None),
        revenue=row.revenue,
        budget=row.budget,
    )


async def get_mentions_from_db(
    *,
    title: str,
    title_id: UUID | None,
    platform: str,
    limit: int,
    offset: int,
) -> DbMentionsResult | None:
    async with get_session() as session:
        title_row = (
            await get_title_by_id(session, title_id)
            if title_id is not None
            else await get_title_by_name(session, title)
        )
        if not title_row:
            return None

        last_collected_at = await get_latest_mention_collected_at(
            session, title_id=title_row.id, platform=platform
        )

        total_count = await get_mentions_count(session, title_id=title_row.id, platform=platform)
        if total_count == 0:
            return DbMentionsResult([], 0, None, title_row.id, last_collected_at)

        mentions = await get_mentions_for_title(
            session,
            title_id=title_row.id,
            platform=platform,
            limit=limit,
            offset=offset,
        )

        has_more = total_count > offset + limit

        results: list[MentionResponse] = []
        for m in mentions:
            content = m.content or ""
            is_truncated = len(content) > 500
            results.append(
                MentionResponse(
                    platform=m.platform,
                    source_id=m.source_id,
                    source_type=m.source_type,
                    content=(content[:500] + "...") if is_truncated else content,
                    content_truncated=is_truncated,
                    author=m.author,
                    url=m.url,
                    created_at=m.created_at.isoformat(),
                    metrics=m.metrics or {},
                    sentiment=m.sentiment,
                )
            )

        next_offset = offset + limit if has_more else None
        return DbMentionsResult(results, total_count, next_offset, title_row.id, last_collected_at)


async def persist_mentions(
    *,
    title_id: UUID,
    platform: str,
    posts: list[CollectedPost],
    sentiment_by_source_id: dict[str, dict[str, Any]],
    collected_at: datetime | None = None,
) -> None:
    async with get_session() as session:
        await insert_mentions(
            session,
            title_id=title_id,
            platform=platform,
            posts=posts,
            sentiment_by_source_id=sentiment_by_source_id,
            collected_at=collected_at,
        )


def sentiment_payload(sr: SentimentResult) -> dict[str, Any]:
    return sr.to_dict()


async def collect_youtube_comment_posts(
    yt_collector: YouTubeCollector,
    video_posts: list[CollectedPost],
    *,
    limit: int,
    settings: Settings,
) -> list[CollectedPost]:
    """Collect bounded YouTube comments for a set of fetched video posts."""
    if not settings.enable_youtube_comments:
        return []
    if not video_posts:
        return []

    comments_per_video_raw = getattr(settings, "youtube_comments_per_video", 30)
    comments_per_video = comments_per_video_raw if isinstance(comments_per_video_raw, int) else 30
    if comments_per_video < 1:
        return []

    max_comment_candidates = max(limit * 2, 1)
    comment_posts: list[CollectedPost] = []
    for video_post in video_posts:
        remaining_budget = max_comment_candidates - len(comment_posts)
        if remaining_budget <= 0:
            break

        per_video_limit = min(comments_per_video, remaining_budget)
        try:
            result = await yt_collector.collect_comments(
                video_post.source_id,
                limit=per_video_limit,
            )
            comments = result.comments
        except Exception as exc:
            logger.debug(
                f"[api] YouTube comment collection skipped ({video_post.source_id}): {exc}"
            )
            continue
        comment_posts.extend(comments)
    return comment_posts


async def collect_mentions_live(
    *,
    platform: str,
    title: str,
    limit: int,
) -> tuple[list[MentionResponse], dict[str, dict[str, Any]], list[Any], datetime]:
    if platform in TOGGLEABLE_PLATFORMS and not await is_collector_enabled(platform):
        raise HTTPException(status_code=503, detail=f"{platform} collector is currently disabled")

    collector: BaseCollector | None = None
    if platform == "reddit":
        collector = reddit_collector
    elif platform == "youtube":
        collector = youtube_collector
    elif platform == "bluesky":
        collector = bluesky_collector
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}")

    if not collector:
        raise HTTPException(status_code=503, detail=f"{platform} collector not initialized")

    result = await collector.collect(query=title, limit=limit)
    if not result.success:
        raise HTTPException(status_code=500, detail=result.error)

    settings = get_settings()
    posts: list[CollectedPost] = result.posts or []
    if platform == "youtube":
        yt_collector = cast(YouTubeCollector, collector)
        comment_posts = await collect_youtube_comment_posts(
            yt_collector,
            posts,
            limit=limit,
            settings=settings,
        )
        posts.extend(comment_posts)

    # Keep response/persist set bounded by requested limit and favor fresh content.
    posts = sorted(posts, key=lambda p: p.created_at, reverse=True)[:limit]

    if not posts:
        return [], {}, [], result.collected_at

    _translate = translate_before_sentiment_enabled(settings)
    sentiment_results = await asyncio.to_thread(
        analyze_texts_for_sentiment,
        [post.content for post in posts],
        translate=_translate,
        log_context="api",
    )

    sentiment_by_source_id = await build_enriched_sentiment_map(posts, sentiment_results, settings)

    mentions: list[MentionResponse] = []
    for post in posts:
        sentiment_for_mention = sentiment_by_source_id.get(collected_post_key(post))
        if sentiment_for_mention is None:
            # Backward compatibility with legacy source_id-only keys.
            sentiment_for_mention = sentiment_by_source_id.get(post.source_id)
        is_truncated = len(post.content) > 500
        mentions.append(
            MentionResponse(
                platform=post.platform,
                source_id=post.source_id,
                source_type=getattr(post, "source_type", "post") or "post",
                content=post.content[:500] + "..." if is_truncated else post.content,
                content_truncated=is_truncated,
                author=post.author,
                url=post.url,
                created_at=post.created_at.isoformat(),
                metrics=post.metrics,
                sentiment=sentiment_for_mention,
            )
        )

    return mentions, sentiment_by_source_id, posts, result.collected_at


async def refresh_mentions_background(
    *,
    title: str,
    title_id: UUID,
    platform: str,
    limit: int,
) -> None:
    del title, limit
    if not await _acquire_refresh_lock(title_id=title_id, platform=platform):
        logger.debug(
            f"[api] {platform} background refresh already in-flight for title_id={title_id}"
        )
        return

    try:
        from sam.scheduler.runner import run_refresh_for_title

        await run_refresh_for_title(title_id=title_id, platform=platform)
    except HTTPException as exc:
        logger.warning(f"[api] {platform} background refresh skipped: {exc.detail}")
    except Exception as exc:
        logger.exception(f"[api] {platform} background refresh failed: {exc}")


async def _acquire_refresh_lock(*, title_id: UUID, platform: str) -> bool:
    """Acquire best-effort refresh lock; fail-open when Redis is unavailable."""
    r = get_redis()
    if r is None:
        return True
    key = f"sam:mentions-refresh:{title_id}:{platform}"
    try:
        acquired = await r.set(key, "1", ex=_REFRESH_LOCK_TTL_SECONDS, nx=True)
        return bool(acquired)
    except Exception:
        return True
