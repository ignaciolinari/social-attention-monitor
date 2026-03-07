from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from asyncpg.exceptions import InvalidCatalogNameError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sam.collectors.base import CollectedPost
from sam.collectors.tmdb import TMDBTitle
from sam.pipeline.metrics_snapshots import compute_and_upsert_metrics_snapshot
from sam.processors.metrics import MetricsCalculator
from sam.scheduler import runner
from sam.storage.models import Base, Lease, PipelineRun
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


# ---------------------------------------------------------------------------
# Real-data-shaped tests
# ---------------------------------------------------------------------------


def _make_title(tmdb_id: int = 900, title: str = "Test Title") -> TMDBTitle:
    """Helper: minimal TMDBTitle for test fixtures."""
    return TMDBTitle(
        tmdb_id=tmdb_id,
        title=title,
        original_title=title,
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


@pytest.mark.asyncio
async def test_youtube_shaped_mentions_and_engagement() -> None:
    """Insert YouTube-shaped CollectedPosts and verify engagement is computed correctly.

    The metrics calculator should sum view_count + like_count + comment_count for
    YouTube mentions (not Reddit keys like score/num_comments).
    """
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
        db_title = await upsert_title(session, _make_title(tmdb_id=700, title="YT Engagement"))
        await session.commit()

        now = datetime.now(UTC)
        posts = [
            CollectedPost(
                platform="youtube",
                source_id=f"yt_eng_{i}",
                source_type="video",
                content=f"Great video about the movie #{i}",
                author=f"channel_{i}",
                url=f"https://youtube.com/watch?v=yt_eng_{i}",
                created_at=now - timedelta(minutes=10 * i),
                metrics={
                    "view_count": 10_000 * (i + 1),
                    "like_count": 500 * (i + 1),
                    "comment_count": 50 * (i + 1),
                },
            )
            for i in range(3)
        ]

        inserted = await insert_mentions(
            session,
            title_id=db_title.id,
            platform="youtube",
            posts=posts,
            sentiment_by_source_id={
                p.source_id: {"compound": 0.5, "label": "positive"} for p in posts
            },
        )
        await session.commit()

        assert inserted == 3

        # Verify the metrics calculator handles YouTube-shaped data correctly
        window_start = now - timedelta(hours=1)
        mentions = await get_mentions_in_window(
            session,
            title_id=db_title.id,
            window_start=window_start,
            window_end=now + timedelta(hours=1),
        )
        assert len(mentions) == 3

        calc = MetricsCalculator()
        mention_dicts = [
            {
                "created_at": m.collected_at,
                "platform": m.platform,
                "author": m.author,
                "metrics": m.metrics or {},
                "sentiment": m.sentiment or {},
            }
            for m in mentions
        ]
        computed = calc.calculate(
            mention_dicts,
            window_hours=24,
            window_end=now + timedelta(hours=1),
        )

        # Total engagement = sum of (view_count + like_count + comment_count) for each post
        # Post 0: 10000 + 500 + 50 = 10550
        # Post 1: 20000 + 1000 + 100 = 21100
        # Post 2: 30000 + 1500 + 150 = 31650
        # Total: 63300
        expected_engagement = sum(10_000 * (i + 1) + 500 * (i + 1) + 50 * (i + 1) for i in range(3))
        assert computed.total_engagement == expected_engagement
        assert computed.platform_breakdown.get("youtube") == 3
        assert computed.mention_count == 3

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_snapshot_window_captures_recent_mentions() -> None:
    """Verify that a snapshot with ceil-to-hour timing captures mentions
    collected during the current hour.

    This validates the _snapshot_bucket ceil fix: if we collect at 14:37, the
    snapshot_time becomes 15:00, and the 1-hour window (14:00-15:00) includes
    the mention collected at ~14:37.
    """
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
        db_title = await upsert_title(session, _make_title(tmdb_id=800, title="Window Test"))
        await session.commit()

        # Simulate collecting at 14:37
        now = datetime(2026, 3, 15, 14, 37, 0, tzinfo=UTC)
        source_id = f"window_test_{uuid4().hex}"
        post = CollectedPost(
            platform="youtube",
            source_id=source_id,
            source_type="video",
            content="Interesting movie review",
            author="reviewer",
            url=f"https://youtube.com/watch?v={source_id}",
            created_at=now - timedelta(hours=2),  # published earlier
            metrics={"view_count": 1000, "like_count": 100, "comment_count": 10},
        )

        await insert_mentions(
            session,
            title_id=db_title.id,
            platform="youtube",
            posts=[post],
            sentiment_by_source_id={
                source_id: {"compound": 0.3, "label": "positive"},
            },
            collected_at=now,
        )
        await session.commit()

        # Snapshot at the ceil of the collection time: 15:00
        # The 1-hour window is 14:00 - 15:00
        snapshot_time = datetime(2026, 3, 15, 15, 0, 0, tzinfo=UTC)
        await compute_and_upsert_metrics_snapshot(
            session,
            title_id=db_title.id,
            snapshot_time=snapshot_time,
            window_hours=1,
        )
        await session.commit()

        snap = await get_latest_metrics_snapshot(session, title_id=db_title.id, window_hours=1)
        assert snap is not None, "Snapshot should have been created"

        # The mention was collected_at ~14:37, which is inside 14:00-15:00
        # so the snapshot should see it.
        assert snap.mention_count == 1, (
            f"Expected 1 mention in the 1-hour window, got {snap.mention_count}"
        )
        assert snap.youtube_mentions == 1

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_mentions_in_window_respects_limit() -> None:
    """B5: get_mentions_in_window honours the ``limit`` parameter and returns
    the most recent mentions when capped."""
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
            tmdb_id=99999,
            title="LimitTestTitle",
            original_title="LimitTestTitle",
            media_type="movie",
            release_date=datetime.now(UTC),
            overview="limit test",
            poster_path=None,
            backdrop_path=None,
            popularity=1.0,
            vote_average=7.0,
            vote_count=5,
            genres=[],
            original_language="en",
            raw_data={},
        )

        db_title = await upsert_title(session, title)
        await session.commit()

        now = datetime.now(UTC)
        posts = [
            CollectedPost(
                platform="reddit",
                source_id=f"limit_test_{i}",
                source_type="post",
                content=f"Post {i}",
                author=f"author_{i}",
                url=f"https://reddit.com/{i}",
                created_at=now - timedelta(minutes=10 - i),  # 0 is oldest
                metrics={},
            )
            for i in range(5)
        ]

        await insert_mentions(
            session,
            title_id=db_title.id,
            platform="reddit",
            posts=posts,
        )
        await session.commit()

        # Fetch all
        all_mentions = await get_mentions_in_window(
            session,
            title_id=db_title.id,
            window_start=now - timedelta(hours=1),
            window_end=now + timedelta(hours=1),
        )
        assert len(all_mentions) == 5

        # Fetch with limit=3 — should get the 3 most recent
        limited = await get_mentions_in_window(
            session,
            title_id=db_title.id,
            window_start=now - timedelta(hours=1),
            window_end=now + timedelta(hours=1),
            limit=3,
        )
        assert len(limited) == 3

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_force_clear_one_shot_state_only_clears_stale_rows(monkeypatch) -> None:
    """Real DB coverage for one-shot recovery: active leases are protected, stale state is cleared."""
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
    lease_name = f"sam:test-collector-cycle:{uuid4().hex}"
    job_name = f"collector-cycle-test-{uuid4().hex}"

    @asynccontextmanager
    async def session_override():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(runner, "get_session", session_override)
    monkeypatch.setattr(runner, "LEASE_NAME", lease_name)
    monkeypatch.setattr(runner, "JOB_NAME", job_name)

    async with Session() as session:
        active_owner = uuid4()
        session.add(
            Lease(
                name=lease_name,
                owner_id=active_owner,
                acquired_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
                updated_at=datetime.now(UTC),
            )
        )
        session.add(
            PipelineRun(
                job_name=job_name,
                owner_id=active_owner,
                status="running",
                started_at=datetime.now(UTC),
                finished_at=None,
                error=None,
                stats=None,
            )
        )
        await session.commit()

    with pytest.raises(RuntimeError, match="active collector lease"):
        await runner._force_clear_one_shot_state()

    async with Session() as session:
        active_lease = await session.get(Lease, lease_name)
        active_runs = [
            row
            for row in (
                await session.execute(
                    PipelineRun.__table__.select().where(PipelineRun.job_name == job_name)
                )
            )
            .mappings()
            .all()
            if row["status"] == "running"
        ]
        assert active_lease is not None
        assert len(active_runs) == 1

        await session.execute(PipelineRun.__table__.delete())
        await session.execute(Lease.__table__.delete())
        await session.commit()

    async with Session() as session:
        stale_owner = uuid4()
        fresh_owner = uuid4()
        session.add(
            Lease(
                name=lease_name,
                owner_id=stale_owner,
                acquired_at=datetime.now(UTC) - timedelta(minutes=20),
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
                updated_at=datetime.now(UTC) - timedelta(minutes=20),
            )
        )
        session.add(
            PipelineRun(
                job_name=job_name,
                owner_id=stale_owner,
                status="running",
                started_at=datetime.now(UTC) - timedelta(minutes=20),
                finished_at=None,
                error=None,
                stats=None,
            )
        )
        session.add(
            PipelineRun(
                job_name=job_name,
                owner_id=fresh_owner,
                status="running",
                started_at=datetime.now(UTC),
                finished_at=None,
                error=None,
                stats=None,
            )
        )
        await session.commit()

    released, failed_runs = await runner._force_clear_one_shot_state()

    assert released == 1
    assert failed_runs == 1

    async with Session() as session:
        assert await session.get(Lease, lease_name) is None
        run_rows = (
            (
                await session.execute(
                    PipelineRun.__table__.select().where(PipelineRun.job_name == job_name)
                )
            )
            .mappings()
            .all()
        )
        assert len(run_rows) == 2
        stale_rows = [row for row in run_rows if row["owner_id"] == stale_owner]
        fresh_rows = [row for row in run_rows if row["owner_id"] == fresh_owner]
        assert len(stale_rows) == 1
        assert stale_rows[0]["status"] == "failed"
        assert stale_rows[0]["error"] == "stale run cleared by one-shot recovery"
        assert len(fresh_rows) == 1
        assert fresh_rows[0]["status"] == "running"

        await session.execute(
            PipelineRun.__table__.delete().where(PipelineRun.job_name == job_name)
        )
        await session.execute(Lease.__table__.delete().where(Lease.name == lease_name))
        await session.commit()

    await engine.dispose()
