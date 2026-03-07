"""Shared time helpers for pipeline ingestion and snapshotting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

DEFAULT_SNAPSHOT_BUCKET_MINUTES = 30


def normalize_utc_datetime(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def floor_time_bucket(
    value: datetime,
    *,
    bucket_minutes: int = DEFAULT_SNAPSHOT_BUCKET_MINUTES,
) -> datetime:
    """Floor a datetime to the start of a fixed-size UTC bucket."""
    if bucket_minutes <= 0:
        raise ValueError("bucket_minutes must be positive")

    normalized = normalize_utc_datetime(value)
    bucket_seconds = bucket_minutes * 60
    floored_epoch = int(normalized.timestamp()) // bucket_seconds * bucket_seconds
    return datetime.fromtimestamp(floored_epoch, tz=UTC)


def is_fresh_timestamp(
    value: datetime | None,
    *,
    max_age: timedelta,
    now: datetime | None = None,
) -> bool:
    """Return True when ``value`` is not older than ``max_age``."""
    if value is None:
        return False
    reference = normalize_utc_datetime(now or datetime.now(UTC))
    return normalize_utc_datetime(value) >= reference - max_age
