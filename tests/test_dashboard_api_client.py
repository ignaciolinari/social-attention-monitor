"""Tests for dashboard.api_client helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import dashboard.api_client as api_client


class TestDashboardApiClient:
    def test_post_json_passes_json_body(self, monkeypatch):
        captured = {}

        def fake_post(url, *, params=None, json=None, headers=None, timeout=None):
            captured.update(
                {
                    "url": url,
                    "params": params,
                    "json": json,
                    "headers": headers,
                    "timeout": timeout,
                }
            )
            response = MagicMock()
            response.json.return_value = {"ok": True}
            response.raise_for_status.return_value = None
            return response

        monkeypatch.setattr(api_client.httpx, "post", fake_post)

        result = api_client.post_json(
            "/api/v1/watchlists",
            json_body={"name": "My Watchlist", "tmdb_ids": [1396]},
        )

        assert result == {"ok": True}
        assert captured["url"].endswith("/api/v1/watchlists")
        assert captured["json"] == {"name": "My Watchlist", "tmdb_ids": [1396]}
        assert captured["params"] is None

    def test_put_json_passes_json_body(self, monkeypatch):
        captured = {}

        def fake_put(url, *, params=None, json=None, headers=None, timeout=None):
            captured.update(
                {
                    "url": url,
                    "params": params,
                    "json": json,
                    "headers": headers,
                    "timeout": timeout,
                }
            )
            response = MagicMock()
            response.json.return_value = {"ok": True}
            response.raise_for_status.return_value = None
            return response

        monkeypatch.setattr(api_client.httpx, "put", fake_put)

        result = api_client.put_json(
            "/api/v1/watchlists/123",
            json_body={"name": "Updated", "tmdb_ids": [1, 2]},
        )

        assert result == {"ok": True}
        assert captured["url"].endswith("/api/v1/watchlists/123")
        assert captured["json"] == {"name": "Updated", "tmdb_ids": [1, 2]}
        assert captured["params"] is None
