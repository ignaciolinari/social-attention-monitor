"""Collector runner.

Implements the missing module referenced by `make run-collector`.
This runs a simple polling loop:
- fetch trending titles from TMDB
- collect mentions from Reddit and YouTube
- compute sentiment
- persist titles + mentions to Postgres

This runner uses APScheduler + a DB-backed lease to prevent overlapping runs
across processes, and persists a run record for observability.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import uuid
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any, cast

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from sam.alerts import AlertManager
from sam.cache import publish_alert_event, publish_metrics_event, publish_system_health_throttled
from sam.collectors.base import CollectedPost, post_identity_key
from sam.collectors.bluesky import BlueskyCollector
from sam.collectors.reddit import RedditCollector
from sam.collectors.tmdb import TMDBCollector, TMDBTitle
from sam.collectors.youtube import YouTubeCollector
from sam.config import get_settings
from sam.logging import setup_logging
from sam.pipeline.enrichment import (
    analyze_texts_for_sentiment_with_stats_and_languages as analyze_texts_for_sentiment_with_stats,
)
from sam.pipeline.enrichment import (
    build_enriched_sentiment_map,
    detect_languages,
    merge_numeric_stats,
    translate_before_sentiment_enabled,
)
from sam.pipeline.metrics_snapshots import (
    compute_and_upsert_metrics_snapshots_multi,
)
from sam.pipeline.raw_storage import delete_raw_data_older_than, persist_collection_result
from sam.pipeline.time import DEFAULT_SNAPSHOT_BUCKET_MINUTES, floor_time_bucket
from sam.processors.matching import build_search_query, match_title_text
from sam.processors.spam_detector import detect_duplicate_content, filter_spam
from sam.quota import YOUTUBE_COMMENT_THREADS_COST, get_quota_tracker, seed_quota_from_db
from sam.storage.database import cleanup_stale_state, close_db, get_session, init_db
from sam.storage.repository import (
    acquire_lease,
    clear_stale_one_shot_state,
    deactivate_titles_not_seen_since,
    delete_mentions_older_than,
    delete_pipeline_runs_older_than,
    finish_pipeline_run,
    get_all_watchlist_tmdb_ids,
    get_title_by_id,
    insert_mentions,
    release_lease,
    renew_lease,
    start_pipeline_run,
    upsert_title,
)

# Optional metrics — imported lazily so the runner works without the API package.
try:
    from sam.api.metrics import Timer, counter_inc, histogram_observe
except ImportError:  # pragma: no cover

    def counter_inc(
        name: str,
        value: float = 1.0,
        labels: dict[str, str] | None = None,
    ) -> None:
        pass

    def histogram_observe(
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        pass

    class Timer:  # type: ignore[no-redef]
        def __init__(self, *a: object, **kw: object) -> None:
            pass

        def __enter__(self) -> Timer:
            return self  # type: ignore[return-value]

        def __exit__(self, *exc: object) -> None:
            pass


LEASE_NAME = "sam:collector-cycle"

# Titles that fail _TITLE_FAILURE_THRESHOLD consecutive times (across cycles)
# are temporarily excluded from processing for _TITLE_QUARANTINE_SECONDS.
_TITLE_FAILURE_THRESHOLD = 3
_TITLE_QUARANTINE_SECONDS = 3600  # 1 hour


async def _title_failure_key(title: str) -> str:
    """Redis key that tracks consecutive failure count for a title."""
    return f"sam:title-fail:{title}"


async def _record_title_failure(title: str) -> int:
    """Increment failure count and return the new value.  Returns 0 on Redis error."""
    from sam.cache import get_redis

    r = get_redis()
    if r is None:
        return 0
    try:
        key = await _title_failure_key(title)
        count: int = await r.incr(key)
        # Auto-expire so keys don't live forever.
        await r.expire(key, _TITLE_QUARANTINE_SECONDS * 2)
        return count
    except Exception:
        return 0


async def _clear_title_failures(title: str) -> None:
    """Reset the title failure counter on success."""
    from sam.cache import get_redis

    r = get_redis()
    if r is None:
        return
    with contextlib.suppress(Exception):
        await r.delete(await _title_failure_key(title))


async def _is_title_quarantined(title: str) -> bool:
    """Return True if the title has exceeded the failure threshold."""
    from sam.cache import get_redis

    r = get_redis()
    if r is None:
        return False
    try:
        key = await _title_failure_key(title)
        raw = await r.get(key)
        if raw is None:
            return False
        return int(raw) >= _TITLE_FAILURE_THRESHOLD
    except Exception:
        return False


# Re-export removed — `_bool_flag` / `bool_flag` no longer needed;
# pydantic Settings fields are properly typed bools.


JOB_NAME = "collector-cycle"
REFRESH_JOB_NAME = "collector-refresh"


def _tmdb_title_from_db_row(row: Any) -> TMDBTitle:
    """Rebuild a minimal ``TMDBTitle`` from a persisted DB row."""
    return TMDBTitle(
        tmdb_id=int(row.tmdb_id),
        title=str(row.title),
        original_title=str(row.original_title or row.title),
        media_type=str(row.media_type),
        release_date=row.release_date,
        overview=str(row.overview or ""),
        poster_path=row.poster_path,
        backdrop_path=(row.extra_data or {}).get("backdrop_path")
        if isinstance(row.extra_data, dict)
        else None,
        popularity=float(row.popularity or 0.0),
        vote_average=float(row.vote_average or 0.0),
        vote_count=int((row.extra_data or {}).get("vote_count", 0) or 0)
        if isinstance(row.extra_data, dict)
        else 0,
        genres=list(row.genres or []),
        original_language=str((row.extra_data or {}).get("original_language", "en"))
        if isinstance(row.extra_data, dict)
        else "en",
        revenue=row.revenue,
        budget=row.budget,
        raw_data=dict(row.extra_data or {}),
    )


def _snapshot_bucket(dt: datetime) -> datetime:
    """Floor to the shared snapshot bucket boundary.

    Buckets are aligned in UTC and shared with CLI backfills so live collection
    and recompute jobs produce the same snapshot timestamps.
    """
    return floor_time_bucket(dt, bucket_minutes=DEFAULT_SNAPSHOT_BUCKET_MINUTES)


def _derive_run_status(stats: dict[str, Any]) -> str:
    """Derive overall run status from per-title outcomes."""
    titles_failed = int(stats.get("titles_failed", 0) or 0)
    titles_succeeded = int(stats.get("titles_succeeded", 0) or 0)
    if titles_failed > 0 and titles_succeeded == 0:
        return "failed"
    if titles_failed > 0:
        return "degraded"
    return "success"


def _normalized_collected_at(value: Any) -> datetime:
    """Normalize collector timestamps to tz-aware UTC datetimes."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    return datetime.now(UTC)


def _filter_title_matches(
    posts: list[CollectedPost], *, title: TMDBTitle
) -> tuple[list[CollectedPost], int]:
    """Drop posts that do not confidently match the expected title."""
    kept: list[CollectedPost] = []
    filtered = 0
    for post in posts:
        if (
            match_title_text(
                post.content,
                title=title.title,
                original_title=title.original_title,
            )
            is None
        ):
            filtered += 1
            continue
        kept.append(post)
    return kept, filtered


def _coerce_int_setting(value: Any, default: int) -> int:
    """Return an integer setting value even when tests pass loose mocks."""
    return value if isinstance(value, int) else default


async def _run_retention_cleanup(*, session: Any, settings: Any) -> dict[str, int]:
    """Delete old pipeline artifacts according to retention settings."""
    deleted_mentions = 0
    deleted_runs = 0
    deleted_raw_files = 0
    mention_days = _coerce_int_setting(getattr(settings, "retention_mentions_days", 0), 0)
    run_days = _coerce_int_setting(getattr(settings, "retention_pipeline_runs_days", 0), 0)
    raw_days = _coerce_int_setting(getattr(settings, "retention_raw_data_days", 0), 0)

    if mention_days > 0:
        deleted_mentions = await delete_mentions_older_than(
            session,
            older_than=datetime.now(UTC) - timedelta(days=mention_days),
        )
    if run_days > 0:
        deleted_runs = await delete_pipeline_runs_older_than(
            session,
            older_than=datetime.now(UTC) - timedelta(days=run_days),
        )
    if settings.storage.enable_raw_data_storage and raw_days > 0:
        deleted_raw_files = await delete_raw_data_older_than(
            settings.storage.raw_data_dir,
            older_than=datetime.now(UTC) - timedelta(days=raw_days),
        )

    return {
        "retention_deleted_mentions": deleted_mentions,
        "retention_deleted_runs": deleted_runs,
        "retention_deleted_raw_files": deleted_raw_files,
    }


def _system_health_payload(anomaly: Any) -> dict[str, Any]:
    """Serialize a system health anomaly for websocket broadcast."""
    return {
        "id": f"system-{anomaly.alert_type.value}",
        "title_id": anomaly.title_id,
        "alert_type": anomaly.alert_type.value,
        "severity": anomaly.severity.value,
        "message": anomaly.message,
        "details": anomaly.details,
        "created_at": anomaly.detected_at.isoformat(),
        "acknowledged_at": None,
        "is_system": True,
    }


async def _lease_heartbeat(
    *,
    owner_id: uuid.UUID,
    ttl_seconds: int,
    stop_event: asyncio.Event,
    lost_lease_event: asyncio.Event,
) -> None:
    """Periodically renew the lease while a collection run is active."""
    interval_seconds = max(10, ttl_seconds // 3)
    while True:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            return
        except TimeoutError:
            pass

        try:
            async with get_session() as session:
                renewed = await renew_lease(
                    session,
                    name=LEASE_NAME,
                    owner_id=owner_id,
                    ttl_seconds=ttl_seconds,
                )
            if not renewed:
                logger.error("[runner] Lease renewal failed; lease may have been stolen")
                lost_lease_event.set()
                return
        except Exception as exc:
            logger.warning(f"[runner] Lease heartbeat error: {exc}")
            lost_lease_event.set()
            return


# _build_sentiment_map, _enrich_sentiments_with_nlp, _build_enriched_sentiment_map
# are now in sam.pipeline.enrichment as build_sentiment_map, enrich_sentiments_with_nlp,
# build_enriched_sentiment_map.


async def collect_once(
    *,
    limit_titles: int,
    limit_reddit: int,
    limit_youtube: int,
    limit_bluesky: int,
    run_id: uuid.UUID | None = None,
    titles_override: list[TMDBTitle] | None = None,
    platform_filter: set[str] | None = None,
    allow_title_parallelism: bool = True,
    record_cycle_metrics: bool = True,
    # Allow callers to pass pre-built collectors so they can be reused.
    tmdb: TMDBCollector | None = None,
    reddit: RedditCollector | None = None,
    youtube: YouTubeCollector | None = None,
    bluesky: BlueskyCollector | None = None,
) -> dict[str, Any]:
    """Run one collection cycle across all trending titles.

    Each title is processed inside its own DB session/transaction so that
    a failure (or even a commit error) for one title does not roll back work
    already committed for previous titles (A1 fix).
    """
    settings = get_settings()
    # Create a logger with run_id context for cycle-level messages
    # (the per-title loop uses contextualize() which adds title= on top).
    run_log = logger.bind(run_id=str(run_id) if run_id else None)
    run_log.info(f"[runner] Starting one-shot collection (demo_mode={settings.demo_mode})")
    translate_before_sentiment = translate_before_sentiment_enabled(settings)
    enable_youtube_comments = settings.enable_youtube_comments
    enable_spam_filter = settings.enable_spam_filter
    comments_per_video = settings.youtube_comments_per_video

    from sam.cache import collector_toggle_get

    allowed_platforms = {p.strip().lower() for p in (platform_filter or set()) if p.strip()}

    if not settings.demo_mode and not settings.tmdb.is_configured:
        raise RuntimeError("TMDB not configured. Set TMDB_API_KEY or TMDB_ACCESS_TOKEN in .env")

    # Re-use passed collectors or create new ones.  Callers that want to reuse
    # HTTP connections across cycles can pass already-initialised instances.
    own_tmdb = tmdb is None
    own_reddit = reddit is None
    own_youtube = youtube is None
    own_bluesky = bluesky is None
    tmdb = tmdb or TMDBCollector()
    reddit = reddit or RedditCollector()
    youtube = youtube or YouTubeCollector()
    bluesky = bluesky or BlueskyCollector()

    quota = get_quota_tracker()

    stats: dict[str, Any] = {
        "titles": 0,
        "titles_succeeded": 0,
        "titles_failed": 0,
        "reddit_mentions_inserted": 0,
        "youtube_mentions_inserted": 0,
        "youtube_comments_inserted": 0,
        "bluesky_mentions_inserted": 0,
        "youtube_skipped_quota": 0,
        "metrics_snapshots_upserted": 0,
        "translate_attempted": 0,
        "translate_count": 0,
        "translate_failures": 0,
        "translate_skipped_english": 0,
        "sentiment_ms_total": 0.0,
        "spam_filtered": 0,
        "duplicates_removed": 0,
        "matching_filtered": 0,
        "raw_storage_failures": 0,
        "mentions_capped_titles": 0,
        "mentions_capped_title_names": [],
        "languages_detection_failures": 0,
        "watchlist_titles_not_found": 0,
        "watchlist_titles_failed": 0,
        "titles_deactivated": 0,
        "retention_deleted_mentions": 0,
        "retention_deleted_runs": 0,
        "retention_deleted_raw_files": 0,
    }
    per_title_ms: dict[str, float] = {}

    # Circuit breaker: skip a platform for remaining titles after
    # MAX_PLATFORM_FAILURES consecutive failures (C1).
    _MAX_PLATFORM_FAILURES = 2
    platform_failures: dict[str, int] = {"reddit": 0, "youtube": 0, "bluesky": 0}
    used_parallel_path = False

    try:
        if titles_override is not None:
            titles = list(titles_override)
            run_log.info(f"[runner] Using explicit title override ({len(titles)} title(s))")
            stats["titles"] = len(titles)
        else:
            titles = await tmdb.get_trending(
                media_type="all", time_window="week", limit=limit_titles
            )
            run_log.info(f"[runner] Trending titles: {len(titles)}")
            stats["titles"] = len(titles)

            # Enrich movies with revenue/budget from TMDB detail API.
            if not settings.demo_mode and titles:
                try:
                    await tmdb.enrich_titles_with_details(titles)
                except Exception as exc:
                    run_log.warning(f"[runner] TMDB enrichment failed: {exc}")

            # Phase 0b: Fetch watchlist titles not already in the trending set.
            trending_tmdb_ids = {t.tmdb_id for t in titles}
            try:
                async with get_session() as wl_session:
                    watchlist_ids = await get_all_watchlist_tmdb_ids(wl_session)
                extra_ids = watchlist_ids - trending_tmdb_ids
                if extra_ids:
                    run_log.info(
                        f"[runner] Fetching {len(extra_ids)} watchlist-only titles from TMDB"
                    )

                    async def _fetch_watchlist_title(tmdb_id: int) -> TMDBTitle | None:
                        """Try movie first, then tv."""
                        for attempt, mtype in enumerate(("movie", "tv")):
                            result = await tmdb.get_details(
                                tmdb_id,
                                media_type=mtype,
                                suppress_not_found_error=attempt == 0,
                            )
                            if result is not None:
                                return result
                        return None

                    detail_results = await asyncio.gather(
                        *[_fetch_watchlist_title(tid) for tid in extra_ids],
                        return_exceptions=True,
                    )
                    watchlist_titles = [r for r in detail_results if isinstance(r, TMDBTitle)]
                    failed_count = sum(1 for r in detail_results if isinstance(r, Exception))
                    not_found_count = sum(1 for r in detail_results if r is None)
                    stats["watchlist_titles_failed"] = (
                        int(stats["watchlist_titles_failed"]) + failed_count
                    )
                    stats["watchlist_titles_not_found"] = (
                        int(stats["watchlist_titles_not_found"]) + not_found_count
                    )
                    if watchlist_titles:
                        run_log.info(
                            f"[runner] Added {len(watchlist_titles)} watchlist titles "
                            f"({not_found_count} not found, {failed_count} failed)"
                        )
                        titles.extend(watchlist_titles)
                        stats["titles"] = len(titles)
            except Exception as exc:
                run_log.warning(f"[runner] Watchlist title fetch failed: {exc}")

            title_retirement_days = _coerce_int_setting(
                getattr(settings, "title_retirement_days", 0),
                0,
            )
            if title_retirement_days > 0:
                try:
                    stale_before = datetime.now(UTC) - timedelta(days=title_retirement_days)
                    tracked_tmdb_ids = {title.tmdb_id for title in titles}
                    async with get_session() as lifecycle_session:
                        stats["titles_deactivated"] = await deactivate_titles_not_seen_since(
                            lifecycle_session,
                            keep_tmdb_ids=tracked_tmdb_ids,
                            stale_before=stale_before,
                        )
                except Exception as exc:
                    run_log.warning(f"[runner] Title retirement sweep failed: {exc}")

        # Read runtime toggle states once per cycle (not per title) to
        # avoid redundant Redis reads.
        reddit_runtime = await collector_toggle_get("reddit")
        yt_runtime = await collector_toggle_get("youtube")
        bsky_runtime = await collector_toggle_get("bluesky")

        reddit_enabled = reddit_runtime if reddit_runtime is not None else settings.reddit.enabled
        yt_enabled = yt_runtime if yt_runtime is not None else settings.youtube.enabled
        bsky_enabled = bsky_runtime if bsky_runtime is not None else settings.bluesky.enabled

        title_parallelism = _coerce_int_setting(
            getattr(settings, "collector_title_concurrency", 1),
            1,
        )
        if allow_title_parallelism and title_parallelism > 1 and len(titles) > 1:
            sem = asyncio.Semaphore(title_parallelism)

            async def _run_parallel_title(title: TMDBTitle) -> dict[str, Any]:
                async with sem:
                    return await collect_once(
                        limit_titles=1,
                        limit_reddit=limit_reddit,
                        limit_youtube=limit_youtube,
                        limit_bluesky=limit_bluesky,
                        run_id=run_id,
                        titles_override=[title],
                        platform_filter=platform_filter,
                        allow_title_parallelism=False,
                        record_cycle_metrics=False,
                    )

            child_results = await asyncio.gather(*[_run_parallel_title(title) for title in titles])
            capped_titles: list[str] = []
            for child_stats in child_results:
                child_per_title = child_stats.get("per_title_ms", {})
                if isinstance(child_per_title, dict):
                    per_title_ms.update(
                        {
                            str(title): float(ms)
                            for title, ms in child_per_title.items()
                            if isinstance(ms, (int, float))
                        }
                    )
                child_capped_titles = child_stats.get("mentions_capped_title_names", [])
                if isinstance(child_capped_titles, list):
                    capped_titles.extend(str(title) for title in child_capped_titles)
                for key, value in child_stats.items():
                    if key in {"titles", "per_title_ms", "mentions_capped_title_names"}:
                        continue
                    if isinstance(value, (int, float)) and isinstance(stats.get(key), (int, float)):
                        stats[key] = float(stats.get(key, 0)) + float(value)
            stats["mentions_capped_title_names"] = capped_titles
            stats["titles"] = len(titles)
            used_parallel_path = True

        if not used_parallel_path:
            for t in titles:
                title_start = perf_counter()
                # Dead-letter: skip titles that have failed repeatedly.
                if await _is_title_quarantined(t.title):
                    logger.warning(
                        f"[runner] Skipping quarantined title '{t.title}' "
                        f"(>{_TITLE_FAILURE_THRESHOLD} consecutive failures)"
                    )
                    continue
                # Bind log context for this title (structured log correlation).
                with logger.contextualize(run_id=str(run_id) if run_id else None, title=t.title):
                    session_cm: Any | None = None
                    session_exited = False
                    try:
                        # A1: Each title gets its own DB session/transaction.
                        session_cm = get_session()
                        session = await session_cm.__aenter__()
                        db_title = await upsert_title(session, t)

                        # -- Phase 1: Collect from all platforms -----------------
                        # We gather posts first, then run sentiment in a single
                        # batch across platforms (B3 optimisation).
                        all_posts: list[CollectedPost] = []
                        youtube_query = build_search_query(
                            t.title,
                            original_title=t.original_title,
                            media_type=t.media_type,
                            release_date=t.release_date,
                        )
                        # (platform, stats_key, posts, collected_at)
                        platform_batches: list[tuple[str, str, list[CollectedPost], datetime]] = []

                        # -- Reddit --
                        reddit_eligible = (
                            (settings.reddit.has_credentials or settings.demo_mode)
                            and reddit_enabled
                            and (not allowed_platforms or "reddit" in allowed_platforms)
                            and platform_failures["reddit"] < _MAX_PLATFORM_FAILURES
                        )
                        if reddit_eligible:
                            try:
                                reddit_result = await reddit.collect(
                                    query=t.title, limit=limit_reddit
                                )
                            except Exception as exc:
                                platform_failures["reddit"] += 1
                                logger.warning(
                                    f"[runner] Reddit collect failed for '{t.title}': {exc}"
                                )
                                reddit_result = None
                            if reddit_result is None:
                                pass
                            elif reddit_result.success and reddit_result.posts:
                                matched_reddit, filtered_reddit = (
                                    (reddit_result.posts, 0)
                                    if settings.demo_mode
                                    else _filter_title_matches(
                                        reddit_result.posts,
                                        title=t,
                                    )
                                )
                                if filtered_reddit:
                                    stats["matching_filtered"] = (
                                        int(stats.get("matching_filtered", 0)) + filtered_reddit
                                    )
                                # A4: Deduplicate posts by source_id (crossposts).
                                seen_ids: set[str] = set()
                                deduped: list[CollectedPost] = []
                                for p in matched_reddit:
                                    if p.source_id not in seen_ids:
                                        seen_ids.add(p.source_id)
                                        deduped.append(p)

                                if settings.storage.enable_raw_data_storage:
                                    ok = await persist_collection_result(
                                        reddit_result,
                                        raw_data_dir=settings.storage.raw_data_dir,
                                        title=t.title,
                                        title_id=db_title.id,
                                        query=t.title,
                                        run_id=run_id,
                                    )
                                    if not ok:
                                        stats["raw_storage_failures"] += 1

                                platform_batches.append(
                                    (
                                        "reddit",
                                        "reddit_mentions_inserted",
                                        deduped,
                                        _normalized_collected_at(reddit_result.collected_at),
                                    )
                                )
                                all_posts.extend(deduped)
                                platform_failures["reddit"] = 0
                            elif not reddit_result.success:
                                platform_failures["reddit"] += 1

                        # -- YouTube --
                        yt_eligible = (
                            (settings.youtube.has_credentials or settings.demo_mode)
                            and yt_enabled
                            and (not allowed_platforms or "youtube" in allowed_platforms)
                            and platform_failures["youtube"] < _MAX_PLATFORM_FAILURES
                        )
                        yt_result = None
                        if yt_eligible:
                            estimated_cost = 100 + 1  # search.list + videos.list
                            if (
                                not quota.youtube_has_budget(cost=estimated_cost)
                                and not settings.demo_mode
                            ):
                                stats["youtube_skipped_quota"] += 1
                                logger.warning(
                                    f"[runner] Skipping YouTube for '{t.title}' "
                                    "— daily quota budget exhausted"
                                )
                            else:
                                try:
                                    yt_result = await youtube.collect(
                                        query=youtube_query or t.title,
                                        limit=limit_youtube,
                                    )
                                except Exception as exc:
                                    platform_failures["youtube"] += 1
                                    logger.warning(
                                        f"[runner] YouTube collect failed for '{t.title}': {exc}"
                                    )
                                    yt_result = None

                        if yt_result and yt_result.success and yt_result.posts:
                            matched_youtube, filtered_youtube = (
                                (yt_result.posts, 0)
                                if settings.demo_mode
                                else _filter_title_matches(
                                    yt_result.posts,
                                    title=t,
                                )
                            )
                            if filtered_youtube:
                                stats["matching_filtered"] = (
                                    int(stats.get("matching_filtered", 0)) + filtered_youtube
                                )

                            if settings.storage.enable_raw_data_storage:
                                ok = await persist_collection_result(
                                    yt_result,
                                    raw_data_dir=settings.storage.raw_data_dir,
                                    title=t.title,
                                    title_id=db_title.id,
                                    query=youtube_query or t.title,
                                    run_id=run_id,
                                )
                                if not ok:
                                    stats["raw_storage_failures"] += 1

                            platform_batches.append(
                                (
                                    "youtube",
                                    "youtube_mentions_inserted",
                                    matched_youtube,
                                    _normalized_collected_at(yt_result.collected_at),
                                )
                            )
                            all_posts.extend(matched_youtube)
                            platform_failures["youtube"] = 0

                            # B2: Collect YouTube comments in parallel.
                            if enable_youtube_comments and matched_youtube:
                                (
                                    all_comments,
                                    spam_total,
                                    comment_failures,
                                ) = await _collect_youtube_comments_parallel(
                                    youtube=youtube,
                                    posts=matched_youtube,
                                    limit=comments_per_video,
                                    enable_spam_filter=enable_spam_filter,
                                    quota=quota,
                                    demo_mode=settings.demo_mode,
                                )
                                stats["spam_filtered"] += spam_total
                                # Feed comment-endpoint failures into the circuit
                                # breaker so persistent YouTube issues (e.g. global
                                # 403) are detected even when video search succeeds.
                                if comment_failures and not all_comments:
                                    platform_failures["youtube"] += 1
                                if all_comments:
                                    platform_batches.append(
                                        (
                                            "youtube",
                                            "youtube_comments_inserted",
                                            all_comments,
                                            _normalized_collected_at(yt_result.collected_at),
                                        )
                                    )
                                    all_posts.extend(all_comments)
                        elif yt_result and not yt_result.success:
                            platform_failures["youtube"] += 1

                        # -- Bluesky --
                        bsky_eligible = (
                            (settings.bluesky.has_credentials or settings.demo_mode)
                            and bsky_enabled
                            and (not allowed_platforms or "bluesky" in allowed_platforms)
                            and platform_failures["bluesky"] < _MAX_PLATFORM_FAILURES
                        )
                        if bsky_eligible:
                            try:
                                bluesky_result = await bluesky.collect(
                                    query=youtube_query or t.title,
                                    limit=limit_bluesky,
                                )
                            except Exception as exc:
                                platform_failures["bluesky"] += 1
                                logger.warning(
                                    f"[runner] Bluesky collect failed for '{t.title}': {exc}"
                                )
                                bluesky_result = None

                            if bluesky_result is None:
                                pass
                            elif bluesky_result.success and bluesky_result.posts:
                                matched_bluesky, filtered_bluesky = (
                                    (bluesky_result.posts, 0)
                                    if settings.demo_mode
                                    else _filter_title_matches(
                                        bluesky_result.posts,
                                        title=t,
                                    )
                                )
                                if filtered_bluesky:
                                    stats["matching_filtered"] = (
                                        int(stats.get("matching_filtered", 0)) + filtered_bluesky
                                    )
                                # Deduplicate posts by source_id (reposts).
                                bsky_seen: set[str] = set()
                                bsky_deduped: list[CollectedPost] = []
                                for p in matched_bluesky:
                                    if p.source_id not in bsky_seen:
                                        bsky_seen.add(p.source_id)
                                        bsky_deduped.append(p)

                                if settings.storage.enable_raw_data_storage:
                                    ok = await persist_collection_result(
                                        bluesky_result,
                                        raw_data_dir=settings.storage.raw_data_dir,
                                        title=t.title,
                                        title_id=db_title.id,
                                        query=youtube_query or t.title,
                                        run_id=run_id,
                                    )
                                    if not ok:
                                        stats["raw_storage_failures"] += 1

                                platform_batches.append(
                                    (
                                        "bluesky",
                                        "bluesky_mentions_inserted",
                                        bsky_deduped,
                                        _normalized_collected_at(bluesky_result.collected_at),
                                    )
                                )
                                all_posts.extend(bsky_deduped)
                                platform_failures["bluesky"] = 0
                            elif not bluesky_result.success:
                                platform_failures["bluesky"] += 1

                        # -- Phase 1b: Content deduplication (cross-author) ----
                        if all_posts:
                            dup_indices = detect_duplicate_content(all_posts)
                            if dup_indices:
                                stats["duplicates_removed"] += len(dup_indices)
                                all_posts = [
                                    p for i, p in enumerate(all_posts) if i not in dup_indices
                                ]
                                # Also remove from platform batches
                                kept = {id(p) for p in all_posts}
                                for batch_idx, (plat, skey, batch_posts, cat) in enumerate(
                                    platform_batches
                                ):
                                    platform_batches[batch_idx] = (
                                        plat,
                                        skey,
                                        [p for p in batch_posts if id(p) in kept],
                                        cat,
                                    )

                        # -- Phase 1c: Unified spam filtering (all platforms) ----
                        if enable_spam_filter and all_posts:
                            all_posts, spam_count = filter_spam(all_posts)
                            stats["spam_filtered"] += spam_count
                            if spam_count:
                                # Rebuild platform batches to exclude filtered posts
                                kept = {id(p) for p in all_posts}
                                for batch_idx, (plat, skey, batch_posts, cat) in enumerate(
                                    platform_batches
                                ):
                                    platform_batches[batch_idx] = (
                                        plat,
                                        skey,
                                        [p for p in batch_posts if id(p) in kept],
                                        cat,
                                    )

                        # -- Phase 2: Batch sentiment analysis (B3) -------------
                        full_sentiment_map: dict[str, dict[str, Any]] = {}
                        language_map: dict[str, str | None] = {}
                        if all_posts:
                            with Timer("sentiment_analysis_seconds"):
                                sentiment_result = await asyncio.to_thread(
                                    analyze_texts_for_sentiment_with_stats,
                                    [p.content for p in all_posts],
                                    translate=translate_before_sentiment,
                                    log_context="runner",
                                )
                            if len(sentiment_result) == 3:
                                sentiments, analysis_stats, detected_languages = sentiment_result
                            else:
                                sentiments, analysis_stats = sentiment_result
                                try:
                                    detected_languages = await asyncio.to_thread(
                                        detect_languages,
                                        [p.content for p in all_posts],
                                    )
                                except Exception as exc:
                                    detected_languages = [None for _ in all_posts]
                                    stats["languages_detection_failures"] = (
                                        int(stats["languages_detection_failures"]) + 1
                                    )
                                    logger.warning(f"[runner] Language detection failed: {exc}")
                            merge_numeric_stats(stats, analysis_stats)
                            full_sentiment_map = await build_enriched_sentiment_map(
                                all_posts,
                                sentiments,
                                settings,
                            )
                            for post, lang in zip(all_posts, detected_languages, strict=True):
                                key = post_identity_key(
                                    post.platform,
                                    post.source_type,
                                    post.source_id,
                                )
                                language_map[key] = lang
                            langs_found = sum(1 for v in detected_languages if v is not None)
                            stats["languages_detected"] = (
                                int(stats.get("languages_detected", 0)) + langs_found
                            )

                        # -- Phase 3: Persist mentions per platform batch --------
                        title_inserted_total = 0
                        for plat, stats_key, posts, collected_at in platform_batches:
                            inserted = await insert_mentions(
                                session,
                                title_id=db_title.id,
                                platform=plat,
                                posts=posts,
                                sentiment_by_source_id=full_sentiment_map,
                                language_by_source_id=language_map,
                                collected_at=collected_at,
                            )
                            stats[stats_key] += inserted
                            title_inserted_total += inserted
                            counter_inc("mentions_inserted_total", inserted, {"platform": plat})
                            if inserted:
                                logger.info(f"[runner] {t.title} {stats_key}: {inserted}")

                        # -- Phase 4: Metrics snapshots (B4 shared query) --------
                        latest_collected_at = max(
                            (batch[3] for batch in platform_batches),
                            default=datetime.now(UTC),
                        )
                        title_snapshot_time = _snapshot_bucket(latest_collected_at)
                        (
                            snapshots_upserted,
                            mentions_capped,
                        ) = await compute_and_upsert_metrics_snapshots_multi(
                            session,
                            title_id=db_title.id,
                            snapshot_time=title_snapshot_time,
                            window_hours_list=[1, 24],
                        )
                        stats["metrics_snapshots_upserted"] += snapshots_upserted
                        if mentions_capped:
                            stats["mentions_capped_titles"] = (
                                int(stats.get("mentions_capped_titles", 0)) + 1
                            )
                            capped_titles = stats.setdefault("mentions_capped_title_names", [])
                            if isinstance(capped_titles, list):
                                capped_titles.append(t.title)
                        with contextlib.suppress(Exception):
                            await publish_metrics_event(
                                {
                                    "title_id": str(db_title.id),
                                    "title": t.title,
                                    "snapshot_time": title_snapshot_time.isoformat(),
                                    "window_hours": [1, 24],
                                    "mentions_inserted": title_inserted_total,
                                    "snapshots_upserted": snapshots_upserted,
                                    "mentions_capped": bool(mentions_capped),
                                }
                            )
                        if session_cm is not None:
                            await session_cm.__aexit__(None, None, None)
                            session_exited = True

                    except Exception as exc:
                        if session_cm is not None and not session_exited:
                            with contextlib.suppress(Exception):
                                await session_cm.__aexit__(type(exc), exc, exc.__traceback__)
                        logger.exception(f"[runner] Failed to process title '{t.title}': {exc}")
                        await _record_title_failure(t.title)
                        stats["titles_failed"] += 1
                        # Continue with the next title instead of aborting the entire run.
                        continue
                    else:
                        # Title processed successfully — reset failure counter.
                        await _clear_title_failures(t.title)
                        stats["titles_succeeded"] += 1
                    finally:
                        # D1: Per-title timing for observability.
                        title_elapsed = perf_counter() - title_start
                        per_title_ms[t.title] = round(title_elapsed * 1000, 1)
                        logger.info(f"[runner] {t.title}: completed in {title_elapsed:.1f}s")

        if not used_parallel_path:
            # C1: Log circuit breaker activations.
            for plat, failures in platform_failures.items():
                if failures >= _MAX_PLATFORM_FAILURES:
                    run_log.warning(
                        f"[runner] Circuit breaker tripped for {plat} ({failures} consecutive failures)"
                    )

    finally:
        # Only close collectors we created ourselves.
        closeable: list[TMDBCollector | RedditCollector | YouTubeCollector | BlueskyCollector] = []
        if own_reddit:
            closeable.append(reddit)
        if own_youtube:
            closeable.append(youtube)
        if own_bluesky:
            closeable.append(bluesky)
        if own_tmdb:
            closeable.append(tmdb)
        for collector in closeable:
            with contextlib.suppress(Exception):
                await collector.close()

    yt_quota = quota.get_usage("youtube")
    if record_cycle_metrics:
        run_log.info(
            f"[runner] YouTube API quota: {yt_quota.get('total_units', 0)} units used today "
            f"({yt_quota.get('total_calls', 0)} calls)"
        )
        if stats["youtube_skipped_quota"]:
            run_log.warning(
                f"[runner] Skipped YouTube for {stats['youtube_skipped_quota']} title(s) "
                "due to quota budget"
            )
        counter_inc("collection_cycles_total")
        raw_units = yt_quota.get("total_units", 0) or 0
        units_val = float(raw_units) if isinstance(raw_units, (int, float)) else 0.0
        counter_inc("quota_units_used", units_val, {"platform": "youtube"})
    # Merge per_title_ms into final stats for persistence.
    final_stats: dict[str, Any] = {**stats, "per_title_ms": per_title_ms}
    return final_stats


async def _collect_youtube_comments_parallel(
    *,
    youtube: YouTubeCollector,
    posts: list[CollectedPost],
    limit: int,
    enable_spam_filter: bool,
    quota: Any,
    demo_mode: bool,
    max_concurrent: int = 5,
) -> tuple[list[CollectedPost], int, int]:
    """Fetch comments for multiple videos concurrently (B2 optimisation).

    Returns ``(all_comments, total_spam_filtered, comment_failures)``.
    """
    sem = asyncio.Semaphore(max_concurrent)
    total_spam = 0

    async def _fetch_one(video_id: str) -> tuple[list[CollectedPost], bool]:
        async with sem:
            result = await youtube.collect_comments(video_id, limit=limit)
            return (result.comments, result.had_error)

    # Pre-filter by quota budget.
    video_ids: list[str] = []
    for p in posts:
        # Reserve budget virtually as we build the batch so we don't schedule
        # N parallel calls that each individually pass the same pre-check.
        projected_cost = YOUTUBE_COMMENT_THREADS_COST * (len(video_ids) + 1)
        if not quota.youtube_has_budget(cost=projected_cost) and not demo_mode:
            logger.debug(f"[runner] Skipping comments for {p.source_id} — quota")
            break
        video_ids.append(p.source_id)

    if not video_ids:
        return [], 0, 0

    results = await asyncio.gather(
        *[_fetch_one(vid) for vid in video_ids],
        return_exceptions=True,
    )

    all_comments: list[CollectedPost] = []
    comment_failures = 0
    for result in results:
        if isinstance(result, BaseException):
            logger.warning(f"[runner] Comment fetch failed: {result}")
            comment_failures += 1
            continue
        comments, had_error = result
        if had_error:
            comment_failures += 1
        if comments:
            all_comments.extend(comments)

    # Run spam filter on the combined comment set.
    if enable_spam_filter and all_comments:
        all_comments, total_spam = filter_spam(all_comments)

    return all_comments, total_spam, comment_failures


async def _collection_job(
    *,
    owner_id: uuid.UUID,
    interval_minutes: int,
    limit_titles: int,
    limit_reddit: int,
    limit_youtube: int,
    limit_bluesky: int,
    job_name: str = JOB_NAME,
    titles_override: list[TMDBTitle] | None = None,
    platform_filter: set[str] | None = None,
    tmdb: TMDBCollector | None = None,
    reddit: RedditCollector | None = None,
    youtube: YouTubeCollector | None = None,
    bluesky: BlueskyCollector | None = None,
) -> None:
    lease_ttl = max(60, interval_minutes * 60 * 2)
    started = datetime.now(UTC)
    run_id: uuid.UUID | None = None
    run_stats: dict[str, object] = {}

    quota_tracker = get_quota_tracker()

    # --- Session 1: acquire lease + create PipelineRun ---
    # Commit immediately so the lease and "running" status are visible to
    # other processes without waiting for the full collection cycle.
    async with get_session() as session:
        acquired = await acquire_lease(
            session,
            name=LEASE_NAME,
            owner_id=owner_id,
            ttl_seconds=lease_ttl,
        )

        if not acquired:
            logger.info("[runner] Skipping cycle (lease held by another instance)")
            return

        run = await start_pipeline_run(
            session,
            job_name=job_name,
            owner_id=owner_id,
            started_at=started,
            stats={
                "interval_minutes": interval_minutes,
                "limit_titles": limit_titles,
                "limit_reddit": limit_reddit,
                "limit_youtube": limit_youtube,
                "limit_bluesky": limit_bluesky,
                "platform_filter": sorted(platform_filter) if platform_filter else [],
                "titles_override_count": len(titles_override or []),
            },
        )
        run_id = run.id
        run_stats = run.stats or {}
    # Session 1 commits here — lease and PipelineRun(status="running") are
    # now persisted and visible to other processes.
    if run_id is None:
        logger.warning("[runner] Pipeline run was not initialized; skipping cycle")
        return

    # --- Run collection outside any long-held session ---
    heartbeat_stop = asyncio.Event()
    lease_lost = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _lease_heartbeat(
            owner_id=owner_id,
            ttl_seconds=lease_ttl,
            stop_event=heartbeat_stop,
            lost_lease_event=lease_lost,
        )
    )
    try:
        quota_start = quota_tracker.get_usage("youtube")
        stats = await collect_once(
            limit_titles=limit_titles,
            limit_reddit=limit_reddit,
            limit_youtube=limit_youtube,
            limit_bluesky=limit_bluesky,
            run_id=run_id,
            titles_override=titles_override,
            platform_filter=platform_filter,
            tmdb=tmdb,
            reddit=reddit,
            youtube=youtube,
            bluesky=bluesky,
        )
        if lease_lost.is_set():
            raise RuntimeError("collector lease lost during cycle; aborting finalize")
        run_status = _derive_run_status(stats)
        run_error = "all titles failed during collection cycle" if run_status == "failed" else None

        # --- Session 2: finalize (alerts, finish run, system health, release lease) ---
        created_alerts: list[dict[str, Any]] = []
        system_health_issues: list[dict[str, Any]] = []
        async with get_session() as session:
            alert_stats = {"alerts_detected": 0, "alerts_created": 0}
            manager = AlertManager(freshness_hours=get_settings().fresh_snapshot_window_hours)
            try:
                detected, created_alerts = await manager.run_detection_cycle(
                    session, window_hours=1, history_points=24
                )
                alert_stats["alerts_detected"] = detected
                alert_stats["alerts_created"] = len(created_alerts)
                if created_alerts:
                    logger.info(f"[alerts] Created {len(created_alerts)} alerts")
            except Exception as e:
                logger.warning(f"[alerts] Detection cycle failed: {e}")

            elapsed = (datetime.now(UTC) - started).total_seconds()

            # Persist per-run quota deltas so daily totals can be summed across runs.
            quota_end = quota_tracker.get_usage("youtube")
            _start_units = quota_start.get("total_units", 0)
            _end_units = quota_end.get("total_units", 0)
            _start_calls = quota_start.get("total_calls", 0)
            _end_calls = quota_end.get("total_calls", 0)
            start_units = int(_start_units) if isinstance(_start_units, (int, str)) else 0
            end_units = int(_end_units) if isinstance(_end_units, (int, str)) else 0
            start_calls = int(_start_calls) if isinstance(_start_calls, (int, str)) else 0
            end_calls = int(_end_calls) if isinstance(_end_calls, (int, str)) else 0

            start_eps = quota_start.get("calls_by_endpoint", {})
            end_eps = quota_end.get("calls_by_endpoint", {})
            if not isinstance(start_eps, dict):
                start_eps = {}
            if not isinstance(end_eps, dict):
                end_eps = {}

            quota_stats = {
                "mode": "delta",
                "youtube": {
                    "date": quota_end.get("date"),
                    "total_units": max(0, end_units - start_units),
                    "total_calls": max(0, end_calls - start_calls),
                    "calls_by_endpoint": {
                        ep: max(0, int(cnt) - int(start_eps.get(ep, 0)))
                        for ep, cnt in end_eps.items()
                    },
                },
            }
            quota_youtube = cast(dict[str, Any], quota_stats["youtube"])
            retention_stats: dict[str, int] = {}
            if job_name == JOB_NAME:
                try:
                    retention_stats = await _run_retention_cleanup(
                        session=session,
                        settings=get_settings(),
                    )
                except Exception as cleanup_exc:
                    logger.warning(f"[runner] Retention cleanup failed: {cleanup_exc}")
            total_mentions_inserted = (
                int(stats.get("reddit_mentions_inserted", 0) or 0)
                + int(stats.get("youtube_mentions_inserted", 0) or 0)
                + int(stats.get("youtube_comments_inserted", 0) or 0)
                + int(stats.get("bluesky_mentions_inserted", 0) or 0)
            )
            await finish_pipeline_run(
                session,
                run_id=run_id,
                status=run_status,
                error=run_error,
                stats={
                    **run_stats,
                    **stats,
                    **retention_stats,
                    **alert_stats,
                    "system_health_issues": 0,
                    "elapsed_seconds": int(elapsed),
                    "mentions_inserted": total_mentions_inserted,
                    "api_quota": quota_stats,
                    "youtube_quota_units": int(quota_youtube.get("total_units", 0) or 0),
                    "youtube_quota_calls": int(quota_youtube.get("total_calls", 0) or 0),
                },
            )

            # System-level health checks depend on the latest collector run status,
            # so evaluate them only after the current run has been finalized.
            try:
                sys_anomalies = await manager.check_system_health(session)
                system_health_issues = [_system_health_payload(a) for a in sys_anomalies]
                if system_health_issues:
                    logger.info(f"[alerts] System health: {len(system_health_issues)} issue(s)")
                    await finish_pipeline_run(
                        session,
                        run_id=run_id,
                        status=run_status,
                        error=run_error,
                        stats={"system_health_issues": len(system_health_issues)},
                    )
            except Exception as sh_exc:
                logger.warning(f"[alerts] System health check failed: {sh_exc}")

            await release_lease(session, name=LEASE_NAME, owner_id=owner_id)
        for alert in created_alerts:
            with contextlib.suppress(Exception):
                await publish_alert_event(alert)
        for issue in system_health_issues:
            with contextlib.suppress(Exception):
                await publish_system_health_throttled(issue)
    except Exception as e:
        logger.exception(f"[runner] collection cycle failed: {e}")
        if run_id is not None:
            system_health_issues = []
            async with get_session() as session:
                await finish_pipeline_run(session, run_id=run_id, status="failed", error=str(e))

                try:
                    manager = AlertManager(
                        freshness_hours=get_settings().fresh_snapshot_window_hours
                    )
                    sys_anomalies = await manager.check_system_health(session)
                    system_health_issues = [_system_health_payload(a) for a in sys_anomalies]
                    if system_health_issues:
                        await finish_pipeline_run(
                            session,
                            run_id=run_id,
                            status="failed",
                            error=str(e),
                            stats={"system_health_issues": len(system_health_issues)},
                        )
                except Exception as sh_exc:
                    logger.warning(f"[alerts] System health check failed: {sh_exc}")

                await release_lease(session, name=LEASE_NAME, owner_id=owner_id)
            for issue in system_health_issues:
                with contextlib.suppress(Exception):
                    await publish_system_health_throttled(issue)
    finally:
        heartbeat_stop.set()
        with contextlib.suppress(Exception):
            await heartbeat_task


async def run_forever(
    *,
    interval_minutes: int,
    limit_titles: int,
    limit_reddit: int,
    limit_youtube: int,
    limit_bluesky: int,
) -> None:
    logger.info(f"[runner] Scheduling collection every {interval_minutes} minutes")
    owner_id = uuid.uuid4()

    # Create collectors once so HTTP connections are reused across cycles.
    tmdb = TMDBCollector()
    reddit = RedditCollector()
    youtube = YouTubeCollector()
    bluesky = BlueskyCollector()

    job_kwargs: dict[str, object] = {
        "owner_id": owner_id,
        "interval_minutes": interval_minutes,
        "limit_titles": limit_titles,
        "limit_reddit": limit_reddit,
        "limit_youtube": limit_youtube,
        "limit_bluesky": limit_bluesky,
        "tmdb": tmdb,
        "reddit": reddit,
        "youtube": youtube,
        "bluesky": bluesky,
    }

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _collection_job,
        trigger=IntervalTrigger(minutes=interval_minutes),
        kwargs=job_kwargs,
        id=JOB_NAME,
        name=JOB_NAME,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=interval_minutes * 60,
    )
    scheduler.start()

    # Run one immediate cycle on startup for fast feedback.
    await _collection_job(
        owner_id=owner_id,
        interval_minutes=interval_minutes,
        limit_titles=limit_titles,
        limit_reddit=limit_reddit,
        limit_youtube=limit_youtube,
        limit_bluesky=limit_bluesky,
        tmdb=tmdb,
        reddit=reddit,
        youtube=youtube,
        bluesky=bluesky,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)
    try:
        await stop_event.wait()
    finally:
        scheduler.shutdown(wait=False)
        for collector in (reddit, youtube, bluesky, tmdb):
            with contextlib.suppress(Exception):
                await collector.close()


async def _force_clear_one_shot_state() -> tuple[int, int]:
    """Clear stale collector state for operator-invoked one-shot runs.

    Refuses to evict an active lease so a manual one-shot run cannot overlap a
    healthy long-running collector process.
    """
    async with get_session() as session:
        released, failed_runs = await clear_stale_one_shot_state(
            session,
            lease_name=LEASE_NAME,
            job_name=JOB_NAME,
            error="stale run cleared by one-shot recovery",
        )

    if released or failed_runs:
        logger.warning(
            f"[runner] Force-cleared state for one-shot run: "
            f"released {released} lease(s), failed {failed_runs} running run(s)"
        )
    return released, failed_runs


async def run_refresh_for_title(*, title_id: uuid.UUID, platform: str) -> None:
    """Run the scheduler ingestion path for one DB title/platform pair."""
    normalized_platform = platform.strip().lower()
    if normalized_platform not in {"reddit", "youtube", "bluesky"}:
        raise ValueError(f"Unsupported refresh platform: {platform}")

    async with get_session() as session:
        row = await get_title_by_id(session, title_id)
        if row is None:
            logger.warning(f"[runner] refresh requested for unknown title_id={title_id}")
            return
        title = _tmdb_title_from_db_row(row)

    settings = get_settings()
    await _collection_job(
        owner_id=uuid.uuid4(),
        interval_minutes=settings.collector.polling_interval_minutes,
        limit_titles=1,
        limit_reddit=settings.collector.max_posts_per_subreddit,
        limit_youtube=20,
        limit_bluesky=50,
        job_name=REFRESH_JOB_NAME,
        titles_override=[title],
        platform_filter={normalized_platform},
    )


def main() -> None:
    setup_logging()
    from sam.config import install_sighup_handler

    install_sighup_handler()
    settings = get_settings()

    parser = argparse.ArgumentParser(prog="sam-collector", description="SAM collector runner")
    parser.add_argument("--once", action="store_true", help="Run a single collection cycle")
    parser.add_argument(
        "--force-clear-lease",
        action="store_true",
        help="With --once, clear stale collector lease/run state before starting",
    )
    parser.add_argument("--init-db", action="store_true", help="Create tables if missing")
    parser.add_argument(
        "--interval-minutes", type=int, default=settings.collector.polling_interval_minutes
    )
    parser.add_argument("--limit-titles", type=int, default=10)
    parser.add_argument(
        "--limit-reddit", type=int, default=settings.collector.max_posts_per_subreddit
    )
    parser.add_argument("--limit-youtube", type=int, default=20)
    parser.add_argument("--limit-bluesky", type=int, default=50)
    args = parser.parse_args()

    if args.force_clear_lease and not args.once:
        parser.error("--force-clear-lease requires --once")

    async def _run() -> None:
        if args.init_db:
            await init_db()

        # Clean up stale leases / orphan runs left by killed processes.
        await cleanup_stale_state()

        # Seed the in-memory quota tracker from previous runs today so the
        # quota guard knows how many units have already been consumed.
        await seed_quota_from_db()

        try:
            if args.once:
                if args.force_clear_lease:
                    await _force_clear_one_shot_state()

                # Route one-shot runs through the same job path so we also:
                # - acquire/release the lease
                # - record a PipelineRun (started/finished/error)
                owner_id = uuid.uuid4()
                await _collection_job(
                    owner_id=owner_id,
                    interval_minutes=args.interval_minutes,
                    limit_titles=args.limit_titles,
                    limit_reddit=args.limit_reddit,
                    limit_youtube=args.limit_youtube,
                    limit_bluesky=args.limit_bluesky,
                )
            else:
                await run_forever(
                    interval_minutes=args.interval_minutes,
                    limit_titles=args.limit_titles,
                    limit_reddit=args.limit_reddit,
                    limit_youtube=args.limit_youtube,
                    limit_bluesky=args.limit_bluesky,
                )
        finally:
            from sam.cache import close_redis

            await close_redis()
            await close_db()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
