"""Tests for sam.quota — QuotaTracker budget checks and daily reset."""

from __future__ import annotations

from unittest.mock import patch

from sam.quota import (
    YOUTUBE_DAILY_BUDGET,
    YOUTUBE_SEARCH_COST,
    YOUTUBE_VIDEOS_COST,
    QuotaTracker,
)


class TestQuotaTrackerBudget:
    """Verify has_budget / youtube_has_budget behaviour."""

    def test_fresh_tracker_has_budget(self) -> None:
        tracker = QuotaTracker()
        assert tracker.youtube_has_budget() is True

    def test_budget_false_after_exceeding_limit(self) -> None:
        tracker = QuotaTracker()
        # 100 search calls * 100 units = 10,000 = exactly the budget
        for _ in range(100):
            tracker.record("youtube", "search.list", YOUTUBE_SEARCH_COST)

        # At exactly the limit, one more search would exceed it
        assert tracker.youtube_has_budget(cost=YOUTUBE_SEARCH_COST) is False
        # But a cheap videos.list call (1 unit) still doesn't fit
        # because 10_000 + 1 > 10_000
        assert tracker.youtube_has_budget(cost=YOUTUBE_VIDEOS_COST) is False

    def test_budget_true_just_under_limit(self) -> None:
        tracker = QuotaTracker()
        # 99 search calls = 9,900 units — one more should still fit
        for _ in range(99):
            tracker.record("youtube", "search.list", YOUTUBE_SEARCH_COST)

        assert tracker.youtube_has_budget(cost=YOUTUBE_SEARCH_COST) is True

    def test_has_budget_generic(self) -> None:
        tracker = QuotaTracker()
        tracker.record("tmdb", "trending", 1)
        # Custom budget of 5
        assert tracker.has_budget("tmdb", cost=4, daily_budget=5) is True
        assert tracker.has_budget("tmdb", cost=5, daily_budget=5) is False


class TestQuotaTrackerDailyReset:
    """Verify that usage resets when the UTC date changes."""

    def test_reset_on_new_day(self) -> None:
        tracker = QuotaTracker()

        # Record usage "today"
        with patch.object(tracker, "_today", return_value="2026-02-04"):
            tracker.record("youtube", "search.list", YOUTUBE_SEARCH_COST)
            assert tracker.youtube_has_budget() is True  # only 100 of 10k used

        # "Tomorrow" — usage should be reset
        with patch.object(tracker, "_today", return_value="2026-02-05"):
            usage = tracker.get_usage("youtube")
            assert usage["total_units"] == 0
            assert usage["total_calls"] == 0
            assert tracker.youtube_has_budget() is True

    def test_usage_accumulates_within_same_day(self) -> None:
        tracker = QuotaTracker()

        with patch.object(tracker, "_today", return_value="2026-02-04"):
            tracker.record("youtube", "search.list", YOUTUBE_SEARCH_COST)
            tracker.record("youtube", "videos.list", YOUTUBE_VIDEOS_COST)

            usage = tracker.get_usage("youtube")
            assert usage["total_units"] == YOUTUBE_SEARCH_COST + YOUTUBE_VIDEOS_COST
            assert usage["total_calls"] == 2


class TestQuotaTrackerSummary:
    """Verify get_youtube_summary returns correct budget info."""

    def test_summary_budget_fields(self) -> None:
        tracker = QuotaTracker()
        tracker.record("youtube", "search.list", YOUTUBE_SEARCH_COST)

        summary = tracker.get_youtube_summary()
        assert summary["daily_budget"] == YOUTUBE_DAILY_BUDGET
        assert summary["total_units"] == YOUTUBE_SEARCH_COST
        assert summary["budget_remaining"] == YOUTUBE_DAILY_BUDGET - YOUTUBE_SEARCH_COST
        assert summary["budget_used_pct"] == round(
            YOUTUBE_SEARCH_COST / YOUTUBE_DAILY_BUDGET * 100, 1
        )

    def test_summary_zero_usage(self) -> None:
        tracker = QuotaTracker()
        summary = tracker.get_youtube_summary()
        assert summary["total_units"] == 0
        assert summary["budget_remaining"] == YOUTUBE_DAILY_BUDGET
        assert summary["budget_used_pct"] == 0.0
