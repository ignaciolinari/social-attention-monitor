from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from asyncpg.exceptions import InvalidCatalogNameError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sam.collectors.base import CollectedPost
from sam.collectors.tmdb import TMDBTitle
from sam.storage.models import Base
from sam.storage.repository import (
    get_latest_metrics_snapshot,
    get_mentions_count,
    get_mentions_in_window,
    insert_mentions,
    upsert_metrics_snapshot,
    upsert_title,
)


@pytest.mark.asyncio
async def test_repository_insert_and_count() -> None:
    db_url = os.getenv("SAM_TEST_DATABASE_URL")
    if not db_url:
        pytest.skip("SAM_TEST_DATABASE_URL not set")

    engine = create_async_engine(db_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except InvalidCatalogNameError:
        await engine.dispose()
        pytest.skip("Test database not available")

    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with Session() as session:
        title = TMDBTitle(
            tmdb_id=123,
            title="Dune: Part Two",
            original_title="Dune: Part Two",
            media_type="movie",
            release_date=datetime.now(UTC),
            overview="test",
            poster_path=None,
            backdrop_path=None,
            popularity=1.0,
            vote_average=8.0,
            vote_count=10,
            genres=[],
            original_language="en",
            raw_data={},
        )

        db_title = await upsert_title(session, title)
        await session.commit()

        post = CollectedPost(
            platform="reddit",
            source_id="abc",
            source_type="post",
            content="hello",
            author="user",
            url="https://example.com",
            created_at=datetime.now(UTC),
            metrics={"score": 1, "num_comments": 0},
        )

        inserted = await insert_mentions(
            session,
            title_id=db_title.id,
            platform="reddit",
            posts=[post],
            sentiment_by_source_id={"abc": {"compound": 0.1}},
        )
        await session.commit()

        count = await get_mentions_count(session, title_id=db_title.id, platform="reddit")

        assert inserted == 1
        assert count == 1

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_mentions_window_and_metrics_snapshot() -> None:
    db_url = os.getenv("SAM_TEST_DATABASE_URL")
    if not db_url:
        pytest.skip("SAM_TEST_DATABASE_URL not set")

    engine = create_async_engine(db_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except InvalidCatalogNameError:
        await engine.dispose()
        pytest.skip("Test database not available")

    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with Session() as session:
        title = TMDBTitle(
            tmdb_id=456,
            title="Severance",
            original_title="Severance",
            media_type="tv",
            release_date=datetime.now(UTC),
            overview="test",
            poster_path=None,
            backdrop_path=None,
            popularity=1.0,
            vote_average=8.0,
            vote_count=10,
            genres=[],
            original_language="en",
            raw_data={},
        )

        db_title = await upsert_title(session, title)
        await session.commit()

        now = datetime.now(UTC)
        post = CollectedPost(
            platform="youtube",
            source_id="xyz",
            source_type="video",
            content="hello",
            author="creator",
            url="https://example.com",
            created_at=now,
            metrics={"like_count": 3, "comment_count": 1},
        )

        await insert_mentions(
            session,
            title_id=db_title.id,
            platform="youtube",
            posts=[post],
            sentiment_by_source_id={"xyz": {"compound": 0.2}},
        )
        await session.commit()

        window_start = now - timedelta(hours=1)
        window_end = now + timedelta(hours=1)
        mentions = await get_mentions_in_window(
            session,
            title_id=db_title.id,
            window_start=window_start,
            window_end=window_end,
        )
        assert len(mentions) == 1

        await upsert_metrics_snapshot(
            session,
            title_id=db_title.id,
            snapshot_time=now,
            window_hours=1,
            metrics={
                "mention_count": 1,
                "unique_authors": 1,
                "reddit_mentions": 0,
                "youtube_mentions": 1,
                "mention_velocity": 1.0,
                "velocity_change": 0.0,
                "avg_sentiment": 0.2,
                "sentiment_volatility": 0.0,
                "positive_ratio": 1.0,
                "attention_index": 50.0,
                "hype_acceleration": 0.0,
                "raw_metrics": {"window_start": window_start.isoformat()},
            },
        )
        await session.commit()

        latest = await get_latest_metrics_snapshot(session, title_id=db_title.id, window_hours=1)
        assert latest is not None
        assert latest.mention_count == 1

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
