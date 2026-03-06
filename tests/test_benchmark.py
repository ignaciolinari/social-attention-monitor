"""Tests for benchmark endpoint."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import sam.api.dependencies as deps


class TestBenchmarkEndpoint:
    def test_requires_title_id(self, client):
        """Benchmark should require title_id parameter."""
        response = client.get("/api/v1/metrics/benchmark")
        assert response.status_code == 422

    def test_title_not_found(self, client, monkeypatch):
        """Benchmark should return 404 for unknown title."""

        async def mock_get_title(_session, _tid):
            return None

        monkeypatch.setattr(deps, "get_title_by_id", mock_get_title)

        response = client.get(
            "/api/v1/metrics/benchmark",
            params={"title_id": "00000000-0000-0000-0000-000000000001"},
        )
        assert response.status_code == 404

    def test_title_without_release_date(self, client, monkeypatch):
        """Benchmark should return 400 if title has no release date."""
        title = MagicMock()
        title.id = "00000000-0000-0000-0000-000000000001"
        title.release_date = None
        title.title = "Test"
        title.media_type = "movie"

        async def mock_get_title(_session, _tid):
            return title

        monkeypatch.setattr(deps, "get_title_by_id", mock_get_title)

        response = client.get(
            "/api/v1/metrics/benchmark",
            params={"title_id": "00000000-0000-0000-0000-000000000001"},
        )
        assert response.status_code == 400
        assert "release date" in response.json().get("detail", "").lower()

    def test_invalid_comparison_type_rejected(self, client, monkeypatch):
        """Benchmark should reject invalid comparison_type values."""
        title = MagicMock()
        title.id = "00000000-0000-0000-0000-000000000001"
        title.release_date = datetime(2025, 1, 1)
        title.title = "Test"
        title.media_type = "movie"

        async def mock_get_title(_session, _tid):
            return title

        monkeypatch.setattr(deps, "get_title_by_id", mock_get_title)

        response = client.get(
            "/api/v1/metrics/benchmark",
            params={
                "title_id": "00000000-0000-0000-0000-000000000001",
                "comparison_type": "documentary",
            },
        )

        assert response.status_code == 422

    def test_accepts_valid_params(self, client, monkeypatch):
        """Benchmark should accept all valid parameters."""
        title = MagicMock()
        title.id = "00000000-0000-0000-0000-000000000001"
        title.tmdb_id = 123
        title.title = "Test Movie"
        title.media_type = "movie"
        title.release_date = datetime(2025, 1, 1)
        title.popularity = 10.0
        title.revenue = None
        title.budget = None

        async def mock_get_title(_session, _tid):
            return title

        async def mock_timeseries(_session, *, title_id, window_hours, since, until):  # noqa: ARG001
            return []

        async def mock_avg_traj(_session, **_kwargs):
            return [{"day": 0, "avg_attention_index": 12.5, "sample_count": 2}]

        async def mock_contributors(_session, **_kwargs):
            return 2

        monkeypatch.setattr(deps, "get_title_by_id", mock_get_title)
        monkeypatch.setattr(deps, "get_metrics_timeseries", mock_timeseries)
        monkeypatch.setattr(deps, "get_average_benchmark_trajectory", mock_avg_traj)
        monkeypatch.setattr(deps, "get_benchmark_contributors_count", mock_contributors)

        response = client.get(
            "/api/v1/metrics/benchmark",
            params={
                "title_id": "00000000-0000-0000-0000-000000000001",
                "comparison_type": "movie",
                "days": 7,
                "window_hours": 24,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "title" in data
        assert "target_points" in data
        assert "avg_trajectory" in data
        assert "comparison_count" in data
        assert "comparison_titles_with_data" in data
        assert data["avg_trajectory"][0]["avg_attention_index"] == 12.5
        assert data["avg_trajectory"][1]["avg_attention_index"] is None
        assert data["comparison_titles_with_data"] == 2
