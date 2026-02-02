"""Tests for sam.pipeline.metrics_snapshots module."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sam.pipeline.metrics_snapshots import compute_and_upsert_metrics_snapshot
from sam.processors.metrics import EngagementMetrics


class TestComputeAndUpsertMetricsSnapshot:
    """Tests for compute_and_upsert_metrics_snapshot function."""

    @pytest.mark.asyncio
    async def test_computes_from_empty_mentions(self) -> None:
        with (
            patch("sam.pipeline.metrics_snapshots.get_calculator") as mock_get_calc,
            patch("sam.pipeline.metrics_snapshots.get_mentions_in_window") as mock_get_mentions,
            patch("sam.pipeline.metrics_snapshots.get_latest_metrics_snapshot") as mock_get_latest,
            patch("sam.pipeline.metrics_snapshots.upsert_metrics_snapshot") as mock_upsert,
        ):
            # Mock calculator
            mock_calc = MagicMock()
            mock_result = EngagementMetrics(
                mention_count=0,
                unique_authors=0,
                total_engagement=0,
                mention_velocity=0.0,
                velocity_change=0.0,
                avg_sentiment=0.0,
                sentiment_volatility=0.0,
                positive_ratio=0.0,
                negative_ratio=0.0,
                attention_index=0.0,
                hype_acceleration=0.0,
                window_start=datetime(2026, 1, 30, 0, 0, 0, tzinfo=UTC),
                window_end=datetime(2026, 1, 30, 1, 0, 0, tzinfo=UTC),
                platform_breakdown={},
            )
            mock_calc.calculate.return_value = mock_result
            mock_get_calc.return_value = mock_calc

            # No mentions
            mock_get_mentions.return_value = []
            mock_get_latest.return_value = None

            mock_session = AsyncMock()
            title_id = uuid.uuid4()
            snapshot_time = datetime(2026, 1, 30, 1, 0, 0, tzinfo=UTC)

            await compute_and_upsert_metrics_snapshot(
                mock_session,
                title_id=title_id,
                snapshot_time=snapshot_time,
                window_hours=1,
            )

            mock_upsert.assert_called_once()
            call_kwargs = mock_upsert.call_args.kwargs
            assert call_kwargs["title_id"] == title_id
            assert call_kwargs["snapshot_time"] == snapshot_time
            assert call_kwargs["window_hours"] == 1
            assert call_kwargs["metrics"]["mention_count"] == 0

    @pytest.mark.asyncio
    async def test_computes_with_mentions(self) -> None:
        with (
            patch("sam.pipeline.metrics_snapshots.get_calculator") as mock_get_calc,
            patch("sam.pipeline.metrics_snapshots.get_mentions_in_window") as mock_get_mentions,
            patch("sam.pipeline.metrics_snapshots.get_latest_metrics_snapshot") as mock_get_latest,
            patch("sam.pipeline.metrics_snapshots.upsert_metrics_snapshot") as mock_upsert,
        ):
            mock_calc = MagicMock()
            mock_result = EngagementMetrics(
                mention_count=10,
                unique_authors=5,
                total_engagement=500,
                mention_velocity=10.0,
                velocity_change=2.0,
                avg_sentiment=0.5,
                sentiment_volatility=0.1,
                positive_ratio=0.7,
                negative_ratio=0.1,
                attention_index=75.0,
                hype_acceleration=1.5,
                window_start=datetime(2026, 1, 30, 0, 0, 0, tzinfo=UTC),
                window_end=datetime(2026, 1, 30, 1, 0, 0, tzinfo=UTC),
                platform_breakdown={"reddit": 7, "youtube": 3},
            )
            mock_calc.calculate.return_value = mock_result
            mock_get_calc.return_value = mock_calc

            # Create mock mentions
            mock_mention = MagicMock()
            mock_mention.created_at = datetime(2026, 1, 30, 0, 30, 0, tzinfo=UTC)
            mock_mention.platform = "reddit"
            mock_mention.author = "user1"
            mock_mention.metrics = {"score": 50}
            mock_mention.sentiment = {"compound": 0.5}
            mock_get_mentions.return_value = [mock_mention]
            mock_get_latest.return_value = None

            mock_session = AsyncMock()
            title_id = uuid.uuid4()

            await compute_and_upsert_metrics_snapshot(
                mock_session,
                title_id=title_id,
                snapshot_time=datetime(2026, 1, 30, 1, 0, 0, tzinfo=UTC),
                window_hours=1,
            )

            mock_upsert.assert_called_once()
            call_kwargs = mock_upsert.call_args.kwargs
            assert call_kwargs["metrics"]["mention_count"] == 10
            assert call_kwargs["metrics"]["reddit_mentions"] == 7
            assert call_kwargs["metrics"]["youtube_mentions"] == 3

    @pytest.mark.asyncio
    async def test_uses_previous_metrics_for_velocity(self) -> None:
        with (
            patch("sam.pipeline.metrics_snapshots.get_calculator") as mock_get_calc,
            patch("sam.pipeline.metrics_snapshots.get_mentions_in_window") as mock_get_mentions,
            patch("sam.pipeline.metrics_snapshots.get_latest_metrics_snapshot") as mock_get_latest,
            patch("sam.pipeline.metrics_snapshots.upsert_metrics_snapshot"),
        ):
            mock_calc = MagicMock()
            mock_result = EngagementMetrics(
                mention_count=15,
                unique_authors=8,
                total_engagement=800,
                mention_velocity=15.0,
                velocity_change=5.0,  # Increased from previous
                avg_sentiment=0.6,
                sentiment_volatility=0.15,
                positive_ratio=0.75,
                negative_ratio=0.1,
                attention_index=85.0,
                hype_acceleration=2.0,
                window_start=datetime(2026, 1, 30, 1, 0, 0, tzinfo=UTC),
                window_end=datetime(2026, 1, 30, 2, 0, 0, tzinfo=UTC),
                platform_breakdown={"reddit": 10, "youtube": 5},
            )
            mock_calc.calculate.return_value = mock_result
            mock_get_calc.return_value = mock_calc

            mock_get_mentions.return_value = []

            # Previous snapshot exists
            mock_previous = MagicMock()
            mock_previous.mention_count = 10
            mock_previous.unique_authors = 5
            mock_previous.mention_velocity = 10.0
            mock_previous.velocity_change = 2.0
            mock_previous.avg_sentiment = 0.5
            mock_previous.sentiment_volatility = 0.1
            mock_previous.positive_ratio = 0.7
            mock_previous.attention_index = 75.0
            mock_previous.hype_acceleration = 1.5
            mock_get_latest.return_value = mock_previous

            mock_session = AsyncMock()
            title_id = uuid.uuid4()

            await compute_and_upsert_metrics_snapshot(
                mock_session,
                title_id=title_id,
                snapshot_time=datetime(2026, 1, 30, 2, 0, 0, tzinfo=UTC),
                window_hours=1,
            )

            # Verify calculator was called with previous_metrics
            mock_calc.calculate.assert_called_once()
            call_kwargs = mock_calc.calculate.call_args.kwargs
            assert call_kwargs["previous_metrics"] is not None
            assert call_kwargs["previous_metrics"].mention_count == 10

    @pytest.mark.asyncio
    async def test_24_hour_window(self) -> None:
        with (
            patch("sam.pipeline.metrics_snapshots.get_calculator") as mock_get_calc,
            patch("sam.pipeline.metrics_snapshots.get_mentions_in_window") as mock_get_mentions,
            patch("sam.pipeline.metrics_snapshots.get_latest_metrics_snapshot") as mock_get_latest,
            patch("sam.pipeline.metrics_snapshots.upsert_metrics_snapshot") as mock_upsert,
        ):
            mock_calc = MagicMock()
            mock_result = EngagementMetrics(
                mention_count=100,
                unique_authors=50,
                total_engagement=5000,
                mention_velocity=4.17,  # 100 / 24
                velocity_change=0.5,
                avg_sentiment=0.3,
                sentiment_volatility=0.2,
                positive_ratio=0.5,
                negative_ratio=0.2,
                attention_index=60.0,
                hype_acceleration=0.2,
                window_start=datetime(2026, 1, 29, 1, 0, 0, tzinfo=UTC),
                window_end=datetime(2026, 1, 30, 1, 0, 0, tzinfo=UTC),
                platform_breakdown={"reddit": 60, "youtube": 40},
            )
            mock_calc.calculate.return_value = mock_result
            mock_get_calc.return_value = mock_calc

            mock_get_mentions.return_value = []
            mock_get_latest.return_value = None

            mock_session = AsyncMock()
            title_id = uuid.uuid4()
            snapshot_time = datetime(2026, 1, 30, 1, 0, 0, tzinfo=UTC)

            await compute_and_upsert_metrics_snapshot(
                mock_session,
                title_id=title_id,
                snapshot_time=snapshot_time,
                window_hours=24,
            )

            # Verify window calculation
            mock_get_mentions.assert_called_once()
            call_kwargs = mock_get_mentions.call_args.kwargs
            expected_start = snapshot_time - timedelta(hours=24)
            assert call_kwargs["window_start"] == expected_start
            assert call_kwargs["window_end"] == snapshot_time

            mock_upsert.assert_called_once()
            assert mock_upsert.call_args.kwargs["window_hours"] == 24

    @pytest.mark.asyncio
    async def test_handles_null_previous_values(self) -> None:
        """Test that null values in previous snapshot are handled."""
        with (
            patch("sam.pipeline.metrics_snapshots.get_calculator") as mock_get_calc,
            patch("sam.pipeline.metrics_snapshots.get_mentions_in_window") as mock_get_mentions,
            patch("sam.pipeline.metrics_snapshots.get_latest_metrics_snapshot") as mock_get_latest,
            patch("sam.pipeline.metrics_snapshots.upsert_metrics_snapshot") as mock_upsert,
        ):
            mock_calc = MagicMock()
            mock_result = EngagementMetrics(
                mention_count=5,
                unique_authors=3,
                total_engagement=100,
                mention_velocity=5.0,
                velocity_change=5.0,
                avg_sentiment=0.0,
                sentiment_volatility=0.0,
                positive_ratio=0.0,
                negative_ratio=0.0,
                attention_index=50.0,
                hype_acceleration=0.0,
                window_start=datetime(2026, 1, 30, 0, 0, 0, tzinfo=UTC),
                window_end=datetime(2026, 1, 30, 1, 0, 0, tzinfo=UTC),
                platform_breakdown={},
            )
            mock_calc.calculate.return_value = mock_result
            mock_get_calc.return_value = mock_calc

            mock_get_mentions.return_value = []

            # Previous with null values
            mock_previous = MagicMock()
            mock_previous.mention_count = 3
            mock_previous.unique_authors = 2
            mock_previous.mention_velocity = None
            mock_previous.velocity_change = None
            mock_previous.avg_sentiment = None
            mock_previous.sentiment_volatility = None
            mock_previous.positive_ratio = None
            mock_previous.attention_index = None
            mock_previous.hype_acceleration = None
            mock_get_latest.return_value = mock_previous

            mock_session = AsyncMock()

            # Should not raise
            await compute_and_upsert_metrics_snapshot(
                mock_session,
                title_id=uuid.uuid4(),
                snapshot_time=datetime(2026, 1, 30, 1, 0, 0, tzinfo=UTC),
                window_hours=1,
            )

            mock_upsert.assert_called_once()
