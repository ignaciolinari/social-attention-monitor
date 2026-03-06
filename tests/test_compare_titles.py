"""Tests for compare titles endpoint."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import sam.api.dependencies as deps


class TestCompareEndpoint:
    def test_requires_title_ids(self, client):
        """Compare should require title_ids parameter."""
        response = client.get("/api/v1/metrics/compare")
        assert response.status_code == 422

    def test_rejects_single_title(self, client):
        """Compare should reject less than 2 title_ids."""
        response = client.get(
            "/api/v1/metrics/compare",
            params={"title_ids": "00000000-0000-0000-0000-000000000001"},
        )
        assert response.status_code == 400
        assert "At least 2" in response.json().get("detail", "")

    def test_rejects_too_many_titles(self, client):
        """Compare should reject more than 5 title_ids."""
        ids = ",".join(f"00000000-0000-0000-0000-00000000000{i}" for i in range(6))
        response = client.get(
            "/api/v1/metrics/compare",
            params={"title_ids": ids},
        )
        assert response.status_code == 400
        assert "Maximum 5" in response.json().get("detail", "")

    def test_rejects_invalid_uuids(self, client):
        """Compare should reject invalid UUIDs."""
        response = client.get(
            "/api/v1/metrics/compare",
            params={"title_ids": "not-a-uuid,also-bad"},
        )
        assert response.status_code == 400

    def test_valid_compare_returns_structure(self, client, monkeypatch):
        """Compare with valid UUIDs should return series structure."""

        found_id = "00000000-0000-0000-0000-000000000001"

        async def mock_get_title(_session, tid):
            if str(tid) != found_id:
                return None
            title = MagicMock()
            title.id = tid
            title.tmdb_id = 123
            title.title = "Movie"
            title.original_title = "Movie"
            title.media_type = "movie"
            title.release_date = datetime(2025, 1, 1)
            title.popularity = 1.0
            title.revenue = None
            title.budget = None
            return title

        async def mock_timeseries(_session, **_kwargs):
            return []

        monkeypatch.setattr(deps, "get_title_by_id", mock_get_title)
        monkeypatch.setattr(deps, "get_metrics_timeseries", mock_timeseries)

        ids = f"{found_id},00000000-0000-0000-0000-000000000002"
        response = client.get(
            "/api/v1/metrics/compare",
            params={"title_ids": ids},
        )
        assert response.status_code == 200
        data = response.json()
        assert "series" in data
        assert isinstance(data["series"], list)
        assert "missing_ids" in data
        assert len(data["series"]) == 1
        assert len(data["missing_ids"]) == 1
