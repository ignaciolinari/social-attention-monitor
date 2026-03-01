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
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from sam.alerts import AlertManager
from sam.collectors.base import CollectedPost
from sam.collectors.bluesky import BlueskyCollector
from sam.collectors.reddit import RedditCollector
from sam.collectors.tmdb import TMDBCollector
from sam.collectors.youtube import YouTubeCollector
from sam.config import get_settings
from sam.logging import setup_logging
from sam.pipeline.enrichment import (
    analyze_texts_for_sentiment_with_stats,
    build_enriched_sentiment_map,
    merge_numeric_stats,
)
from sam.pipeline.metrics_snapshots import (
    compute_and_upsert_metrics_snapshots_multi,
)
from sam.pipeline.raw_storage import persist_collection_result
from sam.processors.spam_detector import detect_duplicate_content, filter_spam
from sam.quota import YOUTUBE_COMMENT_THREADS_COST, get_quota_tracker, seed_quota_from_db
from sam.storage.database import cleanup_stale_state, close_db, get_session, init_db
from sam.storage.repository import (
    acquire_lease,
    finish_pipeline_run,
    insert_mentions,
    release_lease,
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


def _snapshot_bucket(dt: datetime) -> datetime:
    """Round *up* to the next 30-minute boundary so that each collection cycle
    gets its own snapshot bucket.

    E.g. if now is 14:12, snapshot_time becomes 14:30.
    If now is 14:37, snapshot_time becomes 15:00.
    If the current time is already on a 30-minute boundary, keep it as-is.
    """
    if dt.second == 0 and dt.microsecond == 0 and dt.minute % 30 == 0:
        return dt
    if dt.minute < 30:
        return dt.replace(minute=30, second=0, microsecond=0)
    return dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


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
    # Allow callers to pass pre-built collectors so they can be reused.
    tmdb: TMDBCollector | None = None,
    reddit: RedditCollector | None = None,
    youtube: YouTubeCollector | None = None,
    bluesky: BlueskyCollector | None = None,
) -> dict[str, int | float]:
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
    translate_before_sentiment = settings.translate_before_sentiment
    enable_youtube_comments = settings.enable_youtube_comments
    enable_spam_filter = settings.enable_spam_filter
    comments_per_video = settings.youtube_comments_per_video

    from sam.cache import collector_toggle_get

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

    stats: dict[str, int | float] = {
        "titles": 0,
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
        "raw_storage_failures": 0,
    }
    per_title_ms: dict[str, float] = {}

    # Track YouTube video IDs already seen during *this* cycle to avoid
    # burning quota on the same video discovered via different title queries.
    seen_yt_video_ids: set[str] = set()

    # Circuit breaker: skip a platform for remaining titles after
    # MAX_PLATFORM_FAILURES consecutive failures (C1).
    _MAX_PLATFORM_FAILURES = 2
    platform_failures: dict[str, int] = {"reddit": 0, "youtube": 0, "bluesky": 0}

    try:
        titles = await tmdb.get_trending(media_type="all", time_window="week", limit=limit_titles)
        run_log.info(f"[runner] Trending titles: {len(titles)}")
        stats["titles"] = len(titles)

        # Read runtime toggle states once per cycle (not per title) to
        # avoid redundant Redis reads.
        reddit_runtime = await collector_toggle_get("reddit")
        yt_runtime = await collector_toggle_get("youtube")
        bsky_runtime = await collector_toggle_get("bluesky")

        reddit_enabled = reddit_runtime if reddit_runtime is not None else settings.reddit.enabled
        yt_enabled = yt_runtime if yt_runtime is not None else settings.youtube.enabled
        bsky_enabled = bsky_runtime if bsky_runtime is not None else settings.bluesky.enabled

        # Compute snapshot_time once per cycle so all titles in this run
        # share the same snapshot bucket.  Computing per-title risks
        # splitting titles across buckets when a cycle spans a 30-min
        # boundary, making cross-title comparison harder.
        snapshot_time = _snapshot_bucket(datetime.now(UTC))

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
                try:
                    # A1: Each title gets its own DB session/transaction.
                    async with get_session() as session:
                        db_title = await upsert_title(session, t)

                        # -- Phase 1: Collect from all platforms -----------------
                        # We gather posts first, then run sentiment in a single
                        # batch across platforms (B3 optimisation).
                        all_posts: list[CollectedPost] = []
                        # (platform, stats_key, posts, collected_at)
                        platform_batches: list[tuple[str, str, list[CollectedPost], datetime]] = []

                        # -- Reddit --
                        reddit_eligible = (
                            (settings.reddit.has_credentials or settings.demo_mode)
                            and reddit_enabled
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
                                # A4: Deduplicate posts by source_id (crossposts).
                                seen_ids: set[str] = set()
                                deduped: list[CollectedPost] = []
                                for p in reddit_result.posts:
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
                                        reddit_result.collected_at,
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
                                        query=t.title,
                                        limit=limit_youtube,
                                        exclude_source_ids=seen_yt_video_ids,
                                    )
                                except Exception as exc:
                                    platform_failures["youtube"] += 1
                                    logger.warning(
                                        f"[runner] YouTube collect failed for '{t.title}': {exc}"
                                    )
                                    yt_result = None

                        if yt_result and yt_result.success and yt_result.posts:
                            seen_yt_video_ids.update(p.source_id for p in yt_result.posts)

                            if settings.storage.enable_raw_data_storage:
                                ok = await persist_collection_result(
                                    yt_result,
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
                                    "youtube",
                                    "youtube_mentions_inserted",
                                    yt_result.posts,
                                    yt_result.collected_at,
                                )
                            )
                            all_posts.extend(yt_result.posts)
                            platform_failures["youtube"] = 0

                            # B2: Collect YouTube comments in parallel.
                            if enable_youtube_comments:
                                (
                                    all_comments,
                                    spam_total,
                                    comment_failures,
                                ) = await _collect_youtube_comments_parallel(
                                    youtube=youtube,
                                    posts=yt_result.posts,
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
                                            yt_result.collected_at,
                                        )
                                    )
                                    all_posts.extend(all_comments)
                        elif yt_result and not yt_result.success:
                            platform_failures["youtube"] += 1

                        # -- Bluesky --
                        bsky_eligible = (
                            (settings.bluesky.has_credentials or settings.demo_mode)
                            and bsky_enabled
                            and platform_failures["bluesky"] < _MAX_PLATFORM_FAILURES
                        )
                        if bsky_eligible:
                            try:
                                bluesky_result = await bluesky.collect(
                                    query=t.title, limit=limit_bluesky
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
                                # Deduplicate posts by source_id (reposts).
                                bsky_seen: set[str] = set()
                                bsky_deduped: list[CollectedPost] = []
                                for p in bluesky_result.posts:
                                    if p.source_id not in bsky_seen:
                                        bsky_seen.add(p.source_id)
                                        bsky_deduped.append(p)

                                if settings.storage.enable_raw_data_storage:
                                    ok = await persist_collection_result(
                                        bluesky_result,
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
                                        "bluesky",
                                        "bluesky_mentions_inserted",
                                        bsky_deduped,
                                        bluesky_result.collected_at,
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
                        if all_posts:
                            with Timer("sentiment_analysis_seconds"):
                                sentiments, analysis_stats = await asyncio.to_thread(
                                    analyze_texts_for_sentiment_with_stats,
                                    [p.content for p in all_posts],
                                    translate=translate_before_sentiment,
                                    log_context="runner",
                                )
                            merge_numeric_stats(stats, analysis_stats)
                            full_sentiment_map = await build_enriched_sentiment_map(
                                all_posts,
                                sentiments,
                                settings,
                            )

                        # -- Phase 3: Persist mentions per platform batch --------
                        for plat, stats_key, posts, collected_at in platform_batches:
                            inserted = await insert_mentions(
                                session,
                                title_id=db_title.id,
                                platform=plat,
                                posts=posts,
                                sentiment_by_source_id=full_sentiment_map,
                                collected_at=collected_at,
                            )
                            stats[stats_key] += inserted
                            counter_inc("mentions_inserted_total", inserted, {"platform": plat})
                            if inserted:
                                logger.info(f"[runner] {t.title} {stats_key}: {inserted}")

                        # -- Phase 4: Metrics snapshots (B4 shared query) --------
                        snapshots_upserted = await compute_and_upsert_metrics_snapshots_multi(
                            session,
                            title_id=db_title.id,
                            snapshot_time=snapshot_time,
                            window_hours_list=[1, 24],
                        )
                        stats["metrics_snapshots_upserted"] += snapshots_upserted

                except Exception as exc:
                    logger.exception(f"[runner] Failed to process title '{t.title}': {exc}")
                    await _record_title_failure(t.title)
                    # Continue with the next title instead of aborting the entire run.
                    continue
                else:
                    # Title processed successfully — reset failure counter.
                    await _clear_title_failures(t.title)
                finally:
                    # D1: Per-title timing for observability.
                    title_elapsed = perf_counter() - title_start
                    per_title_ms[t.title] = round(title_elapsed * 1000, 1)
                    logger.info(f"[runner] {t.title}: completed in {title_elapsed:.1f}s")

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
    final_stats: dict[str, object] = {**stats, "per_title_ms": per_title_ms}
    return final_stats  # type: ignore[return-value]


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
    tmdb: TMDBCollector | None = None,
    reddit: RedditCollector | None = None,
    youtube: YouTubeCollector | None = None,
    bluesky: BlueskyCollector | None = None,
) -> None:
    lease_ttl = max(60, interval_minutes * 60 * 2)
    started = datetime.now(UTC)

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
            job_name=JOB_NAME,
            owner_id=owner_id,
            started_at=started,
            stats={
                "interval_minutes": interval_minutes,
                "limit_titles": limit_titles,
                "limit_reddit": limit_reddit,
                "limit_youtube": limit_youtube,
                "limit_bluesky": limit_bluesky,
            },
        )
        run_id = run.id
        run_stats = run.stats or {}
    # Session 1 commits here — lease and PipelineRun(status="running") are
    # now persisted and visible to other processes.

    # --- Run collection outside any long-held session ---
    try:
        quota_start = quota_tracker.get_usage("youtube")
        stats = await collect_once(
            limit_titles=limit_titles,
            limit_reddit=limit_reddit,
            limit_youtube=limit_youtube,
            limit_bluesky=limit_bluesky,
            run_id=run_id,
            tmdb=tmdb,
            reddit=reddit,
            youtube=youtube,
            bluesky=bluesky,
        )

        # --- Session 2: finalize (alerts, finish run, release lease) ---
        async with get_session() as session:
            alert_stats = {"alerts_detected": 0, "alerts_created": 0}
            try:
                manager = AlertManager()
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
            await finish_pipeline_run(
                session,
                run_id=run_id,
                status="success",
                stats={
                    **run_stats,
                    **stats,
                    **alert_stats,
                    "elapsed_seconds": int(elapsed),
                    "api_quota": quota_stats,
                },
            )
            await release_lease(session, name=LEASE_NAME, owner_id=owner_id)
    except Exception as e:
        logger.exception(f"[runner] collection cycle failed: {e}")
        async with get_session() as session:
            await finish_pipeline_run(session, run_id=run_id, status="failed", error=str(e))
            await release_lease(session, name=LEASE_NAME, owner_id=owner_id)


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


def main() -> None:
    setup_logging()
    from sam.config import install_sighup_handler

    install_sighup_handler()
    settings = get_settings()

    parser = argparse.ArgumentParser(prog="sam-collector", description="SAM collector runner")
    parser.add_argument("--once", action="store_true", help="Run a single collection cycle")
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
