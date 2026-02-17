from __future__ import annotations

from contextlib import asynccontextmanager

import respx
from fastapi.testclient import TestClient

import sam.api.main as api


class _DummySession:
    async def execute(self, *_args, **_kwargs):
        return None


class _DummyRedis:
    async def ping(self) -> bool:
        return True

    async def get(self, _key: str) -> None:
        return None

    async def set(self, _key: str, _value: str, ex: int | None = None) -> None:
        del ex
        pass


@asynccontextmanager
async def _fake_get_session():
    yield _DummySession()


def test_health_includes_config_and_ok_flags(monkeypatch) -> None:
    # Override API key env vars with empty strings so pydantic-settings doesn't
    # pick up real values from .env.  monkeypatch.setenv takes priority over
    # file-based sources.
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

    api.get_settings.cache_clear()
    api.settings = api.get_settings()

    monkeypatch.setattr(api, "get_session", _fake_get_session)
    monkeypatch.setattr(api, "get_redis", lambda: _DummyRedis())
    monkeypatch.setattr("sam.cache.get_redis", lambda: _DummyRedis())

    with TestClient(api.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"
    assert payload["database_ok"] is True
    assert payload["redis_ok"] is True
    assert payload["reddit_configured"] is False
    assert payload["youtube_configured"] is False
    assert payload["tmdb_configured"] is False
    assert payload["bluesky_configured"] is False
    # Enabled reflects the toggle state (independent of API keys)
    assert "reddit_enabled" in payload
    assert "youtube_enabled" in payload
    assert "bluesky_enabled" in payload


@respx.mock
def test_health_external_checks(monkeypatch) -> None:
    api.get_settings.cache_clear()
    monkeypatch.setenv("TMDB_API_KEY", "test-key")
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    monkeypatch.setenv("BLUESKY_IDENTIFIER", "test.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "test-password")
    monkeypatch.setattr(api, "get_redis", lambda: _DummyRedis())
    monkeypatch.setattr("sam.cache.get_redis", lambda: _DummyRedis())
    monkeypatch.setattr(api, "get_session", _fake_get_session)
    api.settings = api.get_settings()

    respx.get("https://api.themoviedb.org/3/configuration").respond(200, json={})
    respx.get("https://www.googleapis.com/youtube/v3/videos").respond(200, json={"items": []})
    respx.get("https://public.api.bsky.app/xrpc/app.bsky.actor.searchActors").respond(
        200, json={"actors": []}
    )

    with TestClient(api.app) as client:
        response = client.get("/health", params={"external": "true"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["tmdb_reachable"] is True
    assert payload["youtube_reachable"] is True
    assert payload["bluesky_reachable"] is True
