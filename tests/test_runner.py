"""Tests for sam.scheduler.runner module."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sam.collectors.base import CommentCollectionResult
from sam.collectors.tmdb import TMDBTitle
from sam.pipeline import enrichment
from sam.processors.sentiment import SentimentBatchTranslationStats
from sam.scheduler import runner


class TestSnapshotBucket:
    """Tests for _snapshot_bucket helper."""

    def test_rounds_up_to_next_hour(self) -> None:
        dt = datetime(2026, 1, 30, 14, 35, 22, 123456, tzinfo=UTC)
        result = runner._snapshot_bucket(dt)
        # Rounds *up* so mentions collected at 14:35 fall inside the window.
        assert result.hour == 15
        assert result.minute == 0
        assert result.second == 0
        assert result.microsecond == 0

    def test_exact_hour_unchanged(self) -> None:
        dt = datetime(2026, 1, 30, 14, 0, 0, 0, tzinfo=UTC)
        result = runner._snapshot_bucket(dt)
        assert result == dt

    def test_preserves_date(self) -> None:
        dt = datetime(2026, 6, 15, 23, 59, 59, tzinfo=UTC)
        result = runner._snapshot_bucket(dt)
        # 23:59 rounds up to next day 00:00.
        assert result.year == 2026
        assert result.month == 6
        assert result.day == 16
        assert result.hour == 0

    def test_lower_half_rounds_to_30(self) -> None:
        """Minutes < 30 should round up to :30 of the same hour."""
        dt = datetime(2026, 3, 10, 14, 12, 45, 999, tzinfo=UTC)
        result = runner._snapshot_bucket(dt)
        assert result == datetime(2026, 3, 10, 14, 30, 0, 0, tzinfo=UTC)

    def test_exact_half_hour_unchanged(self) -> None:
        """Exactly on the :30 boundary should be kept as-is."""
        dt = datetime(2026, 3, 10, 14, 30, 0, 0, tzinfo=UTC)
        result = runner._snapshot_bucket(dt)
        assert result == dt


class TestCollectOnce:
    """Tests for collect_once() function."""

    @pytest.mark.asyncio
    async def test_collect_once_returns_stats(self) -> None:
        with (
            patch("sam.scheduler.runner.get_settings") as mock_settings,
            patch("sam.scheduler.runner.get_session") as mock_get_session,
            patch("sam.scheduler.runner.TMDBCollector") as mock_tmdb_cls,
            patch("sam.scheduler.runner.RedditCollector") as mock_reddit_cls,
            patch("sam.scheduler.runner.YouTubeCollector") as mock_youtube_cls,
            patch("sam.scheduler.runner.BlueskyCollector") as mock_bluesky_cls,
            patch("sam.scheduler.runner.upsert_title") as mock_upsert,
            patch("sam.scheduler.runner.insert_mentions") as mock_insert,
            patch("sam.scheduler.runner.analyze_texts_for_sentiment_with_stats") as mock_sentiment,
            patch(
                "sam.scheduler.runner.compute_and_upsert_metrics_snapshots_multi",
                new_callable=AsyncMock,
                return_value=2,
            ),
            patch("sam.scheduler.runner.persist_collection_result"),
            patch("sam.cache.collector_toggle_get", new_callable=AsyncMock, return_value=None),
        ):
            # Configure settings
            settings = MagicMock()
            settings.demo_mode = True
            settings.storage.enable_raw_data_storage = False
            mock_settings.return_value = settings

            # Mock get_session async context manager
            mock_session = AsyncMock()
            mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

            # Mock TMDB
            mock_tmdb = AsyncMock()
            mock_title = MagicMock()
            mock_title.title = "Test Movie"
            mock_tmdb.get_trending.return_value = [mock_title]
            mock_tmdb.close = AsyncMock()
            mock_tmdb_cls.return_value = mock_tmdb

            # Mock Reddit
            mock_reddit = AsyncMock()
            mock_reddit.is_configured = True
            mock_post = MagicMock()
            mock_post.source_id = "abc123"
            mock_post.content = "Great!"
            mock_reddit_result = MagicMock()
            mock_reddit_result.success = True
            mock_reddit_result.posts = [mock_post]
            mock_reddit.collect.return_value = mock_reddit_result
            mock_reddit.close = AsyncMock()
            mock_reddit_cls.return_value = mock_reddit

            # Mock YouTube
            mock_youtube = AsyncMock()
            mock_video = MagicMock()
            mock_video.source_id = "xyz789"
            mock_video.content = "Awesome!"
            mock_yt_result = MagicMock()
            mock_yt_result.success = True
            mock_yt_result.posts = [mock_video]
            mock_youtube.collect.return_value = mock_yt_result
            mock_youtube.close = AsyncMock()
            mock_youtube_cls.return_value = mock_youtube

            # Mock Bluesky
            mock_bluesky = AsyncMock()
            mock_bsky_post = MagicMock()
            mock_bsky_post.source_id = "bsky456"
            mock_bsky_post.content = "Cool!"
            mock_bsky_result = MagicMock()
            mock_bsky_result.success = True
            mock_bsky_result.posts = [mock_bsky_post]
            mock_bluesky.collect.return_value = mock_bsky_result
            mock_bluesky.close = AsyncMock()
            mock_bluesky_cls.return_value = mock_bluesky

            # Mock DB title
            mock_db_title = MagicMock()
            mock_db_title.id = uuid.uuid4()
            mock_upsert.return_value = mock_db_title

            # Mock sentiment -- return one result per post.
            # analyze_texts_for_sentiment_with_stats is called separately for
            # each platform batch.
            mock_sentiment_result = MagicMock()
            mock_sentiment_result.compound = 0.5
            mock_sentiment_result.positive = 0.7
            mock_sentiment_result.negative = 0.1
            mock_sentiment_result.neutral = 0.2
            mock_sentiment_result.label = "positive"
            mock_sentiment_result.model = "vader"

            # Side effect: each call returns one result per input text + stats.
            def _fake_batch_sentiment(texts, *, translate, log_context="runner"):
                _ = (translate, log_context)
                return [mock_sentiment_result] * len(
                    texts
                ), SentimentBatchTranslationStats().to_dict()

            mock_sentiment.side_effect = _fake_batch_sentiment

            # Mock insert returns count
            mock_insert.return_value = 1

            # Run
            stats = await runner.collect_once(
                limit_titles=1,
                limit_reddit=5,
                limit_youtube=5,
                limit_bluesky=5,
            )

            assert stats["titles"] == 1
            assert stats["reddit_mentions_inserted"] == 1
            assert stats["youtube_mentions_inserted"] == 1
            assert stats["bluesky_mentions_inserted"] == 1
            assert stats["metrics_snapshots_upserted"] == 2  # 1-hour and 24-hour

    @pytest.mark.asyncio
    async def test_collect_once_handles_empty_results(self) -> None:
        with (
            patch("sam.scheduler.runner.get_settings") as mock_settings,
            patch("sam.scheduler.runner.get_session") as mock_get_session,
            patch("sam.scheduler.runner.TMDBCollector") as mock_tmdb_cls,
            patch("sam.scheduler.runner.RedditCollector") as mock_reddit_cls,
            patch("sam.scheduler.runner.YouTubeCollector") as mock_youtube_cls,
            patch("sam.scheduler.runner.BlueskyCollector") as mock_bluesky_cls,
            patch("sam.scheduler.runner.upsert_title"),
            patch(
                "sam.scheduler.runner.compute_and_upsert_metrics_snapshots_multi",
                new_callable=AsyncMock,
                return_value=2,
            ),
            patch("sam.cache.collector_toggle_get", new_callable=AsyncMock, return_value=None),
        ):
            settings = MagicMock()
            settings.demo_mode = True
            settings.storage.enable_raw_data_storage = False
            mock_settings.return_value = settings

            # Mock get_session async context manager
            mock_session = AsyncMock()
            mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

            # Empty trending
            mock_tmdb = AsyncMock()
            mock_tmdb.get_trending.return_value = []
            mock_tmdb.close = AsyncMock()
            mock_tmdb_cls.return_value = mock_tmdb

            mock_reddit = AsyncMock()
            mock_reddit.is_configured = True
            mock_reddit.close = AsyncMock()
            mock_reddit_cls.return_value = mock_reddit

            mock_youtube = AsyncMock()
            mock_youtube.close = AsyncMock()
            mock_youtube_cls.return_value = mock_youtube

            mock_bluesky = AsyncMock()
            mock_bluesky.close = AsyncMock()
            mock_bluesky_cls.return_value = mock_bluesky

            stats = await runner.collect_once(
                limit_titles=10,
                limit_reddit=10,
                limit_youtube=10,
                limit_bluesky=10,
            )

            assert stats["titles"] == 0
            assert stats["reddit_mentions_inserted"] == 0
            assert stats["youtube_mentions_inserted"] == 0
            assert stats["bluesky_mentions_inserted"] == 0

    @pytest.mark.asyncio
    async def test_collect_once_with_raw_storage_enabled(self) -> None:
        with (
            patch("sam.scheduler.runner.get_settings") as mock_settings,
            patch("sam.scheduler.runner.get_session") as mock_get_session,
            patch("sam.scheduler.runner.TMDBCollector") as mock_tmdb_cls,
            patch("sam.scheduler.runner.RedditCollector") as mock_reddit_cls,
            patch("sam.scheduler.runner.YouTubeCollector") as mock_youtube_cls,
            patch("sam.scheduler.runner.BlueskyCollector") as mock_bluesky_cls,
            patch("sam.scheduler.runner.upsert_title") as mock_upsert,
            patch("sam.scheduler.runner.insert_mentions") as mock_insert,
            patch("sam.scheduler.runner.analyze_texts_for_sentiment_with_stats") as mock_sentiment,
            patch(
                "sam.scheduler.runner.compute_and_upsert_metrics_snapshots_multi",
                new_callable=AsyncMock,
                return_value=2,
            ),
            patch("sam.scheduler.runner.persist_collection_result") as mock_persist,
            patch("sam.cache.collector_toggle_get", new_callable=AsyncMock, return_value=None),
        ):
            settings = MagicMock()
            settings.demo_mode = True
            settings.storage.enable_raw_data_storage = True
            settings.storage.raw_data_dir = "/tmp/raw"
            mock_settings.return_value = settings

            # Mock get_session async context manager
            mock_session = AsyncMock()
            mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_tmdb = AsyncMock()
            mock_title = MagicMock()
            mock_title.title = "Test"
            mock_tmdb.get_trending.return_value = [mock_title]
            mock_tmdb.close = AsyncMock()
            mock_tmdb_cls.return_value = mock_tmdb

            mock_reddit = AsyncMock()
            mock_reddit.is_configured = True
            mock_post = MagicMock()
            mock_post.source_id = "r1"
            mock_post.content = "text"
            mock_reddit_result = MagicMock()
            mock_reddit_result.success = True
            mock_reddit_result.posts = [mock_post]
            mock_reddit.collect.return_value = mock_reddit_result
            mock_reddit.close = AsyncMock()
            mock_reddit_cls.return_value = mock_reddit

            mock_youtube = AsyncMock()
            mock_yt_result = MagicMock()
            mock_yt_result.success = False
            mock_yt_result.posts = []
            mock_youtube.collect.return_value = mock_yt_result
            mock_youtube.close = AsyncMock()
            mock_youtube_cls.return_value = mock_youtube

            mock_bluesky = AsyncMock()
            mock_bsky_result = MagicMock()
            mock_bsky_result.success = False
            mock_bsky_result.posts = []
            mock_bluesky.collect.return_value = mock_bsky_result
            mock_bluesky.close = AsyncMock()
            mock_bluesky_cls.return_value = mock_bluesky

            mock_db_title = MagicMock()
            mock_db_title.id = uuid.uuid4()
            mock_upsert.return_value = mock_db_title

            mock_sentiment_result = MagicMock()
            mock_sentiment_result.compound = 0.0
            mock_sentiment_result.positive = 0.3
            mock_sentiment_result.negative = 0.3
            mock_sentiment_result.neutral = 0.4
            mock_sentiment_result.label = "neutral"
            mock_sentiment_result.model = "vader"

            def _fake_batch_sentiment(texts, *, translate, log_context="runner"):
                _ = (translate, log_context)
                return [mock_sentiment_result] * len(
                    texts
                ), SentimentBatchTranslationStats().to_dict()

            mock_sentiment.side_effect = _fake_batch_sentiment

            mock_insert.return_value = 1

            await runner.collect_once(
                limit_titles=1,
                limit_reddit=1,
                limit_youtube=1,
                limit_bluesky=1,
            )

            # Should have persisted raw Reddit data
            mock_persist.assert_called_once()

    @pytest.mark.asyncio
    async def test_collect_once_includes_watchlist_only_titles(self) -> None:
        with (
            patch("sam.scheduler.runner.get_settings") as mock_settings,
            patch("sam.scheduler.runner.get_session") as mock_get_session,
            patch("sam.scheduler.runner.TMDBCollector") as mock_tmdb_cls,
            patch("sam.scheduler.runner.RedditCollector") as mock_reddit_cls,
            patch("sam.scheduler.runner.YouTubeCollector") as mock_youtube_cls,
            patch("sam.scheduler.runner.BlueskyCollector") as mock_bluesky_cls,
            patch(
                "sam.scheduler.runner.get_all_watchlist_tmdb_ids", new_callable=AsyncMock
            ) as mock_watchlists,
            patch("sam.scheduler.runner.upsert_title") as mock_upsert,
            patch("sam.scheduler.runner.insert_mentions") as mock_insert,
            patch("sam.scheduler.runner.analyze_texts_for_sentiment_with_stats") as mock_sentiment,
            patch(
                "sam.scheduler.runner.compute_and_upsert_metrics_snapshots_multi",
                new_callable=AsyncMock,
                return_value=2,
            ),
            patch("sam.scheduler.runner.persist_collection_result"),
            patch("sam.cache.collector_toggle_get", new_callable=AsyncMock, return_value=None),
        ):
            settings = MagicMock()
            settings.demo_mode = True
            settings.storage.enable_raw_data_storage = False
            mock_settings.return_value = settings

            mock_session = AsyncMock()
            mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

            trending_title = TMDBTitle(
                tmdb_id=1,
                title="Trending Title",
                original_title="Trending Title",
                media_type="movie",
                release_date=datetime(2025, 1, 1, tzinfo=UTC),
                overview="",
                poster_path=None,
                backdrop_path=None,
                popularity=1.0,
                vote_average=7.0,
                vote_count=10,
                genres=[],
                original_language="en",
                revenue=None,
                budget=None,
                raw_data={},
            )
            watchlist_title = TMDBTitle(
                tmdb_id=2,
                title="Watchlist Title",
                original_title="Watchlist Title",
                media_type="movie",
                release_date=datetime(2025, 1, 2, tzinfo=UTC),
                overview="",
                poster_path=None,
                backdrop_path=None,
                popularity=2.0,
                vote_average=7.5,
                vote_count=20,
                genres=[],
                original_language="en",
                revenue=None,
                budget=None,
                raw_data={},
            )

            mock_tmdb = AsyncMock()
            mock_tmdb.get_trending.return_value = [trending_title]
            mock_tmdb.get_details.return_value = watchlist_title
            mock_tmdb.close = AsyncMock()
            mock_tmdb_cls.return_value = mock_tmdb

            mock_watchlists.return_value = {2}

            mock_reddit = AsyncMock()
            mock_reddit.collect.return_value = MagicMock(success=False, posts=[])
            mock_reddit.close = AsyncMock()
            mock_reddit_cls.return_value = mock_reddit

            mock_youtube = AsyncMock()
            mock_youtube.collect.return_value = MagicMock(success=False, posts=[])
            mock_youtube.close = AsyncMock()
            mock_youtube_cls.return_value = mock_youtube

            mock_bluesky = AsyncMock()
            mock_bluesky.collect.return_value = MagicMock(success=False, posts=[])
            mock_bluesky.close = AsyncMock()
            mock_bluesky_cls.return_value = mock_bluesky

            mock_db_title = MagicMock()
            mock_db_title.id = uuid.uuid4()
            mock_upsert.return_value = mock_db_title

            sentiment = MagicMock()
            sentiment.to_dict.return_value = {
                "compound": 0.1,
                "label": "positive",
                "model": "vader",
            }

            def _sentiment_side_effect(texts, *, translate, log_context="runner"):
                _ = (translate, log_context)
                return [sentiment] * len(texts), SentimentBatchTranslationStats().to_dict()

            mock_sentiment.side_effect = _sentiment_side_effect
            mock_insert.return_value = 0

            stats = await runner.collect_once(
                limit_titles=1,
                limit_reddit=1,
                limit_youtube=1,
                limit_bluesky=1,
            )

            assert stats["titles"] == 2
            assert mock_upsert.call_count == 2
            mock_tmdb.get_details.assert_awaited_once_with(
                2,
                media_type="movie",
                suppress_not_found_error=True,
            )

    @pytest.mark.asyncio
    async def test_collect_once_retries_watchlist_title_as_tv_after_movie_miss(self) -> None:
        with (
            patch("sam.scheduler.runner.get_settings") as mock_settings,
            patch("sam.scheduler.runner.get_session") as mock_get_session,
            patch("sam.scheduler.runner.TMDBCollector") as mock_tmdb_cls,
            patch("sam.scheduler.runner.RedditCollector") as mock_reddit_cls,
            patch("sam.scheduler.runner.YouTubeCollector") as mock_youtube_cls,
            patch("sam.scheduler.runner.BlueskyCollector") as mock_bluesky_cls,
            patch(
                "sam.scheduler.runner.get_all_watchlist_tmdb_ids", new_callable=AsyncMock
            ) as mock_watchlists,
            patch("sam.scheduler.runner.upsert_title") as mock_upsert,
            patch("sam.scheduler.runner.insert_mentions") as mock_insert,
            patch("sam.scheduler.runner.analyze_texts_for_sentiment_with_stats") as mock_sentiment,
            patch(
                "sam.scheduler.runner.compute_and_upsert_metrics_snapshots_multi",
                new_callable=AsyncMock,
                return_value=2,
            ),
            patch("sam.scheduler.runner.persist_collection_result"),
            patch("sam.cache.collector_toggle_get", new_callable=AsyncMock, return_value=None),
        ):
            settings = MagicMock()
            settings.demo_mode = True
            settings.storage.enable_raw_data_storage = False
            mock_settings.return_value = settings

            mock_session = AsyncMock()
            mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

            trending_title = TMDBTitle(
                tmdb_id=1,
                title="Trending Title",
                original_title="Trending Title",
                media_type="movie",
                release_date=datetime(2025, 1, 1, tzinfo=UTC),
                overview="",
                poster_path=None,
                backdrop_path=None,
                popularity=1.0,
                vote_average=7.0,
                vote_count=10,
                genres=[],
                original_language="en",
                revenue=None,
                budget=None,
                raw_data={},
            )
            watchlist_title = TMDBTitle(
                tmdb_id=2,
                title="Watchlist Show",
                original_title="Watchlist Show",
                media_type="tv",
                release_date=datetime(2025, 1, 2, tzinfo=UTC),
                overview="",
                poster_path=None,
                backdrop_path=None,
                popularity=2.0,
                vote_average=7.5,
                vote_count=20,
                genres=[],
                original_language="en",
                revenue=None,
                budget=None,
                raw_data={},
            )

            mock_tmdb = AsyncMock()
            mock_tmdb.get_trending.return_value = [trending_title]
            mock_tmdb.get_details.side_effect = [None, watchlist_title]
            mock_tmdb.close = AsyncMock()
            mock_tmdb_cls.return_value = mock_tmdb

            mock_watchlists.return_value = {2}

            mock_reddit = AsyncMock()
            mock_reddit.collect.return_value = MagicMock(success=False, posts=[])
            mock_reddit.close = AsyncMock()
            mock_reddit_cls.return_value = mock_reddit

            mock_youtube = AsyncMock()
            mock_youtube.collect.return_value = MagicMock(success=False, posts=[])
            mock_youtube.close = AsyncMock()
            mock_youtube_cls.return_value = mock_youtube

            mock_bluesky = AsyncMock()
            mock_bluesky.collect.return_value = MagicMock(success=False, posts=[])
            mock_bluesky.close = AsyncMock()
            mock_bluesky_cls.return_value = mock_bluesky

            mock_db_title = MagicMock()
            mock_db_title.id = uuid.uuid4()
            mock_upsert.return_value = mock_db_title

            sentiment = MagicMock()
            sentiment.to_dict.return_value = {
                "compound": 0.1,
                "label": "positive",
                "model": "vader",
            }

            def _sentiment_side_effect(texts, *, translate, log_context="runner"):
                _ = (translate, log_context)
                return [sentiment] * len(texts), SentimentBatchTranslationStats().to_dict()

            mock_sentiment.side_effect = _sentiment_side_effect
            mock_insert.return_value = 0

            stats = await runner.collect_once(
                limit_titles=1,
                limit_reddit=1,
                limit_youtube=1,
                limit_bluesky=1,
            )

            assert stats["titles"] == 2
            assert mock_upsert.call_count == 2
            assert mock_tmdb.get_details.await_args_list == [
                ((2,), {"media_type": "movie", "suppress_not_found_error": True}),
                ((2,), {"media_type": "tv", "suppress_not_found_error": False}),
            ]

    @pytest.mark.asyncio
    async def test_collect_once_continues_when_reddit_raises(self) -> None:
        with (
            patch("sam.scheduler.runner.get_settings") as mock_settings,
            patch("sam.scheduler.runner.get_session") as mock_get_session,
            patch("sam.scheduler.runner.TMDBCollector") as mock_tmdb_cls,
            patch("sam.scheduler.runner.RedditCollector") as mock_reddit_cls,
            patch("sam.scheduler.runner.YouTubeCollector") as mock_youtube_cls,
            patch("sam.scheduler.runner.BlueskyCollector") as mock_bluesky_cls,
            patch("sam.scheduler.runner.upsert_title") as mock_upsert,
            patch("sam.scheduler.runner.insert_mentions") as mock_insert,
            patch("sam.scheduler.runner.analyze_texts_for_sentiment_with_stats") as mock_sentiment,
            patch(
                "sam.scheduler.runner.compute_and_upsert_metrics_snapshots_multi",
                new_callable=AsyncMock,
                return_value=2,
            ),
            patch("sam.scheduler.runner.persist_collection_result"),
            patch("sam.cache.collector_toggle_get", new_callable=AsyncMock, return_value=None),
        ):
            settings = MagicMock()
            settings.demo_mode = True
            settings.storage.enable_raw_data_storage = False
            mock_settings.return_value = settings

            mock_session = AsyncMock()
            mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_tmdb = AsyncMock()
            mock_title = MagicMock()
            mock_title.title = "Test Movie"
            mock_tmdb.get_trending.return_value = [mock_title]
            mock_tmdb.close = AsyncMock()
            mock_tmdb_cls.return_value = mock_tmdb

            mock_reddit = AsyncMock()
            mock_reddit.collect.side_effect = RuntimeError("reddit boom")
            mock_reddit.close = AsyncMock()
            mock_reddit_cls.return_value = mock_reddit

            mock_youtube = AsyncMock()
            mock_video = MagicMock()
            mock_video.source_id = "yt1"
            mock_video.content = "Awesome"
            mock_yt_result = MagicMock()
            mock_yt_result.success = True
            mock_yt_result.posts = [mock_video]
            mock_yt_result.collected_at = datetime.now(UTC)
            mock_youtube.collect.return_value = mock_yt_result
            mock_youtube.close = AsyncMock()
            mock_youtube_cls.return_value = mock_youtube

            mock_bluesky = AsyncMock()
            mock_bsky_result = MagicMock()
            mock_bsky_result.success = False
            mock_bsky_result.posts = []
            mock_bluesky.collect.return_value = mock_bsky_result
            mock_bluesky.close = AsyncMock()
            mock_bluesky_cls.return_value = mock_bluesky

            mock_db_title = MagicMock()
            mock_db_title.id = uuid.uuid4()
            mock_upsert.return_value = mock_db_title

            mock_sentiment_result = MagicMock()
            mock_sentiment_result.to_dict.return_value = {
                "compound": 0.2,
                "label": "positive",
                "model": "vader",
            }

            def _fake_batch_sentiment(texts, *, translate, log_context="runner"):
                _ = (translate, log_context)
                return [mock_sentiment_result] * len(
                    texts
                ), SentimentBatchTranslationStats().to_dict()

            mock_sentiment.side_effect = _fake_batch_sentiment
            mock_insert.return_value = 1

            stats = await runner.collect_once(
                limit_titles=1,
                limit_reddit=1,
                limit_youtube=1,
                limit_bluesky=1,
            )

            # Reddit error should not abort the title; YouTube path still persists.
            assert stats["titles"] == 1
            assert stats["youtube_mentions_inserted"] == 1

    @pytest.mark.asyncio
    async def test_collect_once_tracks_language_detection_failures(self) -> None:
        with (
            patch("sam.scheduler.runner.get_settings") as mock_settings,
            patch("sam.scheduler.runner.get_session") as mock_get_session,
            patch("sam.scheduler.runner.TMDBCollector") as mock_tmdb_cls,
            patch("sam.scheduler.runner.RedditCollector") as mock_reddit_cls,
            patch("sam.scheduler.runner.YouTubeCollector") as mock_youtube_cls,
            patch("sam.scheduler.runner.BlueskyCollector") as mock_bluesky_cls,
            patch("sam.scheduler.runner.upsert_title") as mock_upsert,
            patch("sam.scheduler.runner.insert_mentions", new_callable=AsyncMock, return_value=1),
            patch("sam.scheduler.runner.analyze_texts_for_sentiment_with_stats") as mock_sentiment,
            patch("sam.scheduler.runner.detect_languages", side_effect=RuntimeError("lang boom")),
            patch(
                "sam.scheduler.runner.compute_and_upsert_metrics_snapshots_multi",
                new_callable=AsyncMock,
                return_value=2,
            ),
            patch("sam.scheduler.runner.persist_collection_result"),
            patch("sam.cache.collector_toggle_get", new_callable=AsyncMock, return_value=None),
        ):
            settings = MagicMock()
            settings.demo_mode = True
            settings.storage.enable_raw_data_storage = False
            mock_settings.return_value = settings

            mock_session = AsyncMock()
            mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_title = MagicMock()
            mock_title.title = "Test Movie"
            mock_tmdb = AsyncMock()
            mock_tmdb.get_trending.return_value = [mock_title]
            mock_tmdb.close = AsyncMock()
            mock_tmdb_cls.return_value = mock_tmdb

            post = MagicMock()
            post.source_id = "r1"
            post.source_type = "post"
            post.platform = "reddit"
            post.content = "A long enough post body to trigger detection"
            post.created_at = datetime.now(UTC)
            post.author = "u"
            post.url = None
            post.metrics = {}

            reddit_result = MagicMock()
            reddit_result.success = True
            reddit_result.posts = [post]
            reddit_result.collected_at = datetime.now(UTC)

            mock_reddit = AsyncMock()
            mock_reddit.collect.return_value = reddit_result
            mock_reddit.close = AsyncMock()
            mock_reddit_cls.return_value = mock_reddit

            mock_youtube = AsyncMock()
            mock_youtube.collect.return_value = MagicMock(success=False, posts=[])
            mock_youtube.close = AsyncMock()
            mock_youtube_cls.return_value = mock_youtube

            mock_bluesky = AsyncMock()
            mock_bluesky.collect.return_value = MagicMock(success=False, posts=[])
            mock_bluesky.close = AsyncMock()
            mock_bluesky_cls.return_value = mock_bluesky

            mock_db_title = MagicMock()
            mock_db_title.id = uuid.uuid4()
            mock_upsert.return_value = mock_db_title

            sentiment = MagicMock()
            sentiment.to_dict.return_value = {
                "compound": 0.1,
                "label": "positive",
                "model": "vader",
            }

            def _sentiment_side_effect(texts, *, translate, log_context="runner"):
                _ = (translate, log_context)
                return [sentiment] * len(texts), SentimentBatchTranslationStats().to_dict()

            mock_sentiment.side_effect = _sentiment_side_effect

            stats = await runner.collect_once(
                limit_titles=1,
                limit_reddit=1,
                limit_youtube=1,
                limit_bluesky=1,
            )

            assert stats["languages_detection_failures"] == 1

    @pytest.mark.asyncio
    async def test_collect_once_tracks_watchlist_fetch_failures(self) -> None:
        with (
            patch("sam.scheduler.runner.get_settings") as mock_settings,
            patch("sam.scheduler.runner.get_session") as mock_get_session,
            patch("sam.scheduler.runner.TMDBCollector") as mock_tmdb_cls,
            patch("sam.scheduler.runner.RedditCollector") as mock_reddit_cls,
            patch("sam.scheduler.runner.YouTubeCollector") as mock_youtube_cls,
            patch("sam.scheduler.runner.BlueskyCollector") as mock_bluesky_cls,
            patch(
                "sam.scheduler.runner.get_all_watchlist_tmdb_ids", new_callable=AsyncMock
            ) as mock_watchlists,
            patch("sam.scheduler.runner.upsert_title") as mock_upsert,
            patch("sam.scheduler.runner.insert_mentions", new_callable=AsyncMock, return_value=0),
            patch("sam.scheduler.runner.analyze_texts_for_sentiment_with_stats") as mock_sentiment,
            patch(
                "sam.scheduler.runner.compute_and_upsert_metrics_snapshots_multi",
                new_callable=AsyncMock,
                return_value=2,
            ),
            patch("sam.scheduler.runner.persist_collection_result"),
            patch("sam.cache.collector_toggle_get", new_callable=AsyncMock, return_value=None),
        ):
            settings = MagicMock()
            settings.demo_mode = True
            settings.storage.enable_raw_data_storage = False
            mock_settings.return_value = settings

            mock_session = AsyncMock()
            mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_session.return_value.__aexit__ = AsyncMock(return_value=False)

            trending_title = TMDBTitle(
                tmdb_id=1,
                title="Trending Title",
                original_title="Trending Title",
                media_type="movie",
                release_date=datetime(2025, 1, 1, tzinfo=UTC),
                overview="",
                poster_path=None,
                backdrop_path=None,
                popularity=1.0,
                vote_average=7.0,
                vote_count=10,
                genres=[],
                original_language="en",
                revenue=None,
                budget=None,
                raw_data={},
            )

            mock_tmdb = AsyncMock()
            mock_tmdb.get_trending.return_value = [trending_title]
            mock_tmdb.get_details.side_effect = RuntimeError("tmdb transient error")
            mock_tmdb.close = AsyncMock()
            mock_tmdb_cls.return_value = mock_tmdb

            mock_watchlists.return_value = {2}

            mock_reddit = AsyncMock()
            mock_reddit.collect.return_value = MagicMock(success=False, posts=[])
            mock_reddit.close = AsyncMock()
            mock_reddit_cls.return_value = mock_reddit

            mock_youtube = AsyncMock()
            mock_youtube.collect.return_value = MagicMock(success=False, posts=[])
            mock_youtube.close = AsyncMock()
            mock_youtube_cls.return_value = mock_youtube

            mock_bluesky = AsyncMock()
            mock_bluesky.collect.return_value = MagicMock(success=False, posts=[])
            mock_bluesky.close = AsyncMock()
            mock_bluesky_cls.return_value = mock_bluesky

            mock_db_title = MagicMock()
            mock_db_title.id = uuid.uuid4()
            mock_upsert.return_value = mock_db_title

            def _sentiment_side_effect(texts, *, translate, log_context="runner"):
                _ = (texts, translate, log_context)
                return [], SentimentBatchTranslationStats().to_dict()

            mock_sentiment.side_effect = _sentiment_side_effect

            stats = await runner.collect_once(
                limit_titles=1,
                limit_reddit=1,
                limit_youtube=1,
                limit_bluesky=1,
            )

            assert stats["watchlist_titles_failed"] == 1
            assert stats["watchlist_titles_not_found"] == 0


class TestYouTubeCommentsParallel:
    """Tests for _collect_youtube_comments_parallel helper."""

    @pytest.mark.asyncio
    async def test_comment_prefilter_uses_projected_quota_cost(self) -> None:
        youtube = AsyncMock()
        youtube.collect_comments.return_value = CommentCollectionResult(comments=[])

        class _Quota:
            def __init__(self) -> None:
                self.costs: list[int] = []

            def youtube_has_budget(self, cost: int) -> bool:
                self.costs.append(cost)
                return cost <= 2

        quota = _Quota()
        posts = []
        for idx in range(5):
            p = MagicMock()
            p.source_id = f"vid{idx}"
            posts.append(p)

        comments, spam_count, failures = await runner._collect_youtube_comments_parallel(
            youtube=youtube,
            posts=posts,
            limit=10,
            enable_spam_filter=False,
            quota=quota,
            demo_mode=False,
            max_concurrent=5,
        )

        assert comments == []
        assert spam_count == 0
        assert failures == 0
        assert quota.costs == [1, 2, 3]
        assert youtube.collect_comments.await_count == 2


class TestCollectionJob:
    """Tests for _collection_job() function."""

    @pytest.mark.asyncio
    async def test_skips_when_lease_not_acquired(self) -> None:
        with (
            patch("sam.scheduler.runner.get_session") as mock_session_ctx,
            patch("sam.scheduler.runner.acquire_lease") as mock_acquire,
            patch("sam.scheduler.runner.collect_once") as mock_collect,
        ):
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__.return_value = mock_session
            mock_acquire.return_value = False

            await runner._collection_job(
                owner_id=uuid.uuid4(),
                interval_minutes=5,
                limit_titles=10,
                limit_reddit=10,
                limit_youtube=10,
                limit_bluesky=10,
            )

            mock_acquire.assert_called_once()
            # collect_once should NOT have been called when lease was not acquired
            mock_collect.assert_not_called()


class TestEnrichmentHelpers:
    """Tests for enrichment helper wiring."""

    @pytest.mark.asyncio
    async def test_build_enriched_sentiment_map_calls_enrichment_when_enabled(self) -> None:
        settings = MagicMock()
        settings.enable_emotion_detection = True
        settings.enable_sarcasm_detection = False
        settings.enable_aspect_sentiment = False

        post = MagicMock()
        post.source_id = "sid1"
        post.content = "Great movie"

        sentiment = MagicMock()
        sentiment.to_dict.return_value = {
            "compound": 0.5,
            "label": "positive",
            "model": "vader",
        }

        with patch("sam.pipeline.enrichment.enrich_sentiments_with_nlp") as mock_enrich:
            result = await enrichment.build_enriched_sentiment_map([post], [sentiment], settings)

        assert "sid1" in result
        mock_enrich.assert_called_once()

    @pytest.mark.asyncio
    async def test_build_enriched_sentiment_map_calls_enrichment_when_sarcasm_enabled(self) -> None:
        settings = MagicMock()
        settings.enable_emotion_detection = False
        settings.enable_sarcasm_detection = True
        settings.enable_aspect_sentiment = False

        post = MagicMock()
        post.source_id = "sid1"
        post.content = "Great movie"

        sentiment = MagicMock()
        sentiment.to_dict.return_value = {
            "compound": 0.5,
            "label": "positive",
            "model": "vader",
        }

        with patch("sam.pipeline.enrichment.enrich_sentiments_with_nlp") as mock_enrich:
            result = await enrichment.build_enriched_sentiment_map([post], [sentiment], settings)

        assert "sid1" in result
        mock_enrich.assert_called_once()

    @pytest.mark.asyncio
    async def test_build_enriched_sentiment_map_skips_enrichment_when_disabled(self) -> None:
        settings = MagicMock()
        settings.enable_emotion_detection = False
        settings.enable_sarcasm_detection = False
        settings.enable_aspect_sentiment = False

        post = MagicMock()
        post.source_id = "sid1"
        post.content = "Great movie"

        sentiment = MagicMock()
        sentiment.to_dict.return_value = {
            "compound": 0.5,
            "label": "positive",
            "model": "vader",
        }

        with patch("sam.pipeline.enrichment.enrich_sentiments_with_nlp") as mock_enrich:
            result = await enrichment.build_enriched_sentiment_map([post], [sentiment], settings)

        assert "sid1" in result
        mock_enrich.assert_not_called()

    def test_enrich_sentiments_applies_sarcasm_flag(self) -> None:
        settings = MagicMock()
        settings.enable_emotion_detection = False
        settings.enable_sarcasm_detection = True
        settings.enable_aspect_sentiment = False

        sentiment_map: dict[str, dict[str, object]] = {"sid1": {"compound": 0.2}}
        texts = ["Totally not bad at all 🙃"]
        source_ids = ["sid1"]

        detector = MagicMock()
        detector.detect_batch.return_value = [(True, 0.9132)]

        with patch("sam.processors.sarcasm.get_sarcasm_detector", return_value=detector):
            enrichment.enrich_sentiments_with_nlp(texts, sentiment_map, source_ids, settings)

        assert sentiment_map["sid1"]["is_sarcastic"] is True
        assert sentiment_map["sid1"]["sarcasm_confidence"] == 0.9132
        assert "emotions" not in sentiment_map["sid1"]

    def test_enrich_sentiments_applies_emotions(self) -> None:
        settings = MagicMock()
        settings.enable_emotion_detection = True
        settings.enable_sarcasm_detection = False
        settings.enable_aspect_sentiment = False

        sentiment_map: dict[str, dict[str, object]] = {"sid1": {"compound": 0.8}}
        texts = ["This is amazing"]
        source_ids = ["sid1"]

        detector = MagicMock()
        detector.detect_batch.return_value = [{"joy": 0.98123, "neutral": 0.01877}]

        with patch("sam.processors.emotions.get_emotion_detector", return_value=detector):
            enrichment.enrich_sentiments_with_nlp(texts, sentiment_map, source_ids, settings)

        assert sentiment_map["sid1"]["emotions"] == {"joy": 0.9812, "neutral": 0.0188}
        assert "is_sarcastic" not in sentiment_map["sid1"]

    @pytest.mark.asyncio
    async def test_runs_collection_when_lease_acquired(self) -> None:
        with (
            patch("sam.scheduler.runner.get_session") as mock_session_ctx,
            patch("sam.scheduler.runner.acquire_lease") as mock_acquire,
            patch("sam.scheduler.runner.start_pipeline_run") as mock_start,
            patch("sam.scheduler.runner.collect_once") as mock_collect,
            patch("sam.scheduler.runner.AlertManager") as mock_alert_manager,
            patch("sam.scheduler.runner.finish_pipeline_run") as mock_finish,
            patch("sam.scheduler.runner.release_lease") as mock_release,
        ):
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__.return_value = mock_session

            mock_acquire.return_value = True

            mock_run = MagicMock()
            mock_run.id = uuid.uuid4()
            mock_run.stats = {}
            mock_start.return_value = mock_run

            mock_collect.return_value = {"titles": 5}
            mock_alert_manager.return_value.run_detection_cycle = AsyncMock(return_value=(0, []))

            await runner._collection_job(
                owner_id=uuid.uuid4(),
                interval_minutes=5,
                limit_titles=10,
                limit_reddit=10,
                limit_youtube=10,
                limit_bluesky=10,
            )

            mock_collect.assert_called_once()
            mock_finish.assert_called_once()
            mock_release.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_collection_error(self) -> None:
        with (
            patch("sam.scheduler.runner.get_session") as mock_session_ctx,
            patch("sam.scheduler.runner.acquire_lease") as mock_acquire,
            patch("sam.scheduler.runner.start_pipeline_run") as mock_start,
            patch("sam.scheduler.runner.collect_once") as mock_collect,
            patch("sam.scheduler.runner.AlertManager") as mock_alert_manager,
            patch("sam.scheduler.runner.finish_pipeline_run") as mock_finish,
            patch("sam.scheduler.runner.release_lease") as mock_release,
        ):
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__.return_value = mock_session

            mock_acquire.return_value = True

            mock_run = MagicMock()
            mock_run.id = uuid.uuid4()
            mock_run.stats = {}
            mock_start.return_value = mock_run

            mock_collect.side_effect = RuntimeError("Network error")
            mock_alert_manager.return_value.run_detection_cycle = AsyncMock(return_value=(0, []))

            await runner._collection_job(
                owner_id=uuid.uuid4(),
                interval_minutes=5,
                limit_titles=10,
                limit_reddit=10,
                limit_youtube=10,
                limit_bluesky=10,
            )

            # Should still finish with failed status
            mock_finish.assert_called_once()
            call_args = mock_finish.call_args
            assert call_args.kwargs["status"] == "failed"
            assert "Network error" in call_args.kwargs["error"]

            # Should still release lease
            mock_release.assert_called_once()


class TestMain:
    """Tests for main() entry point."""

    def test_main_once_flag(self) -> None:
        with (
            patch("sam.scheduler.runner.setup_logging"),
            patch("sam.scheduler.runner.get_settings") as mock_settings,
            patch("sam.scheduler.runner.init_db", new_callable=AsyncMock) as mock_init_db,
            patch("sam.scheduler.runner.cleanup_stale_state", new_callable=AsyncMock),
            patch("sam.scheduler.runner.seed_quota_from_db", new_callable=AsyncMock),
            patch("sam.scheduler.runner._collection_job", new_callable=AsyncMock) as mock_job,
            patch("sam.scheduler.runner.run_forever", new_callable=AsyncMock) as mock_run_forever,
            patch("sam.scheduler.runner.close_db", new_callable=AsyncMock) as mock_close_db,
            patch("sam.cache.close_redis", new_callable=AsyncMock),
            patch("sam.config.install_sighup_handler"),
            patch("sys.argv", ["sam-collector", "--once"]),
        ):
            settings = MagicMock()
            settings.collector.polling_interval_minutes = 5
            settings.collector.max_posts_per_subreddit = 10
            mock_settings.return_value = settings

            runner.main()

            mock_init_db.assert_not_awaited()
            mock_run_forever.assert_not_awaited()
            mock_job.assert_awaited_once()
            mock_close_db.assert_awaited_once()

    def test_main_init_db_flag(self) -> None:
        with (
            patch("sam.scheduler.runner.setup_logging"),
            patch("sam.scheduler.runner.get_settings") as mock_settings,
            patch("sam.scheduler.runner.init_db", new_callable=AsyncMock) as mock_init_db,
            patch("sam.scheduler.runner.cleanup_stale_state", new_callable=AsyncMock),
            patch("sam.scheduler.runner.seed_quota_from_db", new_callable=AsyncMock),
            patch("sam.scheduler.runner._collection_job", new_callable=AsyncMock) as mock_job,
            patch("sam.scheduler.runner.run_forever", new_callable=AsyncMock) as mock_run_forever,
            patch("sam.scheduler.runner.close_db", new_callable=AsyncMock) as mock_close_db,
            patch("sam.cache.close_redis", new_callable=AsyncMock),
            patch("sam.config.install_sighup_handler"),
            patch("sys.argv", ["sam-collector", "--init-db", "--once"]),
        ):
            settings = MagicMock()
            settings.collector.polling_interval_minutes = 5
            settings.collector.max_posts_per_subreddit = 10
            mock_settings.return_value = settings

            runner.main()

            mock_init_db.assert_awaited_once()
            mock_run_forever.assert_not_awaited()
            mock_job.assert_awaited_once()
            mock_close_db.assert_awaited_once()

    def test_main_custom_limits(self) -> None:
        with (
            patch("sam.scheduler.runner.setup_logging"),
            patch("sam.scheduler.runner.get_settings") as mock_settings,
            patch("sam.scheduler.runner.init_db", new_callable=AsyncMock) as mock_init_db,
            patch("sam.scheduler.runner.cleanup_stale_state", new_callable=AsyncMock),
            patch("sam.scheduler.runner.seed_quota_from_db", new_callable=AsyncMock),
            patch("sam.scheduler.runner._collection_job", new_callable=AsyncMock) as mock_job,
            patch("sam.scheduler.runner.run_forever", new_callable=AsyncMock) as mock_run_forever,
            patch("sam.scheduler.runner.close_db", new_callable=AsyncMock) as mock_close_db,
            patch("sam.cache.close_redis", new_callable=AsyncMock),
            patch("sam.config.install_sighup_handler"),
            patch(
                "sys.argv",
                [
                    "sam-collector",
                    "--once",
                    "--limit-titles",
                    "5",
                    "--limit-reddit",
                    "20",
                    "--limit-youtube",
                    "30",
                ],
            ),
        ):
            settings = MagicMock()
            settings.collector.polling_interval_minutes = 5
            settings.collector.max_posts_per_subreddit = 10
            mock_settings.return_value = settings

            runner.main()

            mock_init_db.assert_not_awaited()
            mock_run_forever.assert_not_awaited()
            mock_job.assert_awaited_once()
            mock_close_db.assert_awaited_once()


# ---------------------------------------------------------------------------
# Audit-gap tests
# ---------------------------------------------------------------------------


def _make_settings_mock(**overrides: object) -> MagicMock:
    """Build a reusable settings mock for collect_once tests."""
    settings = MagicMock()
    settings.demo_mode = True
    settings.storage.enable_raw_data_storage = overrides.get("enable_raw_data_storage", False)
    settings.storage.raw_data_dir = "/tmp/raw"
    return settings


def _make_session_ctx() -> AsyncMock:
    """Return a mock ``get_session`` that yields an AsyncMock session."""
    mock_session = AsyncMock()
    ctx = AsyncMock()
    ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


class TestRedditDeduplication:
    """A4: Reddit posts are deduplicated by source_id before processing."""

    @pytest.mark.asyncio
    async def test_duplicate_source_ids_are_filtered(self) -> None:
        with (
            patch("sam.scheduler.runner.get_settings") as mock_settings,
            patch("sam.scheduler.runner.get_session") as mock_get_session,
            patch("sam.scheduler.runner.TMDBCollector") as mock_tmdb_cls,
            patch("sam.scheduler.runner.RedditCollector") as mock_reddit_cls,
            patch("sam.scheduler.runner.YouTubeCollector") as mock_youtube_cls,
            patch("sam.scheduler.runner.BlueskyCollector") as mock_bluesky_cls,
            patch("sam.scheduler.runner.upsert_title") as mock_upsert,
            patch("sam.scheduler.runner.insert_mentions") as mock_insert,
            patch("sam.scheduler.runner.analyze_texts_for_sentiment_with_stats") as mock_sentiment,
            patch(
                "sam.scheduler.runner.compute_and_upsert_metrics_snapshots_multi",
                new_callable=AsyncMock,
                return_value=2,
            ),
            patch("sam.scheduler.runner.persist_collection_result"),
            patch("sam.cache.collector_toggle_get", new_callable=AsyncMock, return_value=None),
        ):
            mock_settings.return_value = _make_settings_mock()

            ctx = _make_session_ctx()
            mock_get_session.return_value = ctx.return_value

            # TMDB
            mock_tmdb = AsyncMock()
            mock_title = MagicMock()
            mock_title.title = "Test"
            mock_tmdb.get_trending.return_value = [mock_title]
            mock_tmdb.close = AsyncMock()
            mock_tmdb_cls.return_value = mock_tmdb

            # Reddit — two posts with the SAME source_id (crosspost).
            dup_post_a = MagicMock()
            dup_post_a.source_id = "same_id"
            dup_post_a.content = "Post A"
            dup_post_b = MagicMock()
            dup_post_b.source_id = "same_id"
            dup_post_b.content = "Post B"

            reddit_result = MagicMock()
            reddit_result.success = True
            reddit_result.posts = [dup_post_a, dup_post_b]

            mock_reddit = AsyncMock()
            mock_reddit.is_configured = True
            mock_reddit.collect.return_value = reddit_result
            mock_reddit.close = AsyncMock()
            mock_reddit_cls.return_value = mock_reddit

            # YouTube / Bluesky — no results
            for cls in (mock_youtube_cls, mock_bluesky_cls):
                coll = AsyncMock()
                res = MagicMock()
                res.success = False
                res.posts = []
                coll.collect.return_value = res
                coll.close = AsyncMock()
                cls.return_value = coll

            mock_db_title = MagicMock()
            mock_db_title.id = uuid.uuid4()
            mock_upsert.return_value = mock_db_title

            mock_sr = MagicMock()
            mock_sr.to_dict.return_value = {"compound": 0.1, "label": "neutral", "model": "vader"}

            def _fake(texts, *, translate, log_context="runner"):  # noqa: ARG001
                return [mock_sr] * len(texts), SentimentBatchTranslationStats().to_dict()

            mock_sentiment.side_effect = _fake
            mock_insert.return_value = 1

            await runner.collect_once(
                limit_titles=1,
                limit_reddit=10,
                limit_youtube=10,
                limit_bluesky=10,
            )

            # Sentiment should be called with 1 text (deduped), not 2.
            assert mock_sentiment.call_count == 1
            texts_arg = mock_sentiment.call_args[0][0]
            assert len(texts_arg) == 1

            # insert_mentions should receive only the deduped post.
            assert mock_insert.call_count == 1
            posts_passed = mock_insert.call_args.kwargs["posts"]
            assert len(posts_passed) == 1


class TestBlueskyDeduplication:
    """Bluesky posts are deduplicated by source_id before processing."""

    @pytest.mark.asyncio
    async def test_duplicate_bluesky_source_ids_are_filtered(self) -> None:
        with (
            patch("sam.scheduler.runner.get_settings") as mock_settings,
            patch("sam.scheduler.runner.get_session") as mock_get_session,
            patch("sam.scheduler.runner.TMDBCollector") as mock_tmdb_cls,
            patch("sam.scheduler.runner.RedditCollector") as mock_reddit_cls,
            patch("sam.scheduler.runner.YouTubeCollector") as mock_youtube_cls,
            patch("sam.scheduler.runner.BlueskyCollector") as mock_bluesky_cls,
            patch("sam.scheduler.runner.upsert_title") as mock_upsert,
            patch("sam.scheduler.runner.insert_mentions") as mock_insert,
            patch("sam.scheduler.runner.analyze_texts_for_sentiment_with_stats") as mock_sentiment,
            patch(
                "sam.scheduler.runner.compute_and_upsert_metrics_snapshots_multi",
                new_callable=AsyncMock,
                return_value=2,
            ),
            patch("sam.scheduler.runner.persist_collection_result"),
            patch("sam.cache.collector_toggle_get", new_callable=AsyncMock, return_value=None),
        ):
            mock_settings.return_value = _make_settings_mock()

            ctx = _make_session_ctx()
            mock_get_session.return_value = ctx.return_value

            # TMDB
            mock_tmdb = AsyncMock()
            mock_title = MagicMock()
            mock_title.title = "Test"
            mock_tmdb.get_trending.return_value = [mock_title]
            mock_tmdb.close = AsyncMock()
            mock_tmdb_cls.return_value = mock_tmdb

            # Reddit / YouTube — no results
            for cls in (mock_reddit_cls, mock_youtube_cls):
                coll = AsyncMock()
                res = MagicMock()
                res.success = False
                res.posts = []
                coll.collect.return_value = res
                coll.close = AsyncMock()
                cls.return_value = coll

            # Bluesky — two posts with the SAME source_id (repost).
            dup_a = MagicMock()
            dup_a.source_id = "bsky_dup"
            dup_a.content = "Post A"
            dup_b = MagicMock()
            dup_b.source_id = "bsky_dup"
            dup_b.content = "Post B"

            bsky_result = MagicMock()
            bsky_result.success = True
            bsky_result.posts = [dup_a, dup_b]

            mock_bluesky = AsyncMock()
            mock_bluesky.is_configured = True
            mock_bluesky.collect.return_value = bsky_result
            mock_bluesky.close = AsyncMock()
            mock_bluesky_cls.return_value = mock_bluesky

            mock_db_title = MagicMock()
            mock_db_title.id = uuid.uuid4()
            mock_upsert.return_value = mock_db_title

            mock_sr = MagicMock()
            mock_sr.to_dict.return_value = {"compound": 0.1, "label": "neutral", "model": "vader"}

            def _fake(texts, *, translate, log_context="runner"):  # noqa: ARG001
                return [mock_sr] * len(texts), SentimentBatchTranslationStats().to_dict()

            mock_sentiment.side_effect = _fake
            mock_insert.return_value = 1

            await runner.collect_once(
                limit_titles=1,
                limit_reddit=10,
                limit_youtube=10,
                limit_bluesky=10,
            )

            # Sentiment should be called with 1 text (deduped), not 2.
            assert mock_sentiment.call_count == 1
            texts_arg = mock_sentiment.call_args[0][0]
            assert len(texts_arg) == 1

            # insert_mentions should receive only the deduped post.
            assert mock_insert.call_count == 1
            posts_passed = mock_insert.call_args.kwargs["posts"]
            assert len(posts_passed) == 1


class TestCircuitBreaker:
    """C1: Platform circuit breaker skips after _MAX_PLATFORM_FAILURES."""

    @pytest.mark.asyncio
    async def test_reddit_skipped_after_consecutive_failures(self) -> None:
        """After 2 consecutive Reddit failures the 3rd title should skip Reddit."""
        with (
            patch("sam.scheduler.runner.get_settings") as mock_settings,
            patch("sam.scheduler.runner.get_session") as mock_get_session,
            patch("sam.scheduler.runner.TMDBCollector") as mock_tmdb_cls,
            patch("sam.scheduler.runner.RedditCollector") as mock_reddit_cls,
            patch("sam.scheduler.runner.YouTubeCollector") as mock_youtube_cls,
            patch("sam.scheduler.runner.BlueskyCollector") as mock_bluesky_cls,
            patch("sam.scheduler.runner.upsert_title") as mock_upsert,
            patch("sam.scheduler.runner.insert_mentions", return_value=0),
            patch("sam.scheduler.runner.analyze_texts_for_sentiment_with_stats") as mock_sentiment,
            patch(
                "sam.scheduler.runner.compute_and_upsert_metrics_snapshots_multi",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch("sam.scheduler.runner.persist_collection_result"),
            patch("sam.cache.collector_toggle_get", new_callable=AsyncMock, return_value=None),
        ):
            mock_settings.return_value = _make_settings_mock()

            ctx = _make_session_ctx()
            mock_get_session.return_value = ctx.return_value

            # 3 trending titles so we get 3 iterations.
            titles = [MagicMock() for _ in range(3)]
            for i, t in enumerate(titles):
                t.title = f"Title {i}"
            mock_tmdb = AsyncMock()
            mock_tmdb.get_trending.return_value = titles
            mock_tmdb.close = AsyncMock()
            mock_tmdb_cls.return_value = mock_tmdb

            # Reddit always fails.
            mock_reddit = AsyncMock()
            mock_reddit.is_configured = True
            mock_reddit.collect.side_effect = RuntimeError("boom")
            mock_reddit.close = AsyncMock()
            mock_reddit_cls.return_value = mock_reddit

            # YouTube / Bluesky — disabled (no credentials).
            for cls in (mock_youtube_cls, mock_bluesky_cls):
                coll = AsyncMock()
                res = MagicMock()
                res.success = False
                res.posts = []
                coll.collect.return_value = res
                coll.close = AsyncMock()
                cls.return_value = coll

            mock_db_title = MagicMock()
            mock_db_title.id = uuid.uuid4()
            mock_upsert.return_value = mock_db_title

            mock_sentiment.side_effect = lambda texts, **kw: (  # noqa: ARG005
                [],
                SentimentBatchTranslationStats().to_dict(),
            )

            stats = await runner.collect_once(
                limit_titles=3,
                limit_reddit=5,
                limit_youtube=5,
                limit_bluesky=5,
            )

            # Circuit breaker threshold is 2 — Reddit should be called for
            # the first 2 titles (both fail), then skipped for title 3.
            assert mock_reddit.collect.call_count == 2
            assert stats["titles"] == 3


class TestRawStorageFailureTracking:
    """C3: stats['raw_storage_failures'] incremented when persist returns None."""

    @pytest.mark.asyncio
    async def test_raw_storage_failure_counted(self) -> None:
        with (
            patch("sam.scheduler.runner.get_settings") as mock_settings,
            patch("sam.scheduler.runner.get_session") as mock_get_session,
            patch("sam.scheduler.runner.TMDBCollector") as mock_tmdb_cls,
            patch("sam.scheduler.runner.RedditCollector") as mock_reddit_cls,
            patch("sam.scheduler.runner.YouTubeCollector") as mock_youtube_cls,
            patch("sam.scheduler.runner.BlueskyCollector") as mock_bluesky_cls,
            patch("sam.scheduler.runner.upsert_title") as mock_upsert,
            patch("sam.scheduler.runner.insert_mentions", return_value=1),
            patch("sam.scheduler.runner.analyze_texts_for_sentiment_with_stats") as mock_sentiment,
            patch(
                "sam.scheduler.runner.compute_and_upsert_metrics_snapshots_multi",
                new_callable=AsyncMock,
                return_value=2,
            ),
            patch(
                "sam.scheduler.runner.persist_collection_result",
                new_callable=AsyncMock,
                return_value=None,  # simulate failure
            ),
            patch("sam.cache.collector_toggle_get", new_callable=AsyncMock, return_value=None),
        ):
            settings = _make_settings_mock(enable_raw_data_storage=True)
            mock_settings.return_value = settings

            ctx = _make_session_ctx()
            mock_get_session.return_value = ctx.return_value

            mock_tmdb = AsyncMock()
            mock_title = MagicMock()
            mock_title.title = "Test"
            mock_tmdb.get_trending.return_value = [mock_title]
            mock_tmdb.close = AsyncMock()
            mock_tmdb_cls.return_value = mock_tmdb

            # Reddit with a post
            mock_reddit = AsyncMock()
            mock_reddit.is_configured = True
            post = MagicMock()
            post.source_id = "r_post"
            post.content = "text"
            reddit_result = MagicMock()
            reddit_result.success = True
            reddit_result.posts = [post]
            mock_reddit.collect.return_value = reddit_result
            mock_reddit.close = AsyncMock()
            mock_reddit_cls.return_value = mock_reddit

            # YouTube / Bluesky — no results
            for cls in (mock_youtube_cls, mock_bluesky_cls):
                coll = AsyncMock()
                res = MagicMock()
                res.success = False
                res.posts = []
                coll.collect.return_value = res
                coll.close = AsyncMock()
                cls.return_value = coll

            mock_db_title = MagicMock()
            mock_db_title.id = uuid.uuid4()
            mock_upsert.return_value = mock_db_title

            mock_sr = MagicMock()
            mock_sr.to_dict.return_value = {"compound": 0.1, "label": "neutral", "model": "vader"}

            mock_sentiment.side_effect = lambda texts, **kw: (  # noqa: ARG005
                [mock_sr] * len(texts),
                SentimentBatchTranslationStats().to_dict(),
            )

            stats = await runner.collect_once(
                limit_titles=1,
                limit_reddit=5,
                limit_youtube=5,
                limit_bluesky=5,
            )

            assert stats["raw_storage_failures"] >= 1


class TestCommentFailuresCircuitBreaker:
    """BUG 3 fix: comment endpoint failures feed into circuit breaker."""

    @pytest.mark.asyncio
    async def test_all_comment_failures_returns_failure_count(self) -> None:
        youtube = AsyncMock()
        youtube.collect_comments.return_value = CommentCollectionResult(
            comments=[],
            had_error=True,
        )

        class _AlwaysBudget:
            def youtube_has_budget(self, cost: int) -> bool:  # noqa: ARG002
                return True

        posts = [MagicMock(source_id=f"v{i}") for i in range(3)]

        comments, spam, failures = await runner._collect_youtube_comments_parallel(
            youtube=youtube,
            posts=posts,
            limit=10,
            enable_spam_filter=False,
            quota=_AlwaysBudget(),
            demo_mode=False,
        )

        assert comments == []
        assert failures == 3

    @pytest.mark.asyncio
    async def test_partial_failures_counted(self) -> None:
        """When some comments succeed and some fail, failures are counted but
        the breaker condition (comment_failures and not all_comments) is False."""
        youtube = AsyncMock()

        ok_comment = MagicMock(source_id="c1", content="Nice")
        youtube.collect_comments.side_effect = [
            CommentCollectionResult(comments=[ok_comment]),  # vid0 succeeds
            CommentCollectionResult(comments=[], had_error=True),  # vid1 fails
        ]

        class _AlwaysBudget:
            def youtube_has_budget(self, cost: int) -> bool:  # noqa: ARG002
                return True

        posts = [MagicMock(source_id="v0"), MagicMock(source_id="v1")]

        comments, spam, failures = await runner._collect_youtube_comments_parallel(
            youtube=youtube,
            posts=posts,
            limit=10,
            enable_spam_filter=False,
            quota=_AlwaysBudget(),
            demo_mode=False,
        )

        assert len(comments) == 1
        assert failures == 1
