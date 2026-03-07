"""Tests for cache alert publishing helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from sam import cache


class _RedisSetFailure:
    async def set(self, _key: str, _value: str, ex: int | None = None, nx: bool = False) -> None:
        _ = (ex, nx)
        raise RuntimeError("redis write failed")

    async def delete(self, _key: str) -> int:
        return 1


class _RedisThrottleHit:
    async def set(self, _key: str, _value: str, ex: int | None = None, nx: bool = False) -> bool:
        _ = (ex, nx)
        return False

    async def delete(self, _key: str) -> int:
        return 1


@pytest.mark.asyncio
async def test_publish_system_health_throttled_does_not_double_publish(monkeypatch) -> None:
    publish = AsyncMock(return_value=True)

    monkeypatch.setattr(cache, "get_redis", lambda: _RedisSetFailure())
    monkeypatch.setattr(cache, "publish_alert_event", publish)

    result = await cache.publish_system_health_throttled({"alert_type": "collector_failure"})

    assert result is True
    publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_system_health_throttled_skips_when_throttle_key_exists(monkeypatch) -> None:
    publish = AsyncMock(return_value=True)

    monkeypatch.setattr(cache, "get_redis", lambda: _RedisThrottleHit())
    monkeypatch.setattr(cache, "publish_alert_event", publish)

    result = await cache.publish_system_health_throttled({"alert_type": "collector_failure"})

    assert result is False
    publish.assert_not_awaited()
