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
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from sam.alerts import AlertManager
from sam.collectors.reddit import RedditCollector
from sam.collectors.tmdb import TMDBCollector
from sam.collectors.youtube import YouTubeCollector
from sam.config import get_settings
from sam.logging import setup_logging
from sam.pipeline.metrics_snapshots import compute_and_upsert_metrics_snapshot
from sam.pipeline.raw_storage import persist_collection_result
from sam.processors.sentiment import analyze_sentiment_batch
from sam.storage.database import close_db, get_session, init_db
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
    return dt.replace(minute=0, second=0, microsecond=0)


async def collect_once(
    session: AsyncSession,
    *,
    limit_titles: int,
    limit_reddit: int,
    limit_youtube: int,
    run_id: uuid.UUID | None = None,
) -> dict[str, int]:
    settings = get_settings()
    logger.info(f"[runner] Starting one-shot collection (demo_mode={settings.demo_mode})")

    if not settings.demo_mode and not settings.tmdb.is_configured:
        # TMDB is the source of truth for which titles to track. Without it, the pipeline
        # will "succeed" with 0 titles and give confusing feedback.
        raise RuntimeError("TMDB not configured. Set TMDB_API_KEY or TMDB_ACCESS_TOKEN in .env")

    tmdb = TMDBCollector()
    reddit = RedditCollector()
    youtube = YouTubeCollector()

    stats: dict[str, int] = {
        "titles": 0,
        "reddit_mentions_inserted": 0,
        "youtube_mentions_inserted": 0,
        "metrics_snapshots_upserted": 0,
    }

    try:
        titles = await tmdb.get_trending(media_type="all", time_window="week", limit=limit_titles)
        logger.info(f"[runner] Trending titles: {len(titles)}")
        stats["titles"] = len(titles)

        snapshot_time = _snapshot_hour(datetime.now(UTC))
        for t in titles:
            db_title = await upsert_title(session, t)

            # Reddit
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
                sentiments = analyze_sentiment_batch([p.content for p in reddit_result.posts])
                sentiment_map = {
                    post.source_id: {
                        "compound": s.compound,
                        "positive": s.positive,
                        "negative": s.negative,
                        "neutral": s.neutral,
                        "label": s.label,
                        "model": s.model,
                    }
                    for post, s in zip(reddit_result.posts, sentiments, strict=False)
                }
                inserted = await insert_mentions(
                    session,
                    title_id=db_title.id,
                    platform="reddit",
                    posts=reddit_result.posts,
                    sentiment_by_source_id=sentiment_map,
                )
                stats["reddit_mentions_inserted"] += inserted
                logger.info(f"[runner] {t.title} reddit mentions inserted: {inserted}")

            # YouTube
            yt_query = f"{t.title} trailer"
            yt_result = await youtube.collect(query=yt_query, limit=limit_youtube)
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
                sentiments = analyze_sentiment_batch([p.content for p in yt_result.posts])
                sentiment_map = {
                    post.source_id: {
                        "compound": s.compound,
                        "positive": s.positive,
                        "negative": s.negative,
                        "neutral": s.neutral,
                        "label": s.label,
                        "model": s.model,
                    }
                    for post, s in zip(yt_result.posts, sentiments, strict=False)
                }
                inserted = await insert_mentions(
                    session,
                    title_id=db_title.id,
                    platform="youtube",
                    posts=yt_result.posts,
                    sentiment_by_source_id=sentiment_map,
                )
                stats["youtube_mentions_inserted"] += inserted
                logger.info(f"[runner] {t.title} youtube mentions inserted: {inserted}")

            # Persist metrics snapshots (hourly buckets).
            for window_hours in (1, 24):
                await compute_and_upsert_metrics_snapshot(
                    session,
                    title_id=db_title.id,
                    snapshot_time=snapshot_time,
                    window_hours=window_hours,
                )
                stats["metrics_snapshots_upserted"] += 1

    finally:
        for collector in (reddit, youtube, tmdb):
            with contextlib.suppress(Exception):
                await collector.close()

    return stats


async def _collection_job(
    *,
    owner_id: uuid.UUID,
    interval_minutes: int,
    limit_titles: int,
    limit_reddit: int,
    limit_youtube: int,
) -> None:
    lease_ttl = max(60, interval_minutes * 60 * 2)
    started = datetime.now(UTC)

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
            },
        )

        try:
            stats = await collect_once(
                session,
                limit_titles=limit_titles,
                limit_reddit=limit_reddit,
                limit_youtube=limit_youtube,
                run_id=run.id,
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
            await finish_pipeline_run(
                session,
                run_id=run.id,
                status="success",
                stats={
                    **(run.stats or {}),
                    **stats,
                    **alert_stats,
                    "elapsed_seconds": int(elapsed),
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
) -> None:
    logger.info(f"[runner] Scheduling collection every {interval_minutes} minutes")
    owner_id = uuid.uuid4()

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _collection_job,
        trigger=IntervalTrigger(minutes=interval_minutes),
        kwargs={
            "owner_id": owner_id,
            "interval_minutes": interval_minutes,
            "limit_titles": limit_titles,
            "limit_reddit": limit_reddit,
            "limit_youtube": limit_youtube,
        },
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
    args = parser.parse_args()

    async def _run() -> None:
        if args.init_db:
            await init_db()

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
                )
            else:
                await run_forever(
                    interval_minutes=args.interval_minutes,
                    limit_titles=args.limit_titles,
                    limit_reddit=args.limit_reddit,
                    limit_youtube=args.limit_youtube,
                )
        finally:
            await close_db()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
