"""Tests for sam.scheduler.runner module."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sam.processors.sentiment import SentimentBatchTranslationStats
from sam.scheduler import runner


class TestSnapshotHour:
    """Tests for _snapshot_hour helper."""

    def test_rounds_up_to_next_hour(self) -> None:
        dt = datetime(2026, 1, 30, 14, 35, 22, 123456, tzinfo=UTC)
        result = runner._snapshot_hour(dt)
        # Rounds *up* so mentions collected at 14:35 fall inside the window.
        assert result.hour == 15
        assert result.minute == 0
        assert result.second == 0
        assert result.microsecond == 0

    def test_exact_hour_unchanged(self) -> None:
        dt = datetime(2026, 1, 30, 14, 0, 0, 0, tzinfo=UTC)
        result = runner._snapshot_hour(dt)
        assert result == dt

    def test_preserves_date(self) -> None:
        dt = datetime(2026, 6, 15, 23, 59, 59, tzinfo=UTC)
        result = runner._snapshot_hour(dt)
        # 23:59 rounds up to next day 00:00.
        assert result.year == 2026
        assert result.month == 6
        assert result.day == 16
        assert result.hour == 0


class TestCollectOnce:
    """Tests for collect_once() function."""

    @pytest.mark.asyncio
    async def test_collect_once_returns_stats(self) -> None:
        with (
            patch("sam.scheduler.runner.get_settings") as mock_settings,
            patch("sam.scheduler.runner.TMDBCollector") as mock_tmdb_cls,
            patch("sam.scheduler.runner.RedditCollector") as mock_reddit_cls,
            patch("sam.scheduler.runner.YouTubeCollector") as mock_youtube_cls,
            patch("sam.scheduler.runner.BlueskyCollector") as mock_bluesky_cls,
            patch("sam.scheduler.runner.upsert_title") as mock_upsert,
            patch("sam.scheduler.runner.insert_mentions") as mock_insert,
            patch(
                "sam.scheduler.runner.analyze_sentiment_batch_with_translation"
            ) as mock_sentiment,
            patch("sam.scheduler.runner.compute_and_upsert_metrics_snapshot"),
            patch("sam.scheduler.runner.persist_collection_result"),
            patch("sam.cache.collector_toggle_get", new_callable=AsyncMock, return_value=None),
        ):
            # Configure settings
            settings = MagicMock()
            settings.demo_mode = True
            settings.storage.enable_raw_data_storage = False
            mock_settings.return_value = settings

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
            # analyze_sentiment_batch_with_translation is called separately for
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
                return [mock_sentiment_result] * len(texts), SentimentBatchTranslationStats()

            mock_sentiment.side_effect = _fake_batch_sentiment

            # Mock insert returns count
            mock_insert.return_value = 1

            # Run
            mock_session = AsyncMock()
            stats = await runner.collect_once(
                mock_session,
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
            patch("sam.scheduler.runner.TMDBCollector") as mock_tmdb_cls,
            patch("sam.scheduler.runner.RedditCollector") as mock_reddit_cls,
            patch("sam.scheduler.runner.YouTubeCollector") as mock_youtube_cls,
            patch("sam.scheduler.runner.BlueskyCollector") as mock_bluesky_cls,
            patch("sam.scheduler.runner.upsert_title"),
            patch("sam.scheduler.runner.compute_and_upsert_metrics_snapshot"),
            patch("sam.cache.collector_toggle_get", new_callable=AsyncMock, return_value=None),
        ):
            settings = MagicMock()
            settings.demo_mode = True
            settings.storage.enable_raw_data_storage = False
            mock_settings.return_value = settings

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

            mock_session = AsyncMock()
            stats = await runner.collect_once(
                mock_session,
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
            patch("sam.scheduler.runner.TMDBCollector") as mock_tmdb_cls,
            patch("sam.scheduler.runner.RedditCollector") as mock_reddit_cls,
            patch("sam.scheduler.runner.YouTubeCollector") as mock_youtube_cls,
            patch("sam.scheduler.runner.BlueskyCollector") as mock_bluesky_cls,
            patch("sam.scheduler.runner.upsert_title") as mock_upsert,
            patch("sam.scheduler.runner.insert_mentions") as mock_insert,
            patch(
                "sam.scheduler.runner.analyze_sentiment_batch_with_translation"
            ) as mock_sentiment,
            patch("sam.scheduler.runner.compute_and_upsert_metrics_snapshot"),
            patch("sam.scheduler.runner.persist_collection_result") as mock_persist,
            patch("sam.cache.collector_toggle_get", new_callable=AsyncMock, return_value=None),
        ):
            settings = MagicMock()
            settings.demo_mode = True
            settings.storage.enable_raw_data_storage = True
            settings.storage.raw_data_dir = "/tmp/raw"
            mock_settings.return_value = settings

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
                return [mock_sentiment_result] * len(texts), SentimentBatchTranslationStats()

            mock_sentiment.side_effect = _fake_batch_sentiment

            mock_insert.return_value = 1

            mock_session = AsyncMock()
            await runner.collect_once(
                mock_session,
                limit_titles=1,
                limit_reddit=1,
                limit_youtube=1,
                limit_bluesky=1,
            )

            # Should have persisted raw Reddit data
            mock_persist.assert_called_once()


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

        post = MagicMock()
        post.source_id = "sid1"
        post.content = "Great movie"

        sentiment = MagicMock()
        sentiment.to_dict.return_value = {
            "compound": 0.5,
            "label": "positive",
            "model": "vader",
        }

        with patch("sam.scheduler.runner._enrich_sentiments_with_nlp") as mock_enrich:
            result = await runner._build_enriched_sentiment_map([post], [sentiment], settings)

        assert "sid1" in result
        mock_enrich.assert_called_once()

    @pytest.mark.asyncio
    async def test_build_enriched_sentiment_map_calls_enrichment_when_sarcasm_enabled(self) -> None:
        settings = MagicMock()
        settings.enable_emotion_detection = False
        settings.enable_sarcasm_detection = True

        post = MagicMock()
        post.source_id = "sid1"
        post.content = "Great movie"

        sentiment = MagicMock()
        sentiment.to_dict.return_value = {
            "compound": 0.5,
            "label": "positive",
            "model": "vader",
        }

        with patch("sam.scheduler.runner._enrich_sentiments_with_nlp") as mock_enrich:
            result = await runner._build_enriched_sentiment_map([post], [sentiment], settings)

        assert "sid1" in result
        mock_enrich.assert_called_once()

    @pytest.mark.asyncio
    async def test_build_enriched_sentiment_map_skips_enrichment_when_disabled(self) -> None:
        settings = MagicMock()
        settings.enable_emotion_detection = False
        settings.enable_sarcasm_detection = False

        post = MagicMock()
        post.source_id = "sid1"
        post.content = "Great movie"

        sentiment = MagicMock()
        sentiment.to_dict.return_value = {
            "compound": 0.5,
            "label": "positive",
            "model": "vader",
        }

        with patch("sam.scheduler.runner._enrich_sentiments_with_nlp") as mock_enrich:
            result = await runner._build_enriched_sentiment_map([post], [sentiment], settings)

        assert "sid1" in result
        mock_enrich.assert_not_called()

    def test_enrich_sentiments_applies_sarcasm_flag(self) -> None:
        settings = MagicMock()
        settings.enable_emotion_detection = False
        settings.enable_sarcasm_detection = True

        sentiment_map: dict[str, dict[str, object]] = {"sid1": {"compound": 0.2}}
        texts = ["Totally not bad at all 🙃"]
        source_ids = ["sid1"]

        detector = MagicMock()
        detector.detect_batch.return_value = [(True, 0.9132)]

        with patch("sam.processors.sarcasm.get_sarcasm_detector", return_value=detector):
            runner._enrich_sentiments_with_nlp(texts, sentiment_map, source_ids, settings)

        assert sentiment_map["sid1"]["is_sarcastic"] is True
        assert sentiment_map["sid1"]["sarcasm_confidence"] == 0.9132
        assert "emotions" not in sentiment_map["sid1"]

    def test_enrich_sentiments_applies_emotions(self) -> None:
        settings = MagicMock()
        settings.enable_emotion_detection = True
        settings.enable_sarcasm_detection = False

        sentiment_map: dict[str, dict[str, object]] = {"sid1": {"compound": 0.8}}
        texts = ["This is amazing"]
        source_ids = ["sid1"]

        detector = MagicMock()
        detector.detect_batch.return_value = [{"joy": 0.98123, "neutral": 0.01877}]

        with patch("sam.processors.emotions.get_emotion_detector", return_value=detector):
            runner._enrich_sentiments_with_nlp(texts, sentiment_map, source_ids, settings)

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
