"""Tests for collector status and toggle API endpoints."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

import sam.api.dependencies as deps
import sam.api.main as api
import sam.cache as cache


class _DummySession:
    async def execute(self, *_args, **_kwargs):
        return None


class _DummyRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        del ex
        self.store[key] = value
        return True


@asynccontextmanager
async def _fake_get_session():
    yield _DummySession()


def _setup(monkeypatch) -> None:
    """Common test setup: blank API keys, dummy session/redis."""
    for key in (
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "YOUTUBE_API_KEY",
        "TMDB_API_KEY",
        "TMDB_ACCESS_TOKEN",
        "TMDB_BEARER_TOKEN",
        "BLUESKY_IDENTIFIER",
        "BLUESKY_APP_PASSWORD",
    ):
        monkeypatch.setenv(key, "")

    # Ensure enabled defaults
    monkeypatch.setenv("REDDIT_ENABLED", "false")
    monkeypatch.setenv("YOUTUBE_ENABLED", "true")
    monkeypatch.setenv("BLUESKY_ENABLED", "true")

    api.get_settings.cache_clear()
    api.settings = api.get_settings()

    redis = _DummyRedis()
    monkeypatch.setattr(deps, "get_session", _fake_get_session)
    monkeypatch.setattr(deps, "get_redis", lambda: redis)
    monkeypatch.setattr(cache, "_redis", None)
    monkeypatch.setattr(cache, "get_redis", lambda: redis)

    # Reset runtime overrides between tests
    deps.collector_enabled_overrides.clear()


def test_collectors_status_defaults(monkeypatch) -> None:
    """GET /api/v1/collectors/status returns all 3 platforms with correct defaults."""
    _setup(monkeypatch)

    with TestClient(api.app) as client:
        resp = client.get("/api/v1/collectors/status")

    assert resp.status_code == 200
    data = resp.json()
    collectors = {c["platform"]: c for c in data["collectors"]}

    assert set(collectors.keys()) == {"reddit", "youtube", "bluesky"}

    # Reddit: disabled by default, not toggleable from dashboard
    assert collectors["reddit"]["enabled"] is False
    assert collectors["reddit"]["toggleable"] is False
    assert collectors["reddit"]["message"] is not None

    # YouTube: enabled by default, toggleable
    assert collectors["youtube"]["enabled"] is True
    assert collectors["youtube"]["toggleable"] is True

    # Bluesky: enabled by default, toggleable
    assert collectors["bluesky"]["enabled"] is True
    assert collectors["bluesky"]["toggleable"] is True


def test_toggle_youtube(monkeypatch) -> None:
    """PUT /api/v1/collectors/youtube/toggle works."""
    _setup(monkeypatch)

    with TestClient(api.app) as client:
        # Disable
        resp = client.put(
            "/api/v1/collectors/youtube/toggle",
            params={"enabled": "false"},
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        assert resp.json()["platform"] == "youtube"

        # Re-enable
        resp = client.put(
            "/api/v1/collectors/youtube/toggle",
            params={"enabled": "true"},
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True


def test_toggle_bluesky(monkeypatch) -> None:
    """PUT /api/v1/collectors/bluesky/toggle works."""
    _setup(monkeypatch)

    with TestClient(api.app) as client:
        resp = client.put(
            "/api/v1/collectors/bluesky/toggle",
            params={"enabled": "false"},
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        assert resp.json()["platform"] == "bluesky"


def test_toggle_reddit_forbidden(monkeypatch) -> None:
    """PUT /api/v1/collectors/reddit/toggle returns 403."""
    _setup(monkeypatch)

    with TestClient(api.app) as client:
        resp = client.put(
            "/api/v1/collectors/reddit/toggle",
            params={"enabled": "true"},
        )
        assert resp.status_code == 403


def test_toggle_unknown_platform(monkeypatch) -> None:
    """PUT /api/v1/collectors/tiktok/toggle returns 404."""
    _setup(monkeypatch)

    with TestClient(api.app) as client:
        resp = client.put(
            "/api/v1/collectors/tiktok/toggle",
            params={"enabled": "true"},
        )
        assert resp.status_code == 404


def test_toggle_reflects_in_status(monkeypatch) -> None:
    """After toggling YouTube off, status endpoint reflects it."""
    _setup(monkeypatch)

    with TestClient(api.app) as client:
        client.put(
            "/api/v1/collectors/youtube/toggle",
            params={"enabled": "false"},
        )
        resp = client.get("/api/v1/collectors/status")

    assert resp.status_code == 200
    collectors = {c["platform"]: c for c in resp.json()["collectors"]}
    assert collectors["youtube"]["enabled"] is False


def test_toggle_requires_redis(monkeypatch) -> None:
    _setup(monkeypatch)
    monkeypatch.setattr(cache, "get_redis", lambda: None)

    with TestClient(api.app) as client:
        resp = client.put(
            "/api/v1/collectors/youtube/toggle",
            params={"enabled": "false"},
        )

    assert resp.status_code == 503
