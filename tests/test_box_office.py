"""Tests for box office correlation endpoint."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import sam.api.dependencies as deps


class TestBoxOfficeEndpoint:
    def test_box_office_returns_structure(self, client, monkeypatch):
        """Box office endpoint should return items list."""

        async def mock_trending(*_a, **_kw):
            return []

        monkeypatch.setattr(deps, "get_trending_by_attention_index", mock_trending)

        async def mock_cache_get(_k):
            return None

        async def mock_cache_set(_k, _v, ttl_seconds=None):
            pass

        monkeypatch.setattr(deps, "cache_get_json", mock_cache_get)
        monkeypatch.setattr(deps, "cache_set_json", mock_cache_set)

        response = client.get("/api/v1/metrics/box-office")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "window_hours" in data
        assert isinstance(data["items"], list)

    def test_box_office_accepts_params(self, client, monkeypatch):
        """Box office should accept window_hours and limit."""

        async def mock_trending(*_a, **_kw):
            return []

        monkeypatch.setattr(deps, "get_trending_by_attention_index", mock_trending)

        async def mock_cache_get(_k):
            return None

        async def mock_cache_set(_k, _v, ttl_seconds=None):
            pass

        monkeypatch.setattr(deps, "cache_get_json", mock_cache_get)
        monkeypatch.setattr(deps, "cache_set_json", mock_cache_set)

        response = client.get(
            "/api/v1/metrics/box-office",
            params={"window_hours": 48, "limit": 5},
        )
        assert response.status_code == 200

    def test_box_office_item_shape(self, client, monkeypatch):
        """Box office response items should include expected fields."""

        title = MagicMock()
        title.id = "00000000-0000-0000-0000-000000000001"
        title.title = "Movie"
        title.media_type = "movie"
        title.release_date = datetime(2025, 1, 1)
        title.revenue = 100
        title.budget = 50

        metrics = MagicMock()
        metrics.attention_index = 12.3
        metrics.mention_count = 44
        metrics.avg_sentiment = 0.2

        async def mock_trending(*_a, **_kw):
            return [(title, metrics)]

        async def mock_cache_get(_k):
            return None

        async def mock_cache_set(_k, _v, ttl_seconds=None):
            pass

        monkeypatch.setattr(deps, "get_trending_by_attention_index", mock_trending)
        monkeypatch.setattr(deps, "cache_get_json", mock_cache_get)
        monkeypatch.setattr(deps, "cache_set_json", mock_cache_set)

        response = client.get("/api/v1/metrics/box-office")
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert {
            "title_id",
            "title",
            "media_type",
            "release_date",
            "revenue",
            "budget",
            "attention_index",
            "mention_count",
            "avg_sentiment",
        } <= set(item)
