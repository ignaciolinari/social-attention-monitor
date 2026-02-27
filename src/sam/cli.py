"""
SAM CLI

Command-line interface for Social Attention Monitor.
"""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from sam import __version__
from sam.config import get_settings
from sam.logging import setup_logging


def main() -> None:
    """Main CLI entry point."""
    setup_logging()

    parser = argparse.ArgumentParser(prog="sam", description="Social Attention Monitor (SAM)")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("demo", help="Run demo collectors with mock data")
    recompute = subparsers.add_parser(
        "recompute-metrics",
        help="Backfill/recompute metrics snapshots for a date range",
    )
    recompute.add_argument(
        "--from",
        dest="from_ts",
        required=True,
        help="Start timestamp (ISO 8601, e.g. 2026-01-30T00:00:00+00:00)",
    )
    recompute.add_argument(
        "--to",
        dest="to_ts",
        required=True,
        help="End timestamp (ISO 8601, e.g. 2026-01-31T00:00:00+00:00)",
    )
    recompute.add_argument(
        "--windows",
        default="1,24",
        help="Comma-separated snapshot windows in hours (default: 1,24)",
    )
    recompute.add_argument(
        "--bucket-hours",
        type=int,
        default=1,
        help="Snapshot bucket size (default: 1 hour)",
    )

    subparsers.add_parser(
        "benchmark-sentiment",
        help="Run sentiment analysis performance benchmark (VADER vs RoBERTa)",
    )
    subparsers.add_parser(
        "compare-sentiment",
        help="Compare sentiment analysis results on recent DB mentions",
    )

    args = parser.parse_args()

    print(f"""
╔═══════════════════════════════════════════════════════════╗
║  📊 Social Attention Monitor (SAM) v{__version__}               ║
║  Real-time social attention tracking for film & TV        ║
╚═══════════════════════════════════════════════════════════╝
    """)

    settings = get_settings()

    print(f"Environment: {settings.sam_env}")
    print(f"Demo Mode: {'✅ Enabled' if settings.demo_mode else '❌ Disabled'}")
    print()

    # Check API configurations
    print("API Status:")

    # Check for runtime overrides from the dashboard (shared via Redis).
    from sam.cache import collector_toggle_get_sync

    def _effective_enabled(platform: str, env_enabled: bool) -> tuple[bool, str]:
        """Return (effective_enabled, suffix) merging env + Redis override."""
        override = collector_toggle_get_sync(platform)
        if override is not None and override != env_enabled:
            tag = "enabled" if override else "disabled"
            return override, f"  (runtime override: {tag})"
        return env_enabled, ""

    reddit_on, reddit_suffix = _effective_enabled("reddit", settings.reddit.enabled)
    if not reddit_on:
        reddit_status = "⏸️  Disabled"
    elif settings.reddit.has_credentials:
        reddit_status = "✅ Configured"
    else:
        reddit_status = "❌ Not configured"
    print(f"  Reddit:  {reddit_status}{reddit_suffix}")

    yt_on, yt_suffix = _effective_enabled("youtube", settings.youtube.enabled)
    if not yt_on:
        youtube_status = "⏸️  Disabled"
    elif settings.youtube.has_credentials:
        youtube_status = "✅ Configured"
    else:
        youtube_status = "❌ Not configured"
    print(f"  YouTube: {youtube_status}{yt_suffix}")

    print(f"  TMDB:    {'✅ Configured' if settings.tmdb.is_configured else '❌ Not configured'}")

    bsky_on, bsky_suffix = _effective_enabled("bluesky", settings.bluesky.enabled)
    if not bsky_on:
        bluesky_status = "⏸️  Disabled"
    elif settings.bluesky.has_credentials:
        bluesky_status = "✅ Configured"
    else:
        bluesky_status = "❌ Not configured"
    print(f"  Bluesky: {bluesky_status}{bsky_suffix}")
    print()

    print("Available commands:")
    print("  make run-api        Start the FastAPI server")
    print("  make run-dashboard  Start the Streamlit dashboard")
    print("  make run-collector  Start the data collector")
    print("  make test           Run tests")
    print()

    if settings.demo_mode:
        print("💡 Demo mode is enabled. Add your API keys to .env to collect live data.")

    if args.command == "demo":
        asyncio.run(demo())
    elif args.command == "recompute-metrics":
        asyncio.run(
            recompute_metrics(
                from_ts=args.from_ts,
                to_ts=args.to_ts,
                windows=args.windows,
                bucket_hours=args.bucket_hours,
            )
        )
    elif args.command == "benchmark-sentiment":
        benchmark_sentiment()
    elif args.command == "compare-sentiment":
        asyncio.run(compare_sentiment())


async def demo() -> None:
    """Run a demo of the collectors."""
    setup_logging()

    from sam.collectors.bluesky import BlueskyCollector
    from sam.collectors.reddit import RedditCollector
    from sam.collectors.tmdb import TMDBCollector
    from sam.collectors.youtube import YouTubeCollector
    from sam.processors.sentiment import analyze_sentiment

    print("\n🎬 SAM Demo - Collecting sample data...\n")

    # TMDB trending
    tmdb = TMDBCollector(demo_mode=True)
    trending = await tmdb.get_trending(limit=5)
    print("📊 Trending Titles (from TMDB):")
    for t in trending:
        print(f"  • {t.title} ({t.media_type}) - ⭐ {t.vote_average}")
    print()

    # Reddit mentions
    reddit = RedditCollector(demo_mode=True)
    reddit_result = await reddit.collect(query="The Last of Us", limit=5)
    print(f"📱 Reddit Mentions ({len(reddit_result.posts)} posts):")
    for post in reddit_result.posts[:3]:
        sentiment = analyze_sentiment(post.content)
        print(
            f"  • r/{post.metrics.get('subreddit')} | Score: {post.metrics.get('score')} | Sentiment: {sentiment.label}"
        )
    print()

    # YouTube videos
    youtube = YouTubeCollector(demo_mode=True)
    yt_result = await youtube.collect(query="The Last of Us trailer", limit=5)
    print(f"🎥 YouTube Videos ({len(yt_result.posts)} videos):")
    for video in yt_result.posts[:3]:
        views = video.metrics.get("view_count", 0)
        print(f"  • {video.author} | Views: {views:,}")
    print()

    # Bluesky posts
    bluesky = BlueskyCollector(demo_mode=True)
    bsky_result = await bluesky.collect(query="The Last of Us", limit=5)
    print(f"🦋 Bluesky Posts ({len(bsky_result.posts)} posts):")
    for post in bsky_result.posts[:3]:
        sentiment = analyze_sentiment(post.content)
        likes = post.metrics.get("likes", 0)
        print(f"  • @{post.author} | Likes: {likes} | Sentiment: {sentiment.label}")
    print()

    await tmdb.close()
    await youtube.close()
    await bluesky.close()

    print("✅ Demo complete! Run 'make run-dashboard' to see the dashboard.\n")


def _parse_iso8601(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _floor_to_bucket(dt: datetime, *, bucket_hours: int) -> datetime:
    # Hour buckets only.
    hour = (dt.hour // bucket_hours) * bucket_hours
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0)


async def recompute_metrics(
    *,
    from_ts: str,
    to_ts: str,
    windows: str,
    bucket_hours: int,
) -> None:
    """Backfill/recompute metrics snapshots for all active titles."""
    setup_logging()

    from sam.pipeline.metrics_snapshots import compute_and_upsert_metrics_snapshots_multi
    from sam.storage.database import get_session
    from sam.storage.repository import list_active_titles

    start = _floor_to_bucket(_parse_iso8601(from_ts), bucket_hours=bucket_hours)
    end = _floor_to_bucket(_parse_iso8601(to_ts), bucket_hours=bucket_hours)
    if end < start:
        raise ValueError("--to must be >= --from")

    window_hours = [int(w.strip()) for w in windows.split(",") if w.strip()]
    if not window_hours:
        raise ValueError("--windows must include at least one integer")

    print(
        f"Recomputing snapshots for windows={window_hours}, "
        f"bucket_hours={bucket_hours}, range=[{start.isoformat()} .. {end.isoformat()}]"
    )

    async with get_session() as session:
        offset = 0
        titles_total = 0
        all_titles: list[Any] = []
        while True:
            titles = await list_active_titles(session, limit=200, offset=offset)
            if not titles:
                break
            offset += len(titles)
            titles_total += len(titles)
            all_titles.extend(titles)

    # Process each title in its own session/transaction so that a failure
    # on one title does not roll back work already committed for others.
    for title in all_titles:
        async with get_session() as session:
            snapshot_time = start
            while snapshot_time <= end:
                await compute_and_upsert_metrics_snapshots_multi(
                    session,
                    title_id=title.id,
                    snapshot_time=snapshot_time,
                    window_hours_list=window_hours,
                )
                snapshot_time = snapshot_time + timedelta(hours=bucket_hours)

    print(f"✅ Done. Processed {titles_total} titles.")


def benchmark_sentiment() -> None:
    """Run sentiment analysis benchmark."""
    import time

    from loguru import logger

    from sam.processors.sentiment import SentimentAnalyzer, SentimentModel

    print("\n🚀 Starting Sentiment Analysis Benchmark...\n")

    # Initialize VADER
    start = time.time()
    vader = SentimentAnalyzer(model=SentimentModel.VADER)
    vader_init_time = time.time() - start
    print(f"VADER Init Time:   {vader_init_time:.4f}s")

    # Initialize RoBERTa
    try:
        start = time.time()
        roberta = SentimentAnalyzer(model=SentimentModel.ROBERTA)
        roberta_init_time = time.time() - start
        print(f"RoBERTa Init Time: {roberta_init_time:.4f}s")
    except Exception as e:
        logger.error(f"Failed to initialize RoBERTa: {e}")
        return

    # Test Data
    texts = [
        "This movie is absolutely fantastic! I loved every moment of it.",
        "The plot was boring and the acting was terrible.",
        "It was okay, not great but not bad either.",
        "I'm not sure how I feel about this.",
        "Best experience of my life!",
        "Worst mistake ever.",
        "The cinematography was good but the story fell flat.",
        "Highly recommended for everyone.",
        "Do not watch this, complete waste of time.",
        "An average film with some good moments.",
    ] * 10  # 100 texts

    print(f"\nBenchmarking on {len(texts)} texts...")

    # Benchmark VADER
    start = time.time()
    vader.analyze_batch(texts)
    vader_time = time.time() - start
    print(f"VADER Total Time:  {vader_time:.4f}s")
    print(f"VADER Avg/Text:    {vader_time / len(texts):.6f}s")

    # Benchmark RoBERTa
    start = time.time()
    roberta.analyze_batch(texts)
    roberta_time = time.time() - start
    print(f"RoBERTa Total Time: {roberta_time:.4f}s")
    print(f"RoBERTa Avg/Text:   {roberta_time / len(texts):.6f}s")

    # Comparison
    if vader_time > 0:
        ratio = roberta_time / vader_time
        print(f"\nℹ️  RoBERTa is {ratio:.1f}x slower than VADER")


async def compare_sentiment() -> None:
    """Compare VADER vs RoBERTa on recent DB mentions."""
    from loguru import logger
    from sqlalchemy import desc, select

    from sam.processors.sentiment import SentimentAnalyzer, SentimentModel
    from sam.storage.database import get_session
    from sam.storage.models import Mention

    print("\n🔍 Comparing Sentiment Models on Real Data...\n")

    # Initialize both analyzers
    vader = SentimentAnalyzer(model=SentimentModel.VADER)
    try:
        roberta = SentimentAnalyzer(model=SentimentModel.ROBERTA)
    except Exception as e:
        logger.error(f"Failed to initialize RoBERTa: {e}")
        return

    async with get_session() as session:
        # Fetch recent mentions with content
        stmt = (
            select(Mention)
            .where(Mention.content.is_not(None))
            .order_by(desc(Mention.created_at))
            .limit(20)
        )
        result = await session.execute(stmt)
        mentions = result.scalars().all()

        if not mentions:
            print("⚠️  No mentions found in database.")
            return

        print(f"{'TEXT (TRUNCATED)':<50} | {'VADER':<12} | {'ROBERTA':<12}")
        print("-" * 82)

        for mention in mentions:
            text = mention.content
            if text is None:
                continue

            # Clean/truncate text for display
            display_text = (text[:47] + "...") if len(text) > 47 else text
            display_text = display_text.replace("\n", " ")

            # Analyze
            v_res = vader.analyze(text)
            r_res = roberta.analyze(text)

            # Format Output
            v_out = f"{v_res.label[:3].upper()} ({v_res.compound:.2f})"
            r_out = f"{r_res.label[:3].upper()} ({r_res.compound:.2f})"

            print(f"{display_text:<50} | {v_out:<12} | {r_out:<12}")
    print()


if __name__ == "__main__":
    main()
