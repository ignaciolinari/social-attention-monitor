"""Tests for language breakdown endpoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import sam.api.dependencies as deps


class TestLanguageBreakdownEndpoint:
    def test_requires_title_id(self, client):
        """Language breakdown should require title_id parameter."""
        response = client.get("/api/v1/metrics/language-breakdown")
        assert response.status_code == 422

    def test_title_not_found_returns_404(self, client, monkeypatch):
        """Language breakdown should return 404 for unknown titles."""

        async def mock_get_title(_session, _tid):
            return None

        monkeypatch.setattr(deps, "get_title_by_id", mock_get_title)

        response = client.get(
            "/api/v1/metrics/language-breakdown",
            params={"title_id": "00000000-0000-0000-0000-000000000001", "hours": 24},
        )

        assert response.status_code == 404

    def test_accepts_valid_params(self, client, monkeypatch):
        """Language breakdown should accept title_id and hours."""

        title = MagicMock()

        async def mock_get_title(_session, _tid):
            return title

        async def mock_cache_get(_k):
            return None

        async def mock_cache_set(_k, _v, ttl_seconds=None):
            pass

        monkeypatch.setattr(deps, "get_title_by_id", mock_get_title)
        monkeypatch.setattr(deps, "cache_get_json", mock_cache_get)
        monkeypatch.setattr(deps, "cache_set_json", mock_cache_set)

        response = client.get(
            "/api/v1/metrics/language-breakdown",
            params={
                "title_id": "00000000-0000-0000-0000-000000000001",
                "hours": 24,
            },
        )
        # Expect 200 even with empty data since session returns empty
        assert response.status_code == 200
        data = response.json()
        assert "languages" in data
        assert isinstance(data["languages"], list)

    def test_preserves_neutral_avg_sentiment(self, client, monkeypatch):
        """Language breakdown should preserve a real 0.0 average sentiment."""
        title = MagicMock()
        row = MagicMock(detected_language="en", mention_count=2, avg_sentiment=0.0)

        async def mock_get_title(_session, _tid):
            return title

        async def mock_cache_get(_k):
            return None

        async def mock_cache_set(_k, _v, ttl_seconds=None):
            pass

        class _Result:
            def all(self):
                return [row]

        class _Session:
            async def execute(self, *_a, **_kw):
                return _Result()

        @asynccontextmanager
        async def fake_session():
            yield _Session()

        monkeypatch.setattr(deps, "get_title_by_id", mock_get_title)
        monkeypatch.setattr(deps, "cache_get_json", mock_cache_get)
        monkeypatch.setattr(deps, "cache_set_json", mock_cache_set)
        monkeypatch.setattr(deps, "get_session", fake_session)

        response = client.get(
            "/api/v1/metrics/language-breakdown",
            params={"title_id": "00000000-0000-0000-0000-000000000001", "hours": 24},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["languages"][0]["avg_sentiment"] == 0.0
        assert {"language", "mention_count", "avg_sentiment"} <= set(data["languages"][0])
