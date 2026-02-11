"""Tests for Reddit collector close() method and interface consistency."""

from __future__ import annotations

import pytest

from sam.collectors.bluesky import BlueskyCollector
from sam.collectors.reddit import RedditCollector
from sam.collectors.tmdb import TMDBCollector
from sam.collectors.youtube import YouTubeCollector


@pytest.mark.asyncio
async def test_reddit_collector_has_close_method() -> None:
    """Test that RedditCollector has async close() method for interface consistency."""
    collector = RedditCollector(demo_mode=True)

    # Should have close method
    assert hasattr(collector, "close")

    # Should be callable without error
    await collector.close()

    # Client should be None after close
    assert collector._client is None


@pytest.mark.asyncio
async def test_youtube_collector_has_close_method() -> None:
    """Test that YouTubeCollector has async close() method."""
    collector = YouTubeCollector(demo_mode=True)

    assert hasattr(collector, "close")
    await collector.close()


@pytest.mark.asyncio
async def test_tmdb_collector_has_close_method() -> None:
    """Test that TMDBCollector has async close() method."""
    collector = TMDBCollector(demo_mode=True)

    assert hasattr(collector, "close")
    await collector.close()


@pytest.mark.asyncio
async def test_bluesky_collector_has_close_method() -> None:
    """Test that BlueskyCollector has async close() method."""
    collector = BlueskyCollector(demo_mode=True)

    assert hasattr(collector, "close")
    await collector.close()


@pytest.mark.asyncio
async def test_all_collectors_have_consistent_interface() -> None:
    """Test that all collectors have the same interface methods."""
    collectors = [
        RedditCollector(demo_mode=True),
        YouTubeCollector(demo_mode=True),
        TMDBCollector(demo_mode=True),
        BlueskyCollector(demo_mode=True),
    ]

    for collector in collectors:
        # All should have is_configured property
        assert hasattr(collector, "is_configured")

        # All should have close method
        assert hasattr(collector, "close")

        # Clean up
        await collector.close()


@pytest.mark.asyncio
async def test_bluesky_collector_demo_mode() -> None:
    """Test that BlueskyCollector correctly generates demo data."""
    collector = BlueskyCollector(demo_mode=True)

    result = await collector.collect(query="Dune", limit=5)

    assert result.success
    assert result.platform == "bluesky"
    assert len(result.posts) <= 20  # Demo mode caps at 20
    assert all(p.platform == "bluesky" for p in result.posts)

    await collector.close()
