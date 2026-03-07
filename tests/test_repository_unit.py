"""Unit tests for sam.storage.repository — pure logic & mocked session paths.

These tests do NOT require a live database.  They mock ``AsyncSession`` to
verify query construction, branch coverage, and edge-case handling.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from sam.storage.repository import (
    MentionProjection,
    clear_stale_one_shot_state,
    escape_like,
    fail_running_pipeline_runs,
    finish_pipeline_run,
    force_release_lease,
    get_all_watchlist_tmdb_ids,
    get_average_benchmark_trajectory,
    get_benchmark_contributors_count,
    get_latest_mention_collected_at,
    get_latest_metrics_snapshot,
    get_lease,
    get_mentions_count,
    get_mentions_for_title,
    get_mentions_in_window,
    get_mentions_in_window_lightweight,
    get_metrics_timeseries,
    get_title_by_id,
    get_title_by_name,
    get_trending_by_attention_index,
    insert_mentions,
    list_active_titles,
    list_titles,
    start_pipeline_run,
)

# ---------------------------------------------------------------------------
# escape_like
# ---------------------------------------------------------------------------


class TestEscapeLike:
    def test_no_special_chars(self) -> None:
        assert escape_like("hello") == "hello"

    def test_percent_escaped(self) -> None:
        assert escape_like("100%") == r"100\%"

    def test_underscore_escaped(self) -> None:
        assert escape_like("foo_bar") == r"foo\_bar"

    def test_backslash_escaped(self) -> None:
        assert escape_like(r"a\b") == r"a\\b"

    def test_multiple_specials(self) -> None:
        assert escape_like(r"a%b_c\d") == r"a\%b\_c\\d"

    def test_strips_whitespace(self) -> None:
        assert escape_like("  hello  ") == "hello"

    def test_empty_string(self) -> None:
        assert escape_like("") == ""


# ---------------------------------------------------------------------------
# MentionProjection dataclass
# ---------------------------------------------------------------------------


class TestMentionProjection:
    def test_creation(self) -> None:
        now = datetime.now(UTC)
        proj = MentionProjection(
            collected_at=now,
            platform="reddit",
            author="user1",
            source_type="post",
            metrics={"score": 42},
            sentiment={"compound": 0.5},
            content="hello world",
        )
        assert proj.platform == "reddit"
        assert proj.author == "user1"
        assert proj.metrics == {"score": 42}

    def test_asdict(self) -> None:
        now = datetime.now(UTC)
        proj = MentionProjection(
            collected_at=now,
            platform="youtube",
            author=None,
            source_type=None,
            metrics=None,
            sentiment=None,
            content=None,
        )
        d = asdict(proj)
        assert d["platform"] == "youtube"
        assert d["author"] is None
        assert d["content"] is None


# ---------------------------------------------------------------------------
# Helpers to build mock sessions
# ---------------------------------------------------------------------------


def _mock_session() -> AsyncMock:
    """Build an AsyncMock that mimics AsyncSession."""
    session = AsyncMock()
    # AsyncSession.add() is synchronous — override the default AsyncMock
    # so calling it doesn't produce a coroutine that's never awaited.
    session.add = MagicMock()
    return session


def _mock_execute_result(
    *,
    scalars_all: list | None = None,
    scalar_one: object = None,
    scalar_one_or_none: object = None,
    first: object = None,
    all_rows: list | None = None,
):
    """Build a mock result object returned by session.execute(...)."""
    result = MagicMock()
    if scalars_all is not None:
        result.scalars.return_value.all.return_value = scalars_all
        result.scalars.return_value.first.return_value = scalars_all[0] if scalars_all else None
    if scalar_one is not None:
        result.scalar_one.return_value = scalar_one
    if scalar_one_or_none is not None:
        result.scalar_one_or_none.return_value = scalar_one_or_none
    if first is not None:
        result.scalars.return_value.first.return_value = first
    if all_rows is not None:
        result.all.return_value = all_rows
    return result


# ---------------------------------------------------------------------------
# insert_mentions
# ---------------------------------------------------------------------------


class TestInsertMentions:
    @pytest.mark.asyncio
    async def test_empty_posts_returns_zero(self) -> None:
        session = _mock_session()
        count = await insert_mentions(
            session,
            title_id=uuid4(),
            platform="reddit",
            posts=[],
        )
        assert count == 0
        session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_insert_returns_count(self) -> None:
        from sam.collectors.base import CollectedPost

        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalars_all=[uuid4(), uuid4()])

        post1 = CollectedPost(
            platform="reddit",
            source_id="s1",
            source_type="post",
            content="hello",
            author="u1",
            url="https://example.com",
            created_at=datetime.now(UTC),
            metrics={},
        )
        post2 = CollectedPost(
            platform="reddit",
            source_id="s2",
            source_type="post",
            content="world",
            author="u2",
            url="https://example.com",
            created_at=datetime.now(UTC),
            metrics={},
        )

        count = await insert_mentions(
            session,
            title_id=uuid4(),
            platform="reddit",
            posts=[post1, post2],
            sentiment_by_source_id={"s1": {"compound": 0.1}},
        )
        assert count == 2

    @pytest.mark.asyncio
    async def test_insert_with_collected_at(self) -> None:
        from sam.collectors.base import CollectedPost

        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalars_all=[uuid4()])

        post = CollectedPost(
            platform="youtube",
            source_id="yt1",
            source_type="video",
            content="test",
            author="author",
            url="https://youtube.com",
            created_at=datetime.now(UTC),
            metrics={},
        )

        ts = datetime(2025, 1, 1, tzinfo=UTC)
        count = await insert_mentions(
            session,
            title_id=uuid4(),
            platform="youtube",
            posts=[post],
            collected_at=ts,
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_language_map_uses_composite_identity_key(self) -> None:
        from sam.collectors.base import CollectedPost, post_identity_key

        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalars_all=[uuid4()])

        post = CollectedPost(
            platform="youtube",
            source_id="shared-id",
            source_type="video",
            content="test",
            author="author",
            url="https://youtube.com",
            created_at=datetime.now(UTC),
            metrics={},
        )

        await insert_mentions(
            session,
            title_id=uuid4(),
            platform="youtube",
            posts=[post],
            language_by_source_id={
                post_identity_key("youtube", "video", "shared-id"): "en",
                "shared-id": "es",
            },
        )

        stmt = session.execute.call_args.args[0]
        assert stmt.compile().params["detected_language_m0"] == "en"


class TestWatchlistHelpers:
    @pytest.mark.asyncio
    async def test_get_all_watchlist_tmdb_ids_deduplicates_ids(self) -> None:
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(
            all_rows=[([1396, 438631],), ([438631, 95396],), (None,)]
        )

        result = await get_all_watchlist_tmdb_ids(session)

        assert result == {1396, 438631, 95396}

    @pytest.mark.asyncio
    async def test_get_all_watchlist_tmdb_ids_accepts_async_mock_rows(self) -> None:
        session = _mock_session()
        result = MagicMock()
        result.all = AsyncMock(return_value=[([1396],)])
        session.execute.return_value = result

        ids = await get_all_watchlist_tmdb_ids(session)

        assert ids == {1396}

    @pytest.mark.asyncio
    async def test_get_all_watchlist_tmdb_ids_ignores_malformed_values(self) -> None:
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(
            all_rows=[([1396, "bad", -1, 0, 438631],), ({"oops": True},), (None,)]
        )

        ids = await get_all_watchlist_tmdb_ids(session)

        assert ids == {1396, 438631}


class TestBenchmarkHelpers:
    @pytest.mark.asyncio
    async def test_get_average_benchmark_trajectory_maps_rows(self) -> None:
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(
            all_rows=[
                MagicMock(day=0, avg_attention_index=12.345, sample_count=3),
                MagicMock(day=1, avg_attention_index=9.0, sample_count=2),
            ]
        )

        rows = await get_average_benchmark_trajectory(
            session,
            target_title_id=uuid4(),
            comparison_type="movie",
            window_hours=24,
            days=7,
            comparison_limit=20,
        )

        assert rows == [
            {"day": 0, "avg_attention_index": 12.35, "sample_count": 3},
            {"day": 1, "avg_attention_index": 9.0, "sample_count": 2},
        ]

    @pytest.mark.asyncio
    async def test_get_benchmark_contributors_count_maps_scalar(self) -> None:
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalar_one=4)

        count = await get_benchmark_contributors_count(
            session,
            target_title_id=uuid4(),
            comparison_type="movie",
            window_hours=24,
            days=7,
            comparison_limit=20,
        )

        assert count == 4


# ---------------------------------------------------------------------------
# get_title_by_name / get_title_by_id
# ---------------------------------------------------------------------------


class TestTitleLookups:
    @pytest.mark.asyncio
    async def test_get_title_by_name_found(self) -> None:
        fake_title = MagicMock(title="Dune")
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(first=fake_title)

        result = await get_title_by_name(session, "Dune")
        assert result is fake_title

    @pytest.mark.asyncio
    async def test_get_title_by_name_not_found(self) -> None:
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        session.execute.return_value = mock_result

        result = await get_title_by_name(session, "Nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_title_by_id_found(self) -> None:
        fake_title = MagicMock(title="Dune")
        session = _mock_session()
        session.get.return_value = fake_title

        result = await get_title_by_id(session, uuid4())
        assert result is fake_title

    @pytest.mark.asyncio
    async def test_get_title_by_id_not_found(self) -> None:
        session = _mock_session()
        session.get.return_value = None

        result = await get_title_by_id(session, uuid4())
        assert result is None


# ---------------------------------------------------------------------------
# list_titles / list_active_titles
# ---------------------------------------------------------------------------


class TestListTitles:
    @pytest.mark.asyncio
    async def test_list_titles_no_query(self) -> None:
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalars_all=[])

        result = await list_titles(session, limit=10, offset=0)
        assert result == []

    @pytest.mark.asyncio
    async def test_list_titles_with_query(self) -> None:
        fake_title = MagicMock(title="Dune")
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalars_all=[fake_title])

        result = await list_titles(session, query="dune", limit=10, offset=0)
        assert len(result) == 1
        assert result[0].title == "Dune"

    @pytest.mark.asyncio
    async def test_list_active_titles(self) -> None:
        t1, t2 = MagicMock(title="A"), MagicMock(title="B")
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalars_all=[t1, t2])

        result = await list_active_titles(session, limit=100)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# get_mentions_for_title / get_mentions_count / get_latest_mention_collected_at
# ---------------------------------------------------------------------------


class TestMentionQueries:
    @pytest.mark.asyncio
    async def test_get_mentions_for_title_empty(self) -> None:
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalars_all=[])

        result = await get_mentions_for_title(
            session,
            title_id=uuid4(),
            platform="reddit",
            limit=10,
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_get_mentions_count(self) -> None:
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalar_one=42)

        count = await get_mentions_count(session, title_id=uuid4(), platform="reddit")
        assert count == 42

    @pytest.mark.asyncio
    async def test_get_latest_mention_collected_at(self) -> None:
        now = datetime.now(UTC)
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalar_one=now)

        result = await get_latest_mention_collected_at(
            session,
            title_id=uuid4(),
            platform="reddit",
        )
        assert result == now

    @pytest.mark.asyncio
    async def test_get_latest_mention_collected_at_none(self) -> None:
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = None
        session.execute.return_value = mock_result

        result = await get_latest_mention_collected_at(
            session,
            title_id=uuid4(),
            platform="youtube",
        )
        assert result is None


# ---------------------------------------------------------------------------
# get_mentions_in_window / get_mentions_in_window_lightweight
# ---------------------------------------------------------------------------


class TestMentionsWindow:
    @pytest.mark.asyncio
    async def test_window_returns_reversed(self) -> None:
        """Mentions should be returned in chronological order (oldest first)."""
        m1 = MagicMock(name="older")
        m2 = MagicMock(name="newer")
        session = _mock_session()
        # The query orders DESC, so we return [newer, older].
        session.execute.return_value = _mock_execute_result(scalars_all=[m2, m1])

        now = datetime.now(UTC)
        result = await get_mentions_in_window(
            session,
            title_id=uuid4(),
            window_start=now - timedelta(hours=1),
            window_end=now,
        )
        # After reverse: oldest first
        assert result == [m1, m2]

    @pytest.mark.asyncio
    async def test_window_empty(self) -> None:
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalars_all=[])

        now = datetime.now(UTC)
        result = await get_mentions_in_window(
            session,
            title_id=uuid4(),
            window_start=now - timedelta(hours=1),
            window_end=now,
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_lightweight_returns_projections(self) -> None:
        now = datetime.now(UTC)
        row = MagicMock(
            collected_at=now,
            platform="reddit",
            author="u1",
            source_type="post",
            metrics={"score": 1},
            sentiment={"compound": 0.5},
            content="hello",
        )
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(all_rows=[row])

        result = await get_mentions_in_window_lightweight(
            session,
            title_id=uuid4(),
            window_start=now - timedelta(hours=1),
            window_end=now,
        )
        assert len(result) == 1
        assert isinstance(result[0], MentionProjection)
        assert result[0].platform == "reddit"
        assert result[0].content == "hello"


# ---------------------------------------------------------------------------
# get_latest_metrics_snapshot / get_metrics_timeseries
# ---------------------------------------------------------------------------


class TestMetricsQueries:
    @pytest.mark.asyncio
    async def test_get_latest_snapshot_found(self) -> None:
        fake = MagicMock()
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(first=fake)

        result = await get_latest_metrics_snapshot(
            session,
            title_id=uuid4(),
            window_hours=1,
        )
        assert result is fake

    @pytest.mark.asyncio
    async def test_get_latest_snapshot_with_before(self) -> None:
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        session.execute.return_value = mock_result

        result = await get_latest_metrics_snapshot(
            session,
            title_id=uuid4(),
            window_hours=1,
            before=datetime.now(UTC),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_get_timeseries_empty(self) -> None:
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalars_all=[])

        result = await get_metrics_timeseries(
            session,
            title_id=uuid4(),
            window_hours=1,
            since=datetime.now(UTC) - timedelta(hours=24),
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_get_timeseries_with_until(self) -> None:
        snap = MagicMock()
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalars_all=[snap])

        now = datetime.now(UTC)
        result = await get_metrics_timeseries(
            session,
            title_id=uuid4(),
            window_hours=1,
            since=now - timedelta(hours=24),
            until=now,
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_trending_empty(self) -> None:
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(all_rows=[])

        result = await get_trending_by_attention_index(
            session,
            window_hours=24,
            limit=10,
        )
        assert result == []


# ---------------------------------------------------------------------------
# start_pipeline_run / finish_pipeline_run
# ---------------------------------------------------------------------------


class TestPipelineRun:
    @pytest.mark.asyncio
    async def test_start_pipeline_run(self) -> None:
        session = _mock_session()

        run = await start_pipeline_run(
            session,
            job_name="test_job",
            owner_id=uuid4(),
        )
        assert run.job_name == "test_job"
        assert run.status == "running"
        assert run.started_at is not None
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_pipeline_run_custom_time(self) -> None:
        session = _mock_session()
        ts = datetime(2025, 6, 1, tzinfo=UTC)

        run = await start_pipeline_run(
            session,
            job_name="job",
            owner_id=uuid4(),
            started_at=ts,
            stats={"titles": 5},
        )
        assert run.started_at == ts
        assert run.stats == {"titles": 5}

    @pytest.mark.asyncio
    async def test_finish_pipeline_run_found(self) -> None:
        fake_run = MagicMock()
        fake_run.stats = {"titles": 3}
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalar_one_or_none=fake_run)

        await finish_pipeline_run(
            session,
            run_id=uuid4(),
            status="success",
            stats={"mentions_inserted": 10},
        )
        assert fake_run.status == "success"
        assert fake_run.finished_at is not None
        assert fake_run.stats == {"titles": 3, "mentions_inserted": 10}

    @pytest.mark.asyncio
    async def test_finish_pipeline_run_not_found(self) -> None:
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalar_one_or_none=None)

        # Should not raise, just log a warning
        await finish_pipeline_run(
            session,
            run_id=uuid4(),
            status="failed",
            error="timeout",
        )

    @pytest.mark.asyncio
    async def test_finish_pipeline_run_no_stats(self) -> None:
        fake_run = MagicMock()
        fake_run.stats = None
        session = _mock_session()
        session.execute.return_value = _mock_execute_result(scalar_one_or_none=fake_run)

        await finish_pipeline_run(
            session,
            run_id=uuid4(),
            status="failed",
            error="oops",
        )
        assert fake_run.status == "failed"
        assert fake_run.error == "oops"

    @pytest.mark.asyncio
    async def test_force_release_lease_returns_rowcount(self) -> None:
        session = _mock_session()
        result = _mock_execute_result()
        result.rowcount = 1
        session.execute.return_value = result

        released = await force_release_lease(session, name="collector-cycle-lease")

        assert released == 1

    @pytest.mark.asyncio
    async def test_get_lease_returns_scalar_one_or_none(self) -> None:
        session = _mock_session()
        lease = MagicMock()
        session.execute.return_value = _mock_execute_result(scalar_one_or_none=lease)

        current = await get_lease(session, name="collector-cycle-lease")

        assert current is lease

    @pytest.mark.asyncio
    async def test_fail_running_pipeline_runs_returns_rowcount(self) -> None:
        session = _mock_session()
        result = _mock_execute_result()
        result.rowcount = 2
        session.execute.return_value = result

        failed = await fail_running_pipeline_runs(
            session,
            job_name="collector-cycle",
            error="stale run cleared by one-shot recovery",
        )

        assert failed == 2

    @pytest.mark.asyncio
    async def test_clear_stale_one_shot_state_refuses_active_lease(self) -> None:
        session = _mock_session()
        db_now = datetime.now(UTC)
        lease = MagicMock()
        lease.expires_at = db_now + timedelta(minutes=5)
        session.execute.side_effect = [
            _mock_execute_result(scalar_one=db_now),
            _mock_execute_result(scalar_one_or_none=lease),
        ]

        with pytest.raises(RuntimeError, match="active collector lease"):
            await clear_stale_one_shot_state(
                session,
                lease_name="collector-cycle-lease",
                job_name="collector-cycle",
                error="stale run cleared by one-shot recovery",
            )

    @pytest.mark.asyncio
    async def test_clear_stale_one_shot_state_clears_stale_owner_runs(self) -> None:
        session = _mock_session()
        db_now = datetime.now(UTC)
        stale_owner = uuid4()
        lease = MagicMock()
        lease.expires_at = db_now - timedelta(minutes=1)
        lease.owner_id = stale_owner
        delete_result = _mock_execute_result()
        delete_result.rowcount = 1
        fail_result = _mock_execute_result()
        fail_result.rowcount = 2
        session.execute.side_effect = [
            _mock_execute_result(scalar_one=db_now),
            _mock_execute_result(scalar_one_or_none=lease),
            delete_result,
            fail_result,
        ]

        released, failed_runs = await clear_stale_one_shot_state(
            session,
            lease_name="collector-cycle-lease",
            job_name="collector-cycle",
            error="stale run cleared by one-shot recovery",
        )

        assert released == 1
        assert failed_runs == 2

    @pytest.mark.asyncio
    async def test_clear_stale_one_shot_state_without_lease_only_fails_old_runs(self) -> None:
        session = _mock_session()
        db_now = datetime.now(UTC)
        no_lease_result = MagicMock()
        no_lease_result.scalar_one_or_none.return_value = None
        fail_result = _mock_execute_result()
        fail_result.rowcount = 1
        session.execute.side_effect = [
            _mock_execute_result(scalar_one=db_now),
            no_lease_result,
            fail_result,
        ]

        released, failed_runs = await clear_stale_one_shot_state(
            session,
            lease_name="collector-cycle-lease",
            job_name="collector-cycle",
            error="stale run cleared by one-shot recovery",
        )

        assert released == 0
        assert failed_runs == 1
