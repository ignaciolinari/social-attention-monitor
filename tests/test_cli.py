"""Tests for sam.cli module."""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sam import cli


class TestIso8601Parser:
    """Tests for _parse_iso8601 helper."""

    def test_parse_with_timezone(self) -> None:
        result = cli._parse_iso8601("2026-01-30T12:00:00+00:00")
        assert result.tzinfo is not None
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 30
        assert result.hour == 12

    def test_parse_without_timezone_adds_utc(self) -> None:
        result = cli._parse_iso8601("2026-01-30T12:00:00")
        assert result.tzinfo is UTC
        assert result.year == 2026

    def test_parse_with_offset(self) -> None:
        result = cli._parse_iso8601("2026-01-30T12:00:00-05:00")
        assert result.tzinfo is not None


class TestFloorToBucket:
    """Tests for _floor_to_bucket helper."""

    def test_floor_to_1_hour_bucket(self) -> None:
        dt = datetime(2026, 1, 30, 14, 35, 22, tzinfo=UTC)
        result = cli._floor_to_bucket(dt, bucket_hours=1)
        assert result.hour == 14
        assert result.minute == 0
        assert result.second == 0
        assert result.microsecond == 0

    def test_floor_to_6_hour_bucket(self) -> None:
        dt = datetime(2026, 1, 30, 14, 35, 22, tzinfo=UTC)
        result = cli._floor_to_bucket(dt, bucket_hours=6)
        assert result.hour == 12  # 14 // 6 * 6 = 12

    def test_floor_to_12_hour_bucket(self) -> None:
        dt = datetime(2026, 1, 30, 22, 0, 0, tzinfo=UTC)
        result = cli._floor_to_bucket(dt, bucket_hours=12)
        assert result.hour == 12


class TestMain:
    """Tests for main() entry point."""

    def test_main_prints_banner(self) -> None:
        with (
            patch("sam.cli.get_settings") as mock_settings,
            patch("sam.cli.setup_logging"),
            patch("sys.argv", ["sam"]),
            patch("sys.stdout", new_callable=StringIO) as mock_stdout,
        ):
            # Mock settings
            settings = MagicMock()
            settings.sam_env = "test"
            settings.demo_mode = True
            settings.reddit.is_configured = False
            settings.youtube.is_configured = False
            settings.tmdb.is_configured = False
            mock_settings.return_value = settings

            cli.main()

            output = mock_stdout.getvalue()
            assert "Social Attention Monitor" in output
            assert "Demo Mode" in output

    def test_main_demo_command(self) -> None:
        with (
            patch("sam.cli.get_settings") as mock_settings,
            patch("sam.cli.setup_logging"),
            patch("sam.cli.demo", new_callable=AsyncMock) as mock_demo,
            patch("sys.argv", ["sam", "demo"]),
            patch("sys.stdout", new_callable=StringIO),
        ):
            settings = MagicMock()
            settings.sam_env = "test"
            settings.demo_mode = True
            settings.reddit.is_configured = False
            settings.youtube.is_configured = False
            settings.tmdb.is_configured = False
            mock_settings.return_value = settings

            cli.main()

            mock_demo.assert_awaited_once()

    def test_main_recompute_metrics_command(self) -> None:
        with (
            patch("sam.cli.get_settings") as mock_settings,
            patch("sam.cli.setup_logging"),
            patch("sam.cli.recompute_metrics", new_callable=AsyncMock) as mock_recompute,
            patch(
                "sys.argv",
                [
                    "sam",
                    "recompute-metrics",
                    "--from",
                    "2026-01-30T00:00:00+00:00",
                    "--to",
                    "2026-01-31T00:00:00+00:00",
                ],
            ),
            patch("sys.stdout", new_callable=StringIO),
        ):
            settings = MagicMock()
            settings.sam_env = "test"
            settings.demo_mode = True
            settings.reddit.is_configured = False
            settings.youtube.is_configured = False
            settings.tmdb.is_configured = False
            mock_settings.return_value = settings

            cli.main()

            mock_recompute.assert_awaited_once()


class TestDemo:
    """Tests for demo() function."""

    @pytest.mark.asyncio
    async def test_demo_collects_from_all_sources(self) -> None:
        # These are imported locally in demo(), so patch at source
        with (
            patch("sam.cli.setup_logging"),
            patch("sam.collectors.tmdb.TMDBCollector") as mock_tmdb_cls,
            patch("sam.collectors.reddit.RedditCollector") as mock_reddit_cls,
            patch("sam.collectors.youtube.YouTubeCollector") as mock_youtube_cls,
            patch("sam.processors.sentiment.analyze_sentiment") as mock_sentiment,
            patch("sys.stdout", new_callable=StringIO),
        ):
            # Mock TMDB
            mock_tmdb = AsyncMock()
            mock_title = MagicMock()
            mock_title.title = "Test Movie"
            mock_title.media_type = "movie"
            mock_title.vote_average = 8.5
            mock_tmdb.get_trending.return_value = [mock_title]
            mock_tmdb_cls.return_value = mock_tmdb

            # Mock Reddit
            mock_reddit = AsyncMock()
            mock_post = MagicMock()
            mock_post.content = "Great movie!"
            mock_post.metrics = {"subreddit": "movies", "score": 100}
            mock_reddit_result = MagicMock()
            mock_reddit_result.posts = [mock_post]
            mock_reddit.collect.return_value = mock_reddit_result
            mock_reddit_cls.return_value = mock_reddit

            # Mock YouTube
            mock_youtube = AsyncMock()
            mock_video = MagicMock()
            mock_video.author = "MovieChannel"
            mock_video.metrics = {"view_count": 1000000}
            mock_yt_result = MagicMock()
            mock_yt_result.posts = [mock_video]
            mock_youtube.collect.return_value = mock_yt_result
            mock_youtube_cls.return_value = mock_youtube

            # Mock sentiment
            mock_sentiment_result = MagicMock()
            mock_sentiment_result.label = "positive"
            mock_sentiment.return_value = mock_sentiment_result

            await cli.demo()

            # Verify collectors were used
            mock_tmdb.get_trending.assert_called_once()
            mock_reddit.collect.assert_called_once()
            mock_youtube.collect.assert_called_once()

            # Verify cleanup
            mock_tmdb.close.assert_called_once()
            mock_youtube.close.assert_called_once()


class TestRecomputeMetrics:
    """Tests for recompute_metrics() function."""

    @pytest.mark.asyncio
    async def test_recompute_validates_date_range(self) -> None:
        with pytest.raises(ValueError, match="--to must be >= --from"):
            await cli.recompute_metrics(
                from_ts="2026-01-31T00:00:00+00:00",
                to_ts="2026-01-30T00:00:00+00:00",
                windows="1,24",
                bucket_hours=1,
            )

    @pytest.mark.asyncio
    async def test_recompute_validates_windows(self) -> None:
        with pytest.raises(ValueError, match="--windows must include at least one integer"):
            await cli.recompute_metrics(
                from_ts="2026-01-30T00:00:00+00:00",
                to_ts="2026-01-31T00:00:00+00:00",
                windows="",
                bucket_hours=1,
            )

    @pytest.mark.asyncio
    async def test_recompute_processes_titles(self) -> None:
        # These are imported locally in recompute_metrics(), so patch at source
        with (
            patch("sam.cli.setup_logging"),
            patch("sam.storage.database.get_session") as mock_session_ctx,
            patch("sam.storage.repository.list_active_titles") as mock_list,
            patch(
                "sam.pipeline.metrics_snapshots.compute_and_upsert_metrics_snapshots_multi",
                new_callable=AsyncMock,
            ) as mock_compute,
            patch("sys.stdout", new_callable=StringIO),
        ):
            # Mock session
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__.return_value = mock_session

            # Mock titles (return empty after first batch)
            import uuid

            mock_title = MagicMock()
            mock_title.id = uuid.uuid4()
            mock_list.side_effect = [[mock_title], []]

            await cli.recompute_metrics(
                from_ts="2026-01-30T00:00:00+00:00",
                to_ts="2026-01-30T01:00:00+00:00",
                windows="1",
                bucket_hours=1,
            )

            # Should compute for each hour bucket (00:00 and 01:00)
            assert mock_compute.call_count == 2
