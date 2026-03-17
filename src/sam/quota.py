"""API quota tracking.

Lightweight in-process counters that track estimated API usage for
YouTube Data API v3 (and extensible for TMDB/Reddit later).

Quota costs are based on YouTube Data API v3 documentation:
  - search.list  = 100 units
  - videos.list  =   1 unit
  - Daily budget = 10,000 units (default project quota)

The tracker resets its in-memory counters at midnight Pacific Time (PT),
matching YouTube's real quota reset boundary.

Usage is read by the runner and persisted in pipeline_run stats, so we get
history even though the counter itself is in-memory.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from zoneinfo import ZoneInfo

from loguru import logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# YouTube Data API v3 costs (units per call)
YOUTUBE_SEARCH_COST = 100
YOUTUBE_VIDEOS_COST = 1
YOUTUBE_COMMENT_THREADS_COST = 1
YOUTUBE_DAILY_BUDGET = 10_000


@dataclass
class _PlatformUsage:
    """Accumulated usage for one platform within a single YouTube quota day (PT)."""

    date: str  # ISO date "YYYY-MM-DD"
    calls: dict[str, int] = field(default_factory=dict)  # endpoint -> call count
    units: int = 0


class QuotaTracker:
    """Thread-safe API quota tracker with automatic daily reset."""

    _YOUTUBE_RESET_TZ = ZoneInfo("America/Los_Angeles")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._platforms: dict[str, _PlatformUsage] = {}

    def _today(self) -> str:
        # YouTube quota resets at midnight Pacific Time.
        return datetime.now(self._YOUTUBE_RESET_TZ).strftime("%Y-%m-%d")

    def _get_platform(self, platform: str) -> _PlatformUsage:
        """Get or create today's usage for a platform, resetting if the day changed."""
        today = self._today()
        usage = self._platforms.get(platform)
        if usage is None or usage.date != today:
            if usage is not None and usage.date != today:
                logger.info(
                    f"[quota] {platform} daily reset "
                    f"(previous day {usage.date}: {usage.units} units in {sum(usage.calls.values())} calls)"
                )
            usage = _PlatformUsage(date=today)
            self._platforms[platform] = usage
        return usage

    def seed(
        self,
        platform: str,
        units: int,
        calls_by_endpoint: dict[str, int] | None = None,
    ) -> None:
        """Pre-load usage from persisted data (e.g. previous pipeline runs today).

        This is additive: if the tracker already has usage for today it is
        merged, so callers can safely call ``seed()`` multiple times.
        """
        if units <= 0 and not calls_by_endpoint:
            return
        with self._lock:
            usage = self._get_platform(platform)
            usage.units += units
            for ep, cnt in (calls_by_endpoint or {}).items():
                usage.calls[ep] = usage.calls.get(ep, 0) + cnt
            logger.info(f"[quota] Seeded {platform} with {units} units (total now: {usage.units})")

    def replace(
        self,
        platform: str,
        units: int,
        calls_by_endpoint: dict[str, int] | None = None,
    ) -> None:
        """Overwrite today's usage for a platform."""
        with self._lock:
            usage = self._get_platform(platform)
            usage.units = max(0, units)
            usage.calls = {
                str(ep): max(0, int(cnt or 0)) for ep, cnt in (calls_by_endpoint or {}).items()
            }

    def record(self, platform: str, endpoint: str, units: int) -> None:
        """Record an API call and its quota cost."""
        with self._lock:
            usage = self._get_platform(platform)
            usage.calls[endpoint] = usage.calls.get(endpoint, 0) + 1
            usage.units += units

    def get_usage(self, platform: str) -> dict[str, Any]:
        """Get current usage summary for a platform."""
        with self._lock:
            usage = self._get_platform(platform)
            return {
                "date": usage.date,
                "total_units": usage.units,
                "total_calls": sum(usage.calls.values()),
                "calls_by_endpoint": dict(usage.calls),
            }

    def get_all_usage(self) -> dict[str, dict[str, object]]:
        """Get usage for all tracked platforms."""
        with self._lock:
            result: dict[str, dict[str, object]] = {}
            for platform in list(self._platforms):
                usage = self._get_platform(platform)
                result[platform] = {
                    "date": usage.date,
                    "total_units": usage.units,
                    "total_calls": sum(usage.calls.values()),
                    "calls_by_endpoint": dict(usage.calls),
                }
            return result

    def has_budget(self, platform: str, cost: int, daily_budget: int) -> bool:
        """Return True if spending ``cost`` units would stay within ``daily_budget``."""
        with self._lock:
            usage = self._get_platform(platform)
            return (usage.units + cost) <= daily_budget

    def youtube_has_budget(self, cost: int = YOUTUBE_SEARCH_COST) -> bool:
        """Convenience: check if a YouTube API call of ``cost`` units fits the daily budget."""
        return self.has_budget("youtube", cost, YOUTUBE_DAILY_BUDGET)

    def get_youtube_summary(self) -> dict[str, object]:
        """Convenience: YouTube-specific summary with budget info."""
        usage = self.get_usage("youtube")
        total_units = usage["total_units"]
        return {
            **usage,
            "daily_budget": YOUTUBE_DAILY_BUDGET,
            "budget_used_pct": round(total_units / YOUTUBE_DAILY_BUDGET * 100, 1)
            if isinstance(total_units, int)
            else 0,
            "budget_remaining": YOUTUBE_DAILY_BUDGET - total_units
            if isinstance(total_units, int)
            else YOUTUBE_DAILY_BUDGET,
        }


# Module-level singleton
_tracker: QuotaTracker | None = None
_tracker_lock = threading.Lock()


def get_quota_tracker() -> QuotaTracker:
    """Get or create the global quota tracker (thread-safe)."""
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                _tracker = QuotaTracker()
    return _tracker


_REDIS_QUOTA_PREFIX = "sam:quota"


def _youtube_quota_today() -> str:
    return datetime.now(QuotaTracker._YOUTUBE_RESET_TZ).strftime("%Y-%m-%d")


def _shared_quota_key(platform: str, *, day: str | None = None) -> str:
    normalized_day = day or _youtube_quota_today()
    return f"{_REDIS_QUOTA_PREFIX}:{platform}:{normalized_day}"


async def _get_shared_usage(
    platform: str,
) -> dict[str, Any] | None:
    from sam.cache import get_redis

    r = get_redis()
    if r is None:
        return None

    key = _shared_quota_key(platform)
    try:
        raw = await cast(Any, r).hgetall(key)
    except Exception:
        return None

    if not raw:
        return {
            "date": _youtube_quota_today(),
            "total_units": 0,
            "total_calls": 0,
            "calls_by_endpoint": {},
        }

    calls_by_endpoint = {
        field[3:]: int(value or 0) for field, value in raw.items() if field.startswith("ep:")
    }
    total_calls_raw = raw.get("total_calls")
    total_calls = (
        int(total_calls_raw or 0)
        if total_calls_raw is not None
        else sum(calls_by_endpoint.values())
    )
    return {
        "date": raw.get("date", _youtube_quota_today()),
        "total_units": int(raw.get("total_units", 0) or 0),
        "total_calls": total_calls,
        "calls_by_endpoint": calls_by_endpoint,
    }


async def _sync_shared_usage(
    platform: str,
    *,
    units: int,
    calls_by_endpoint: dict[str, int] | None = None,
) -> None:
    from sam.cache import get_redis

    r = get_redis()
    if r is None:
        return

    key = _shared_quota_key(platform)
    today = _youtube_quota_today()
    calls = {str(ep): max(0, int(cnt or 0)) for ep, cnt in (calls_by_endpoint or {}).items()}
    payload: dict[str, str | int] = {
        "date": today,
        "total_units": max(0, units),
        "total_calls": sum(calls.values()),
    }
    payload.update({f"ep:{ep}": count for ep, count in calls.items()})
    try:
        pipe = r.pipeline(transaction=True)
        pipe.delete(key)
        pipe.hset(key, mapping=payload)
        pipe.expire(key, 172800)
        await pipe.execute()
    except Exception:
        return


async def youtube_has_budget(cost: int = YOUTUBE_SEARCH_COST) -> bool:
    """Check YouTube budget using shared state when available."""
    shared = await _get_shared_usage("youtube")
    if shared is not None:
        total_units = shared.get("total_units", 0)
        if isinstance(total_units, int):
            return (total_units + cost) <= YOUTUBE_DAILY_BUDGET

    tracker = get_quota_tracker()
    return tracker.youtube_has_budget(cost=cost)


async def record_youtube_usage(endpoint: str, units: int) -> None:
    """Record YouTube quota usage in shared state when available."""
    if units <= 0:
        return

    from sam.cache import get_redis

    r = get_redis()
    if r is not None:
        key = _shared_quota_key("youtube")
        try:
            pipe = r.pipeline(transaction=True)
            pipe.hset(key, mapping={"date": _youtube_quota_today()})
            pipe.hincrby(key, "total_units", units)
            pipe.hincrby(key, "total_calls", 1)
            pipe.hincrby(key, f"ep:{endpoint}", 1)
            pipe.expire(key, 172800)
            await pipe.execute()
            return
        except Exception:
            pass

    tracker = get_quota_tracker()
    tracker.record("youtube", endpoint, units)


@dataclass
class YouTubeDailyQuota:
    """Aggregated YouTube quota usage from today's pipeline runs."""

    date: str
    total_units: int
    total_calls: int
    calls_by_endpoint: dict[str, int]
    last_run_at: str | None

    @property
    def budget_used_pct(self) -> float:
        """Percentage of the daily API quota consumed, in the range [0.0, 100.0]."""
        return (
            round(self.total_units / YOUTUBE_DAILY_BUDGET * 100, 1) if YOUTUBE_DAILY_BUDGET else 0.0
        )

    @property
    def budget_remaining(self) -> int:
        """Remaining YouTube Data API v3 units before the daily quota is exhausted."""
        return YOUTUBE_DAILY_BUDGET - self.total_units

    def to_api_dict(self) -> dict[str, object]:
        """Return a dict suitable for the /api/v1/pipeline/quota response."""
        return {
            "date": self.date,
            "total_units": self.total_units,
            "total_calls": self.total_calls,
            "calls_by_endpoint": self.calls_by_endpoint,
            "daily_budget": YOUTUBE_DAILY_BUDGET,
            "budget_used_pct": self.budget_used_pct,
            "budget_remaining": self.budget_remaining,
        }


async def aggregate_youtube_quota_from_db(
    session: AsyncSession | None = None,
) -> YouTubeDailyQuota:
    """Aggregate YouTube quota usage across all successful pipeline runs today (PT).

    Newer runner versions persist **per-run deltas** in ``pipeline_runs.stats.api_quota``
    (``mode='delta'``). Older runs persisted **day-to-date cumulative** totals.

    Aggregation rules:
    - delta-mode runs: sum deltas
    - cumulative-mode runs: take the max as a base (to avoid double-counting)
    - if both exist in the PT day window: base(max cumulative) + sum(deltas)

    Shared between ``seed_quota_from_db`` and the ``/api/v1/pipeline/quota``
    endpoint to avoid duplicating the aggregation logic.
    """
    from sqlalchemy import select

    from sam.storage.database import get_session
    from sam.storage.models import PipelineRun

    pacific = ZoneInfo("America/Los_Angeles")
    now_pt = datetime.now(pacific)
    today_pt = now_pt.strftime("%Y-%m-%d")
    today_utc = datetime.now(UTC).strftime("%Y-%m-%d")
    today_start_pt = now_pt.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start_pt.astimezone(UTC)

    last_run_at: str | None = None

    base_units = 0
    base_calls = 0
    base_calls_by_endpoint: dict[str, int] = {}

    delta_units = 0
    delta_calls = 0
    delta_calls_by_endpoint: dict[str, int] = {}

    async def _aggregate(db_session: AsyncSession) -> None:
        nonlocal last_run_at, base_units, base_calls, base_calls_by_endpoint
        nonlocal delta_units, delta_calls, delta_calls_by_endpoint

        result = await db_session.execute(
            select(PipelineRun)
            .where(
                PipelineRun.started_at >= today_start_utc,
            )
            .order_by(PipelineRun.started_at.desc())
        )
        runs = result.scalars().all()

        for run in runs:
            if last_run_at is None:
                last_run_at = run.started_at.isoformat()
            if run.stats and "api_quota" in run.stats:
                yt = run.stats["api_quota"].get("youtube", {})
                mode = run.stats["api_quota"].get("mode")

                # Backward compatibility: older runs persisted quota date using UTC.
                # We already filter runs to the current PT-day window, so accept either.
                yt_date = yt.get("date", "")
                if yt_date and yt_date not in {today_pt, today_utc}:
                    continue

                if mode == "delta":
                    delta_units += int(yt.get("total_units", 0) or 0)
                    delta_calls += int(yt.get("total_calls", 0) or 0)
                    for ep, cnt in (yt.get("calls_by_endpoint", {}) or {}).items():
                        delta_calls_by_endpoint[ep] = delta_calls_by_endpoint.get(ep, 0) + int(
                            cnt or 0
                        )
                else:
                    # Legacy cumulative mode: use the max as the day-to-date base.
                    candidate_units = int(yt.get("total_units", 0) or 0)
                    if candidate_units >= base_units:
                        base_units = candidate_units
                        base_calls = int(yt.get("total_calls", 0) or 0)
                        base_calls_by_endpoint = {
                            str(ep): int(cnt or 0)
                            for ep, cnt in (yt.get("calls_by_endpoint", {}) or {}).items()
                        }

    if session is not None:
        await _aggregate(session)
    else:
        async with get_session() as s:
            await _aggregate(s)
    calls_by_endpoint: dict[str, int] = dict(base_calls_by_endpoint)
    for ep, cnt in delta_calls_by_endpoint.items():
        calls_by_endpoint[ep] = calls_by_endpoint.get(ep, 0) + cnt

    return YouTubeDailyQuota(
        date=today_pt,
        total_units=base_units + delta_units,
        total_calls=base_calls + delta_calls,
        calls_by_endpoint=calls_by_endpoint,
        last_run_at=last_run_at,
    )


async def get_current_youtube_quota(
    session: AsyncSession | None = None,
) -> YouTubeDailyQuota:
    """Return the best current YouTube quota summary.

    Prefers shared Redis-backed state when available so API-driven live usage and
    collector usage are visible across processes in near real time. Falls back to
    the greater of the local in-memory tracker or persisted run history.
    """
    shared = await _get_shared_usage("youtube")
    if shared is not None:
        db_quota = await aggregate_youtube_quota_from_db(session)
        return YouTubeDailyQuota(
            date=str(shared.get("date", _youtube_quota_today())),
            total_units=int(shared.get("total_units", 0) or 0),
            total_calls=int(shared.get("total_calls", 0) or 0),
            calls_by_endpoint={
                str(ep): int(cnt or 0)
                for ep, cnt in (shared.get("calls_by_endpoint", {}) or {}).items()
            },
            last_run_at=db_quota.last_run_at,
        )

    db_quota = await aggregate_youtube_quota_from_db(session)
    tracker_usage = get_quota_tracker().get_usage("youtube")
    tracker_units = tracker_usage.get("total_units", 0)
    if isinstance(tracker_units, int) and tracker_units > db_quota.total_units:
        calls_by_endpoint = tracker_usage.get("calls_by_endpoint", {})
        return YouTubeDailyQuota(
            date=str(tracker_usage.get("date", db_quota.date)),
            total_units=tracker_units,
            total_calls=int(tracker_usage.get("total_calls", 0) or 0),
            calls_by_endpoint={
                str(ep): int(cnt or 0)
                for ep, cnt in (
                    calls_by_endpoint if isinstance(calls_by_endpoint, dict) else {}
                ).items()
            },
            last_run_at=db_quota.last_run_at,
        )
    return db_quota


async def seed_quota_from_db() -> None:
    """Load today's accumulated YouTube quota from persisted pipeline runs
    and seed the in-memory tracker.

    This must be called at runner startup (before any collection) so that
    the quota guard (``youtube_has_budget``) correctly accounts for units
    already spent by previous ``--once`` invocations today.
    """
    try:
        quota = await aggregate_youtube_quota_from_db()
    except Exception as exc:
        logger.warning(f"[quota] Failed to seed YouTube quota from DB: {exc}")
        return

    tracker = get_quota_tracker()
    tracker.replace("youtube", quota.total_units, quota.calls_by_endpoint)
    shared = await _get_shared_usage("youtube")
    shared_units = shared.get("total_units", 0) if isinstance(shared, dict) else 0
    if not isinstance(shared_units, int) or shared_units < quota.total_units:
        await _sync_shared_usage(
            "youtube",
            units=quota.total_units,
            calls_by_endpoint=quota.calls_by_endpoint,
        )

    if quota.total_units > 0:
        logger.info(f"[quota] Loaded {quota.total_units} YouTube units from DB today")
    else:
        logger.debug("[quota] No prior YouTube usage found for today")
