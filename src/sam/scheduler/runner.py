"""Collector runner.

Implements the missing module referenced by `make run-collector`.
This runs a simple polling loop:
- fetch trending titles from TMDB
- collect mentions from Reddit and YouTube
- compute sentiment
- persist titles + mentions to Postgres

This is intentionally a minimal "Phase 2" bridge to get an end-to-end pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from loguru import logger

from sam.collectors.reddit import RedditCollector
from sam.collectors.tmdb import TMDBCollector
from sam.collectors.youtube import YouTubeCollector
from sam.config import get_settings
from sam.logging import setup_logging
from sam.processors.sentiment import analyze_sentiment_batch
from sam.storage.database import close_db, get_session, init_db
from sam.storage.repository import insert_mentions, upsert_title


async def collect_once(*, limit_titles: int, limit_reddit: int, limit_youtube: int) -> None:
    settings = get_settings()
    logger.info(f"[runner] Starting one-shot collection (demo_mode={settings.demo_mode})")

    tmdb = TMDBCollector()
    reddit = RedditCollector()
    youtube = YouTubeCollector()

    try:
        titles = await tmdb.get_trending(media_type="all", time_window="week", limit=limit_titles)
        logger.info(f"[runner] Trending titles: {len(titles)}")

        async with get_session() as session:
            for t in titles:
                db_title = await upsert_title(session, t)

                # Reddit
                reddit_result = await reddit.collect(query=t.title, limit=limit_reddit)
                if reddit_result.success and reddit_result.posts:
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
                    logger.info(f"[runner] {t.title} reddit mentions inserted: {inserted}")

                # YouTube
                yt_query = f"{t.title} trailer"
                yt_result = await youtube.collect(query=yt_query, limit=limit_youtube)
                if yt_result.success and yt_result.posts:
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
                    logger.info(f"[runner] {t.title} youtube mentions inserted: {inserted}")

    finally:
        await youtube.close()
        await tmdb.close()


async def run_forever(
    *,
    interval_minutes: int,
    limit_titles: int,
    limit_reddit: int,
    limit_youtube: int,
) -> None:
    logger.info(f"[runner] Polling every {interval_minutes} minutes")
    while True:
        started = datetime.now(UTC)
        try:
            await collect_once(
                limit_titles=limit_titles,
                limit_reddit=limit_reddit,
                limit_youtube=limit_youtube,
            )
        except Exception as e:
            logger.exception(f"[runner] collection cycle failed: {e}")
        elapsed = (datetime.now(UTC) - started).total_seconds()
        sleep_for = max(0.0, interval_minutes * 60 - elapsed)
        await asyncio.sleep(sleep_for)


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
                await collect_once(
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
