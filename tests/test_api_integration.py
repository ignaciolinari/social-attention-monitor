"""Integration tests for the API endpoints."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import sam.api.main as api
from sam.processors.sentiment import SentimentBatchTranslationStats, SentimentResult


class _DummySession:
    """Dummy session for testing."""

    async def execute(self, *_args, **_kwargs):
        return MagicMock(scalar_one=lambda: 1, scalars=lambda: MagicMock(all=lambda: []))


class _DummyRedis:
    """Dummy Redis for testing."""

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


@pytest.fixture
def client(monkeypatch):
    """Create a test client with mocked dependencies."""
    monkeypatch.setattr(api, "get_session", _fake_get_session)
    monkeypatch.setattr(api, "get_redis", lambda: _DummyRedis())
    monkeypatch.setattr("sam.cache.get_redis", lambda: _DummyRedis())

    with TestClient(api.app) as c:
        yield c


class TestHealthEndpoints:
    """Tests for health check endpoints."""

    def test_health_returns_200(self, client) -> None:
        """Health endpoint should return 200."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert "timestamp" in data

    def test_health_includes_component_status(self, client) -> None:
        """Health should include database and redis status."""
        response = client.get("/health")
        data = response.json()
        assert "database_ok" in data
        assert "redis_ok" in data


class TestRateLimiting:
    """Tests for rate limiting middleware."""

    def test_rate_limit_headers_present(self, client) -> None:
        """Responses should include rate limit headers."""
        response = client.get("/api/v1/db/titles")
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers

    def test_health_bypasses_rate_limit(self, client) -> None:
        """Health endpoints should bypass rate limiting."""
        # Make many requests to health endpoint
        for _ in range(150):
            response = client.get("/health")
            assert response.status_code == 200


class TestTitlesEndpoints:
    """Tests for titles endpoints."""

    def test_list_titles_returns_empty_list(self, client) -> None:
        """List titles should return empty list when no titles."""
        response = client.get("/api/v1/db/titles")
        assert response.status_code == 200
        data = response.json()
        assert "titles" in data
        assert isinstance(data["titles"], list)

    def test_list_titles_accepts_query_param(self, client) -> None:
        """List titles should accept query parameter."""
        response = client.get("/api/v1/db/titles", params={"q": "test"})
        assert response.status_code == 200

    def test_list_titles_accepts_pagination(self, client) -> None:
        """List titles should accept limit and offset."""
        response = client.get("/api/v1/db/titles", params={"limit": 10, "offset": 0})
        assert response.status_code == 200


class TestErrorHandling:
    """Tests for error handling."""

    def test_404_returns_error_schema(self, client) -> None:
        """404 errors should use consistent error schema."""
        response = client.get("/api/v1/nonexistent")
        assert response.status_code == 404
        data = response.json()
        assert "error" in data

    def test_validation_error_returns_422(self, client) -> None:
        """Validation errors should return 422."""
        response = client.get("/api/v1/db/titles", params={"limit": -1})
        assert response.status_code == 422
        data = response.json()
        assert "error" in data


class TestAlertsEndpoints:
    """Tests for alerts endpoints."""

    def test_list_alerts_returns_structure(self, client, monkeypatch) -> None:
        """List alerts should return proper structure."""

        # Mock the alert functions
        async def mock_get_alerts(*_args, **_kwargs):
            return []

        async def mock_get_unack(*_args, **_kwargs):
            return 0

        monkeypatch.setattr(api, "get_recent_alerts", mock_get_alerts)
        monkeypatch.setattr(api, "get_unacknowledged_count", mock_get_unack)

        response = client.get("/api/v1/alerts")
        assert response.status_code == 200
        data = response.json()
        assert "alerts" in data
        assert "total_count" in data
        assert "unacknowledged_count" in data

    def test_alert_counts_returns_structure(self, client, monkeypatch) -> None:
        """Alert counts should return proper structure."""

        async def mock_get_counts(*_args, **_kwargs):
            return {"info": 1, "warning": 2, "critical": 0}

        async def mock_get_unack(*_args, **_kwargs):
            return 2

        monkeypatch.setattr(api, "get_alert_counts_by_severity", mock_get_counts)
        monkeypatch.setattr(api, "get_unacknowledged_count", mock_get_unack)

        response = client.get("/api/v1/alerts/counts")
        assert response.status_code == 200
        data = response.json()
        assert "counts" in data
        assert "total" in data
        assert "unacknowledged" in data

    def test_run_detection_broadcasts_alerts(self, client, monkeypatch) -> None:
        """Run detection should broadcast newly created alerts."""
        created_alert = {
            "id": "123",
            "title_id": "abc",
            "alert_type": "mention_spike",
            "severity": "warning",
            "message": "Test alert",
            "details": {"z_score": 3.2},
            "created_at": datetime.now(UTC).isoformat(),
            "acknowledged_at": None,
        }

        monkeypatch.setattr(
            api.AlertManager,
            "run_detection_cycle",
            AsyncMock(return_value=(1, [created_alert])),
        )
        mock_broadcast = AsyncMock()
        monkeypatch.setattr(api, "broadcast_alert", mock_broadcast)

        response = client.post("/api/v1/alerts/run-detection")
        assert response.status_code == 200
        data = response.json()
        assert data["alerts_created"] == 1
        mock_broadcast.assert_awaited_once_with(created_alert)


class TestWebSocketStatus:
    """Tests for WebSocket status endpoint."""

    def test_ws_status_returns_connection_count(self, client) -> None:
        """WebSocket status should return connection count."""
        response = client.get("/api/v1/ws/status")
        assert response.status_code == 200
        data = response.json()
        assert "active_connections" in data
        assert data["active_connections"] >= 0


class TestPipelineHealth:
    """Tests for pipeline health endpoint."""

    def test_pipeline_health_returns_structure(self, client, monkeypatch) -> None:
        """Pipeline health should return proper structure."""

        async def mock_get_stats(_session):
            return {
                "timestamp": datetime.now(UTC).isoformat(),
                "active_titles": 5,
                "total_mentions": 100,
                "mentions_last_24h": {"reddit": 50, "youtube": 50},
                "newest_mention_age_seconds": {"reddit": 300.0, "youtube": 600.0},
                "newest_mention_at": {
                    "reddit": datetime.now(UTC).isoformat(),
                    "youtube": datetime.now(UTC).isoformat(),
                },
                "latest_pipeline_runs": [],
            }

        # Mock both the stats function and cache
        monkeypatch.setattr(api, "get_pipeline_health_stats", mock_get_stats)

        async def mock_cache_get(_key):
            return None

        async def mock_cache_set(_key, _value, ttl_seconds=None):
            del ttl_seconds
            pass

        monkeypatch.setattr(api, "cache_get_json", mock_cache_get)
        monkeypatch.setattr(api, "cache_set_json", mock_cache_set)

        response = client.get("/api/v1/pipeline/health")
        assert response.status_code == 200
        data = response.json()
        assert "active_titles" in data
        assert "total_mentions" in data
        assert "mentions_last_24h" in data
        assert "newest_mention_age_seconds" in data


class TestMetricsEndpoints:
    """Tests for metrics endpoints."""

    def test_trending_metrics_accepts_params(self, client, monkeypatch) -> None:
        """Trending metrics should accept window_hours and limit."""

        async def mock_get_trending(*_args, **_kwargs):
            return []

        monkeypatch.setattr(api, "get_trending_by_attention_index", mock_get_trending)

        async def mock_cache_get(_key):
            return None

        async def mock_cache_set(_key, _value, ttl_seconds=None):
            del ttl_seconds
            pass

        monkeypatch.setattr(api, "cache_get_json", mock_cache_get)
        monkeypatch.setattr(api, "cache_set_json", mock_cache_set)

        response = client.get(
            "/api/v1/metrics/trending",
            params={"window_hours": 24, "limit": 10},
        )
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "window_hours" in data

    def test_timeseries_requires_title_id(self, client) -> None:
        """Timeseries endpoint should require title_id."""
        response = client.get("/api/v1/metrics/timeseries")
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_collect_mentions_live_persists_extra_sentiment(monkeypatch) -> None:
    post = MagicMock()
    post.platform = "reddit"
    post.source_id = "abc123"
    post.content = "Great movie"
    post.author = "user"
    post.url = "https://example.com"
    post.created_at = datetime.now(UTC)
    post.metrics = {}

    collector = AsyncMock()
    collector.collect.return_value = MagicMock(
        success=True,
        posts=[post],
        collected_at=datetime.now(UTC),
    )
    monkeypatch.setattr(api, "_reddit_collector", collector)

    roberta_result = SentimentResult(
        compound=0.7,
        positive=0.8,
        negative=0.1,
        neutral=0.1,
        label="positive",
        model="roberta",
        raw_scores={},
    )
    both_result = SentimentResult(
        compound=0.4,
        positive=0.6,
        negative=0.2,
        neutral=0.2,
        label="positive",
        model="both",
        raw_scores={},
        extra={"roberta": roberta_result},
    )

    def _fake_batch_with_translation(_texts, *, translate, log_context="api"):
        _ = (translate, log_context)
        return [both_result], SentimentBatchTranslationStats()

    monkeypatch.setattr(
        api, "analyze_sentiment_batch_with_translation", _fake_batch_with_translation
    )

    mentions, sentiment_by_source_id, _posts, _collected_at = await api._collect_mentions_live(
        platform="reddit",
        title="Dune",
        limit=1,
    )

    payload = sentiment_by_source_id["abc123"]
    assert payload["model"] == "both"
    assert "extra" in payload
    assert payload["extra"]["roberta"]["compound"] == 0.7
    assert mentions[0].sentiment is not None
    assert "extra" in mentions[0].sentiment


class TestSentimentEndpoint:
    """Tests for sentiment analysis endpoint."""

    def test_analyze_sentiment_returns_result(self, client) -> None:
        """Sentiment analysis should return sentiment scores."""
        response = client.get(
            "/api/v1/sentiment/analyze",
            params={"text": "This movie is amazing!"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "sentiment" in data
        assert "compound" in data["sentiment"]
        assert "label" in data["sentiment"]

    def test_analyze_sentiment_includes_extra_payload(self, client, monkeypatch) -> None:
        """Sentiment endpoint should include secondary model data when present."""
        roberta = SentimentResult(
            compound=0.8,
            positive=0.9,
            negative=0.05,
            neutral=0.05,
            label="positive",
            model="roberta",
            raw_scores={},
        )
        both = SentimentResult(
            compound=0.3,
            positive=0.6,
            negative=0.3,
            neutral=0.1,
            label="positive",
            model="both",
            raw_scores={},
            extra={"roberta": roberta},
        )
        monkeypatch.setattr(api, "analyze_sentiment", lambda _text: both)

        response = client.get(
            "/api/v1/sentiment/analyze",
            params={"text": "This movie is amazing!"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "both"
        assert "extra" in data["sentiment"]
        assert data["sentiment"]["extra"]["roberta"]["model"] == "roberta"

    def test_analyze_sentiment_requires_text(self, client) -> None:
        """Sentiment analysis should require text parameter."""
        response = client.get("/api/v1/sentiment/analyze")
        assert response.status_code == 422
