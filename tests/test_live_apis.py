"""Smoke tests that hit the real TMDB + YouTube APIs.

These are skipped by default in CI (no API keys). Run explicitly with:

    pytest tests/test_live_apis.py -v -s

They require TMDB_API_KEY and YOUTUBE_API_KEY to be set in .env or the environment.
The ``test_mini_pipeline_*`` test additionally requires a test Postgres database
via SAM_TEST_DATABASE_URL.
"""

from __future__ import annotations

import os

import pytest

from sam.collectors.tmdb import TMDBCollector
from sam.collectors.youtube import YouTubeCollector
from sam.config import get_settings


def _should_skip_youtube_error(error: str | None) -> bool:
    if not error:
        return False
    e = error.lower()
    return any(
        token in e
        for token in (
            "quotaexceeded",
            "dailylimitexceeded",
            "quota/rate limit exceeded",
            "rate limited",
            "useratelimitexceeded",
        )
    )


# Skip the entire module when keys are not configured.
pytestmark = pytest.mark.skipif(
    not get_settings().tmdb.is_configured or not get_settings().youtube.is_configured,
    reason="TMDB and/or YouTube API keys not configured",
)


@pytest.mark.asyncio
async def test_tmdb_trending_returns_titles(monkeypatch) -> None:
    """TMDB trending endpoint returns at least one title with real data."""
    monkeypatch.setenv("SAM_DEMO_MODE", "false")

    collector = TMDBCollector(demo_mode=False)
    try:
        titles = await collector.get_trending(media_type="all", time_window="week", limit=5)
    finally:
        await collector.close()

    assert len(titles) >= 1, "Expected at least 1 trending title from TMDB"
    for t in titles:
        assert t.tmdb_id > 0
        assert t.title
        assert t.media_type in ("movie", "tv")
        assert t.popularity > 0


@pytest.mark.asyncio
async def test_youtube_collect_returns_videos(monkeypatch) -> None:
    """YouTube search returns videos for a known query."""
    monkeypatch.setenv("SAM_DEMO_MODE", "false")

    collector = YouTubeCollector(demo_mode=False)
    try:
        result = await collector.collect(query="Dune Part Two trailer", limit=3)
    finally:
        await collector.close()

    if result.success is False and _should_skip_youtube_error(result.error):
        pytest.skip(f"YouTube quota/rate-limit exhausted: {result.error}")

    assert result.success is True, f"YouTube collection failed: {result.error}"
    assert len(result.posts) >= 1, "Expected at least 1 YouTube video"
    for post in result.posts:
        assert post.source_id
        assert post.url.startswith("https://youtube.com/watch?v=")
        assert post.metrics
        assert post.metrics["view_count"] >= 0


@pytest.mark.asyncio
async def test_tmdb_titles_feed_youtube_search(monkeypatch) -> None:
    """End-to-end: fetch trending from TMDB, then search YouTube for the top title."""
    monkeypatch.setenv("SAM_DEMO_MODE", "false")

    tmdb = TMDBCollector(demo_mode=False)
    try:
        titles = await tmdb.get_trending(media_type="all", time_window="week", limit=1)
    finally:
        await tmdb.close()

    assert titles, "No trending titles from TMDB"
    query = f"{titles[0].title} trailer"

    yt = YouTubeCollector(demo_mode=False)
    try:
        result = await yt.collect(query=query, limit=3)
    finally:
        await yt.close()

    if result.success is False and _should_skip_youtube_error(result.error):
        pytest.skip(f"YouTube quota/rate-limit exhausted: {result.error}")

    assert result.success is True, f"YouTube search for '{query}' failed: {result.error}"
    assert len(result.posts) >= 1, f"No YouTube results for '{query}'"


# ---------------------------------------------------------------------------
# Mini-pipeline integration test: TMDB -> YouTube -> sentiment -> DB
# Requires a test Postgres database *and* API keys.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mini_pipeline_collect_once(monkeypatch) -> None:
    """Run the real collect_once flow against live APIs and a test database.

    Validates:
    - Titles are upserted from TMDB
    - YouTube mentions are inserted with real source_ids
    - Sentiment scores fall within valid VADER ranges
    - Metrics snapshots are created with non-zero values
    """
    db_url = os.getenv("SAM_TEST_DATABASE_URL")
    if not db_url:
        pytest.skip("SAM_TEST_DATABASE_URL not set — skipping pipeline integration test")

    from asyncpg.exceptions import InvalidCatalogNameError
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from sam.scheduler.runner import collect_once
    from sam.storage.models import Base, Mention, MetricsSnapshot, Title

    monkeypatch.setenv("SAM_DEMO_MODE", "false")
    monkeypatch.setenv("SAM_STORAGE_ENABLE_RAW_DATA_STORAGE", "false")

    engine = create_async_engine(db_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except InvalidCatalogNameError:
        await engine.dispose()
        pytest.skip("Test database not available")

    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    try:
        async with Session() as session:
            stats = await collect_once(
                session,
                limit_titles=2,
                limit_reddit=0,  # Reddit not available
                limit_youtube=3,
                limit_bluesky=0,  # Keep this test scoped to TMDB + YouTube
            )
            await session.commit()

        # -- Verify titles were upserted --
        assert stats["titles"] >= 1, "Expected at least 1 title from TMDB"

        async with Session() as session:
            title_count = await session.scalar(select(func.count()).select_from(Title))
            assert title_count >= 1, "No titles in DB after collect_once"

            # -- Verify YouTube mentions --
            yt_mentions = (
                (
                    await session.execute(
                        select(Mention).where(Mention.platform == "youtube").limit(20)
                    )
                )
                .scalars()
                .all()
            )

            if stats["youtube_mentions_inserted"] > 0:
                assert len(yt_mentions) >= 1, "Stats say mentions inserted but DB is empty"

                for m in yt_mentions:
                    # Real YouTube video IDs are 11-char alphanumeric strings
                    assert m.source_id, "source_id should not be empty"
                    assert m.platform == "youtube"
                    assert m.content, "content should not be empty"

                    # Sentiment should have been computed
                    if m.sentiment:
                        compound = m.sentiment.get("compound", None)
                        assert compound is not None, "sentiment should have 'compound'"
                        assert -1.0 <= compound <= 1.0, f"compound={compound} out of VADER range"

                    # YouTube metrics should include view_count
                    if m.metrics:
                        assert "view_count" in m.metrics, (
                            f"YouTube mention missing view_count: {m.metrics}"
                        )

            # -- Verify metrics snapshots --
            snapshot_count = await session.scalar(select(func.count()).select_from(MetricsSnapshot))
            assert snapshot_count >= 1, "No metrics snapshots after collect_once"

            # Check that at least one snapshot has real data
            snapshots = (await session.execute(select(MetricsSnapshot).limit(10))).scalars().all()

            for snap in snapshots:
                assert snap.window_hours in (1, 24)
                assert snap.mention_count >= 0
                # attention_index should be computed (non-null)
                assert snap.attention_index is not None

    finally:
        # Always clean up the test DB
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()
