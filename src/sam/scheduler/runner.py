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

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from sam.alerts import AlertManager
from sam.collectors.base import CollectedPost
from sam.collectors.bluesky import BlueskyCollector
from sam.collectors.reddit import RedditCollector
from sam.collectors.tmdb import TMDBCollector
from sam.collectors.youtube import YouTubeCollector
from sam.config import get_settings
from sam.logging import setup_logging
from sam.pipeline.metrics_snapshots import compute_and_upsert_metrics_snapshot
from sam.pipeline.raw_storage import persist_collection_result
from sam.processors.sentiment import SentimentResult, analyze_sentiment_batch
from sam.quota import get_quota_tracker, seed_quota_from_db
from sam.storage.database import cleanup_stale_state, close_db, get_session, init_db
from sam.storage.repository import (
    acquire_lease,
    finish_pipeline_run,
    insert_mentions,
    release_lease,
    start_pipeline_run,
    upsert_title,
)

LEASE_NAME = "sam:collector-cycle"
JOB_NAME = "collector-cycle"


def _snapshot_hour(dt: datetime) -> datetime:
    """Round *up* to the next hour boundary so that mentions collected during
    the current hour always fall inside the (snapshot_time - window, snapshot_time] range.

    E.g. if now is 14:37, snapshot_time becomes 15:00.  The 1-hour window then
    covers 14:00-15:00, which includes the mentions we just ingested at ~14:37.
    If the current time is already exactly on the hour, keep it as-is.
    """
    if dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
        return dt
    return dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def _build_sentiment_map(
    posts: list[CollectedPost],
    sentiments: list[SentimentResult],
) -> dict[str, dict[str, object]]:
    """Build a source_id -> sentiment dict from parallel lists."""
    return {
        post.source_id: {
            "compound": s.compound,
            "positive": s.positive,
            "negative": s.negative,
            "neutral": s.neutral,
            "label": s.label,
            "model": s.model,
        }
        for post, s in zip(posts, sentiments, strict=True)
    }


async def collect_once(
    session: AsyncSession,
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
) -> dict[str, int]:
    settings = get_settings()
    logger.info(f"[runner] Starting one-shot collection (demo_mode={settings.demo_mode})")

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

    stats: dict[str, int] = {
        "titles": 0,
        "reddit_mentions_inserted": 0,
        "youtube_mentions_inserted": 0,
        "bluesky_mentions_inserted": 0,
        "youtube_skipped_quota": 0,
        "metrics_snapshots_upserted": 0,
    }

    # Track YouTube video IDs already seen during *this* cycle to avoid
    # burning quota on the same video discovered via different title queries.
    seen_yt_video_ids: set[str] = set()

    try:
        titles = await tmdb.get_trending(media_type="all", time_window="week", limit=limit_titles)
        logger.info(f"[runner] Trending titles: {len(titles)}")
        stats["titles"] = len(titles)

        snapshot_time = _snapshot_hour(datetime.now(UTC))
        for t in titles:
            try:
                db_title = await upsert_title(session, t)

                # -- Reddit --
                # Only attempt collection if the platform is actually configured.
                if reddit.is_configured or settings.demo_mode:
                    reddit_result = await reddit.collect(query=t.title, limit=limit_reddit)
                    if reddit_result.success and reddit_result.posts:
                        if settings.storage.enable_raw_data_storage:
                            await persist_collection_result(
                                reddit_result,
                                raw_data_dir=settings.storage.raw_data_dir,
                                title=t.title,
                                title_id=db_title.id,
                                query=t.title,
                                run_id=run_id,
                            )
                        # Run CPU-bound VADER sentiment in a thread to avoid
                        # blocking the async event loop.
                        sentiments = await asyncio.to_thread(
                            analyze_sentiment_batch,
                            [p.content for p in reddit_result.posts],
                        )
                        inserted = await insert_mentions(
                            session,
                            title_id=db_title.id,
                            platform="reddit",
                            posts=reddit_result.posts,
                            sentiment_by_source_id=_build_sentiment_map(
                                reddit_result.posts, sentiments
                            ),
                        )
                        stats["reddit_mentions_inserted"] += inserted
                        logger.info(f"[runner] {t.title} reddit mentions inserted: {inserted}")

                # -- YouTube --
                # Only attempt collection if the platform is actually configured.
                if youtube.is_configured or settings.demo_mode:
                    # Guard: skip YouTube for this title if we'd exceed the daily budget.
                    estimated_cost = 100 + 1  # search.list (100) + videos.list (1)
                    if not quota.youtube_has_budget(cost=estimated_cost) and not settings.demo_mode:
                        stats["youtube_skipped_quota"] += 1
                        logger.warning(
                            f"[runner] Skipping YouTube for '{t.title}' — daily quota budget exhausted"
                        )
                        yt_result = None
                    else:
                        yt_query = t.title
                        yt_result = await youtube.collect(
                            query=yt_query,
                            limit=limit_youtube,
                            exclude_source_ids=seen_yt_video_ids,
                        )
                else:
                    yt_result = None

                if yt_result:
                    yt_query = t.title
                    if yt_result.success and yt_result.posts:
                        # Track IDs so subsequent titles skip already-seen videos.
                        seen_yt_video_ids.update(p.source_id for p in yt_result.posts)

                    if yt_result.success and yt_result.posts:
                        if settings.storage.enable_raw_data_storage:
                            await persist_collection_result(
                                yt_result,
                                raw_data_dir=settings.storage.raw_data_dir,
                                title=t.title,
                                title_id=db_title.id,
                                query=yt_query,
                                run_id=run_id,
                            )
                        sentiments = await asyncio.to_thread(
                            analyze_sentiment_batch,
                            [p.content for p in yt_result.posts],
                        )
                        inserted = await insert_mentions(
                            session,
                            title_id=db_title.id,
                            platform="youtube",
                            posts=yt_result.posts,
                            sentiment_by_source_id=_build_sentiment_map(
                                yt_result.posts, sentiments
                            ),
                        )
                        stats["youtube_mentions_inserted"] += inserted
                        logger.info(f"[runner] {t.title} youtube mentions inserted: {inserted}")

                # -- Bluesky --
                # Only attempt collection if the platform is actually configured.
                if bluesky.is_configured or settings.demo_mode:
                    bluesky_result = await bluesky.collect(query=t.title, limit=limit_bluesky)
                    if bluesky_result.success and bluesky_result.posts:
                        if settings.storage.enable_raw_data_storage:
                            await persist_collection_result(
                                bluesky_result,
                                raw_data_dir=settings.storage.raw_data_dir,
                                title=t.title,
                                title_id=db_title.id,
                                query=t.title,
                                run_id=run_id,
                            )
                        # Run sentiment analysis
                        sentiments = await asyncio.to_thread(
                            analyze_sentiment_batch,
                            [p.content for p in bluesky_result.posts],
                        )
                        inserted = await insert_mentions(
                            session,
                            title_id=db_title.id,
                            platform="bluesky",
                            posts=bluesky_result.posts,
                            sentiment_by_source_id=_build_sentiment_map(
                                bluesky_result.posts, sentiments
                            ),
                        )
                        stats["bluesky_mentions_inserted"] += inserted
                        logger.info(f"[runner] {t.title} bluesky mentions inserted: {inserted}")

                # Persist metrics snapshots (hourly buckets).
                for window_hours in (1, 24):
                    await compute_and_upsert_metrics_snapshot(
                        session,
                        title_id=db_title.id,
                        snapshot_time=snapshot_time,
                        window_hours=window_hours,
                    )
                    stats["metrics_snapshots_upserted"] += 1

            except Exception as exc:
                logger.exception(f"[runner] Failed to process title '{t.title}': {exc}")
                # Continue with the next title instead of aborting the entire run.
                continue

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
    logger.info(
        f"[runner] YouTube API quota: {yt_quota.get('total_units', 0)} units used today "
        f"({yt_quota.get('total_calls', 0)} calls)"
    )
    if stats["youtube_skipped_quota"]:
        logger.warning(
            f"[runner] Skipped YouTube for {stats['youtube_skipped_quota']} title(s) "
            "due to quota budget"
        )
    return stats


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

        try:
            quota_start = quota_tracker.get_usage("youtube")
            stats = await collect_once(
                session,
                limit_titles=limit_titles,
                limit_reddit=limit_reddit,
                limit_youtube=limit_youtube,
                limit_bluesky=limit_bluesky,
                run_id=run.id,
                tmdb=tmdb,
                reddit=reddit,
                youtube=youtube,
                bluesky=bluesky,
            )
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
                run_id=run.id,
                status="success",
                stats={
                    **(run.stats or {}),
                    **stats,
                    **alert_stats,
                    "elapsed_seconds": int(elapsed),
                    "api_quota": quota_stats,
                },
            )
        except Exception as e:
            logger.exception(f"[runner] collection cycle failed: {e}")
            await finish_pipeline_run(session, run_id=run.id, status="failed", error=str(e))
        finally:
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
        misfire_grace_time=60,
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
            await close_db()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
