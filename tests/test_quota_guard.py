"""Tests for sam.quota — QuotaTracker budget checks and daily reset."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from sam.quota import (
    YOUTUBE_DAILY_BUDGET,
    YOUTUBE_SEARCH_COST,
    YOUTUBE_VIDEOS_COST,
    QuotaTracker,
    YouTubeDailyQuota,
    get_current_youtube_quota,
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


class TestQuotaTrackerSeed:
    """Verify seed(), including additive merging and edge cases."""

    def test_seed_basic(self) -> None:
        tracker = QuotaTracker()
        tracker.seed("youtube", 500, {"search.list": 5})
        usage = tracker.get_usage("youtube")
        assert usage["total_units"] == 500
        assert usage["calls_by_endpoint"] == {"search.list": 5}

    def test_seed_additive(self) -> None:
        tracker = QuotaTracker()
        tracker.seed("youtube", 300, {"search.list": 3})
        tracker.seed("youtube", 200, {"search.list": 2, "videos.list": 1})
        usage = tracker.get_usage("youtube")
        assert usage["total_units"] == 500
        assert usage["calls_by_endpoint"]["search.list"] == 5
        assert usage["calls_by_endpoint"]["videos.list"] == 1

    def test_seed_then_record(self) -> None:
        tracker = QuotaTracker()
        tracker.seed("youtube", 500)
        tracker.record("youtube", "search.list", YOUTUBE_SEARCH_COST)
        usage = tracker.get_usage("youtube")
        assert usage["total_units"] == 500 + YOUTUBE_SEARCH_COST

    def test_seed_zero_units_no_calls_is_noop(self) -> None:
        tracker = QuotaTracker()
        tracker.seed("youtube", 0)
        usage = tracker.get_usage("youtube")
        assert usage["total_units"] == 0

    def test_seed_zero_units_with_calls_still_seeds(self) -> None:
        tracker = QuotaTracker()
        tracker.seed("youtube", 0, {"search.list": 2})
        usage = tracker.get_usage("youtube")
        assert usage["calls_by_endpoint"]["search.list"] == 2

    def test_seed_does_not_cross_days(self) -> None:
        tracker = QuotaTracker()
        with patch.object(tracker, "_today", return_value="2026-02-04"):
            tracker.seed("youtube", 500)

        with patch.object(tracker, "_today", return_value="2026-02-05"):
            usage = tracker.get_usage("youtube")
            # New day: seed from previous day should be gone
            assert usage["total_units"] == 0

    def test_seed_affects_budget_check(self) -> None:
        tracker = QuotaTracker()
        tracker.seed("youtube", YOUTUBE_DAILY_BUDGET - 50)
        # 9950 + 100 = 10050 > 10000 — search doesn't fit
        assert tracker.youtube_has_budget(cost=YOUTUBE_SEARCH_COST) is False
        # 9950 + 50 = 10000 <= 10000 — exactly fits
        assert tracker.youtube_has_budget(cost=50) is True
        # 9950 + 51 = 10001 > 10000 — just over
        assert tracker.youtube_has_budget(cost=51) is False


@pytest.mark.asyncio
async def test_get_current_youtube_quota_prefers_shared_usage(monkeypatch) -> None:
    async def _fake_shared(_platform: str):
        return {
            "date": "2026-02-04",
            "total_units": 321,
            "total_calls": 7,
            "calls_by_endpoint": {"search.list": 3, "videos.list": 4},
        }

    async def _fake_db(_session=None):
        return YouTubeDailyQuota(
            date="2026-02-04",
            total_units=100,
            total_calls=2,
            calls_by_endpoint={"search.list": 1, "videos.list": 1},
            last_run_at="2026-02-04T00:00:00+00:00",
        )

    monkeypatch.setattr("sam.quota._get_shared_usage", _fake_shared)
    monkeypatch.setattr("sam.quota.aggregate_youtube_quota_from_db", _fake_db)

    quota = await get_current_youtube_quota()
    assert quota.total_units == 321
    assert quota.total_calls == 7
    assert quota.calls_by_endpoint["videos.list"] == 4
