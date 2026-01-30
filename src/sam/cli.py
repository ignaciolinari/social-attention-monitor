"""
SAM CLI

Command-line interface for Social Attention Monitor.
"""

import argparse
import asyncio

from sam import __version__
from sam.config import get_settings
from sam.logging import setup_logging


def main() -> None:
    """Main CLI entry point."""
    setup_logging()

    parser = argparse.ArgumentParser(prog="sam", description="Social Attention Monitor (SAM)")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("demo", help="Run demo collectors with mock data")
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
    print(f"  Reddit:  {'✅ Configured' if settings.reddit.is_configured else '❌ Not configured'}")
    print(
        f"  YouTube: {'✅ Configured' if settings.youtube.is_configured else '❌ Not configured'}"
    )
    print(f"  TMDB:    {'✅ Configured' if settings.tmdb.is_configured else '❌ Not configured'}")
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


async def demo() -> None:
    """Run a demo of the collectors."""
    setup_logging()

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

    await tmdb.close()
    await youtube.close()

    print("✅ Demo complete! Run 'make run-dashboard' to see the dashboard.\n")


if __name__ == "__main__":
    main()
