"""Integration tests for the API endpoints."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import sam.api.dependencies as deps
import sam.api.main as api
import sam.api.routes.alerts as alerts_mod
from sam.collectors.base import collected_post_key
from sam.config import get_settings
from sam.processors.sentiment import SentimentResult


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
    monkeypatch.setattr(deps, "get_session", _fake_get_session)
    monkeypatch.setattr(deps, "get_redis", lambda: _DummyRedis())
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

        monkeypatch.setattr(deps, "get_recent_alerts", mock_get_alerts)
        monkeypatch.setattr(deps, "get_unacknowledged_count", mock_get_unack)

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

        monkeypatch.setattr(deps, "get_alert_counts_by_severity", mock_get_counts)
        monkeypatch.setattr(deps, "get_unacknowledged_count", mock_get_unack)

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
            deps.AlertManager,
            "run_detection_cycle",
            AsyncMock(return_value=(1, [created_alert])),
        )
        mock_broadcast = AsyncMock()
        monkeypatch.setattr(alerts_mod, "broadcast_alert", mock_broadcast)

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
        monkeypatch.setattr(deps, "get_pipeline_health_stats", mock_get_stats)

        async def mock_cache_get(_key):
            return None

        async def mock_cache_set(_key, _value, ttl_seconds=None):
            del ttl_seconds
            pass

        monkeypatch.setattr(deps, "cache_get_json", mock_cache_get)
        monkeypatch.setattr(deps, "cache_set_json", mock_cache_set)

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

        monkeypatch.setattr(deps, "get_trending_by_attention_index", mock_get_trending)

        async def mock_cache_get(_key):
            return None

        async def mock_cache_set(_key, _value, ttl_seconds=None):
            del ttl_seconds
            pass

        monkeypatch.setattr(deps, "cache_get_json", mock_cache_get)
        monkeypatch.setattr(deps, "cache_set_json", mock_cache_set)

        response = client.get(
            "/api/v1/metrics/trending",
            params={"window_hours": 24, "limit": 10},
        )
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "window_hours" in data

    def test_trending_metrics_surfaces_approximation_flags(self, client, monkeypatch) -> None:
        async def mock_get_trending(*_args, **_kwargs):
            title = SimpleNamespace(
                id="title-1",
                tmdb_id=42,
                title="Dune",
                media_type="movie",
                release_date=None,
                popularity=1.0,
                is_active=True,
            )
            metrics = SimpleNamespace(
                title_id="title-1",
                snapshot_time=datetime.now(UTC),
                window_hours=24,
                mention_count=10000,
                unique_authors=5000,
                reddit_mentions=100,
                youtube_mentions=9800,
                bluesky_mentions=100,
                mention_velocity=100.0,
                velocity_change=5.0,
                avg_sentiment=0.2,
                sentiment_volatility=0.1,
                positive_ratio=0.6,
                negative_ratio=0.2,
                attention_index=42.0,
                hype_acceleration=1.0,
                raw_metrics={"mentions_capped": True, "mentions_fetch_limit": 10000},
            )
            return [(title, metrics)]

        async def mock_cache_get(_key):
            return None

        async def mock_cache_set(_key, _value, ttl_seconds=None):
            del ttl_seconds
            return None

        monkeypatch.setattr(deps, "get_trending_by_attention_index", mock_get_trending)
        monkeypatch.setattr(deps, "cache_get_json", mock_cache_get)
        monkeypatch.setattr(deps, "cache_set_json", mock_cache_set)

        response = client.get("/api/v1/metrics/trending", params={"window_hours": 24, "limit": 1})
        assert response.status_code == 200
        metrics = response.json()["items"][0]["metrics"]
        assert metrics["mentions_capped"] is True
        assert metrics["mentions_fetch_limit"] == 10000
        assert metrics["is_approximate"] is True

    def test_timeseries_requires_title_id(self, client) -> None:
        """Timeseries endpoint should require title_id."""
        response = client.get("/api/v1/metrics/timeseries")
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_collect_mentions_live_persists_extra_sentiment(monkeypatch) -> None:
    post = MagicMock()
    post.platform = "reddit"
    post.source_id = "abc123"
    post.content = "Great Dune movie"
    post.author = "user"
    post.url = "https://example.com"
    post.created_at = datetime.now(UTC)
    post.metrics = {}
    post.source_type = "post"

    collector = AsyncMock()
    collector.collect.return_value = MagicMock(
        success=True,
        posts=[post],
        collected_at=datetime.now(UTC),
    )
    monkeypatch.setattr(deps, "reddit_collector", collector)
    monkeypatch.setattr(
        deps, "resolve_title_context", AsyncMock(return_value=deps.fallback_tmdb_title("Dune"))
    )

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

    def _fake_analyze_texts(texts, *, translate, log_context="api"):
        _ = (translate, log_context)
        return [both_result] * len(texts)

    monkeypatch.setattr(deps, "analyze_texts_for_sentiment", _fake_analyze_texts)

    mentions, sentiment_by_source_id, _posts, _collected_at = await deps.collect_mentions_live(
        platform="reddit",
        title="Dune",
        limit=1,
    )

    payload = (
        sentiment_by_source_id.get("abc123") or sentiment_by_source_id[collected_post_key(post)]
    )
    assert payload["model"] == "both"
    assert "extra" in payload
    assert payload["extra"]["roberta"]["compound"] == 0.7
    assert mentions[0].source_type == "post"
    assert mentions[0].sentiment is not None
    assert "extra" in mentions[0].sentiment


@pytest.mark.asyncio
async def test_collect_mentions_live_applies_matching_and_cleanup(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("SAM_DEMO_MODE", "false")

    base_time = datetime.now(UTC)

    def _post(*, source_id: str, content: str, author: str, minutes_ago: int) -> MagicMock:
        post = MagicMock()
        post.platform = "reddit"
        post.source_id = source_id
        post.source_type = "post"
        post.content = content
        post.author = author
        post.url = f"https://example.com/{source_id}"
        post.created_at = base_time - timedelta(minutes=minutes_ago)
        post.metrics = {}
        return post

    kept = _post(
        source_id="keep",
        content="Dune trailer reactions are everywhere today",
        author="user-a",
        minutes_ago=1,
    )
    duplicate = _post(
        source_id="dup",
        content="Dune trailer reactions are everywhere today",
        author="user-b",
        minutes_ago=2,
    )
    spam = _post(
        source_id="spam",
        content=(
            "Dune FREE TRIAL!!! https://spam.example.com https://spam.example.com "
            "#dune #movie #trailer #free #promo #deal"
        ),
        author="user-c",
        minutes_ago=3,
    )
    unrelated = _post(
        source_id="other",
        content="Completely unrelated post about cooking",
        author="user-d",
        minutes_ago=4,
    )

    collector = AsyncMock()
    collector.collect.return_value = MagicMock(
        success=True,
        posts=[kept, duplicate, spam, unrelated],
        collected_at=base_time,
    )
    monkeypatch.setattr(deps, "reddit_collector", collector)
    monkeypatch.setattr(
        deps, "resolve_title_context", AsyncMock(return_value=deps.fallback_tmdb_title("Dune"))
    )

    sentiment_result = SentimentResult(
        compound=0.4,
        positive=0.6,
        negative=0.1,
        neutral=0.3,
        label="positive",
        model="vader",
        raw_scores={},
    )

    def _fake_analyze_texts(texts, *, translate, log_context="api"):
        _ = (translate, log_context)
        return [sentiment_result] * len(texts)

    monkeypatch.setattr(deps, "analyze_texts_for_sentiment", _fake_analyze_texts)

    mentions, _sentiment_by_source_id, posts, _collected_at = await deps.collect_mentions_live(
        platform="reddit",
        title="Dune",
        limit=10,
    )

    assert [post.source_id for post in posts] == ["keep"]
    assert [mention.source_id for mention in mentions] == ["keep"]
    get_settings.cache_clear()


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
        monkeypatch.setattr(deps, "analyze_sentiment", lambda _text: both)

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


class TestPipelineRunsEndpoint:
    """Tests for pipeline runs endpoint."""

    def test_pipeline_runs_returns_structure(self, client, monkeypatch) -> None:
        """Pipeline runs should return paginated response structure."""

        async def mock_get_runs(_session, *, limit, offset, status):
            del status
            return {"runs": [], "total": 0, "limit": limit, "offset": offset}

        monkeypatch.setattr(deps, "get_pipeline_runs", mock_get_runs)

        response = client.get("/api/v1/pipeline/runs")
        assert response.status_code == 200
        data = response.json()
        assert "runs" in data
        assert "total" in data
        assert data["total"] == 0
        assert isinstance(data["runs"], list)

    def test_pipeline_runs_accepts_params(self, client, monkeypatch) -> None:
        """Pipeline runs should accept limit, offset, and status params."""

        async def mock_get_runs(_session, *, limit, offset, status):
            del status
            return {"runs": [], "total": 0, "limit": limit, "offset": offset}

        monkeypatch.setattr(deps, "get_pipeline_runs", mock_get_runs)

        response = client.get(
            "/api/v1/pipeline/runs",
            params={"limit": 5, "offset": 0, "status": "success"},
        )
        assert response.status_code == 200

    def test_pipeline_runs_limit_capped_at_100(self, client) -> None:
        """Limit > 100 should be rejected."""
        response = client.get("/api/v1/pipeline/runs", params={"limit": 101})
        assert response.status_code == 422


class TestSystemHealthEndpoint:
    """Tests for system health endpoint."""

    def test_system_health_returns_structure(self, client, monkeypatch) -> None:
        """System health should return healthy flag and issues list."""
        monkeypatch.setattr(
            deps.AlertManager,
            "check_system_health",
            AsyncMock(return_value=[]),
        )

        response = client.get("/api/v1/alerts/system-health")
        assert response.status_code == 200
        data = response.json()
        assert "healthy" in data
        assert data["healthy"] is True
        assert "issues" in data
        assert isinstance(data["issues"], list)
        assert "timestamp" in data


class TestPrometheusMetrics:
    """Tests for /metrics endpoint."""

    def test_metrics_returns_text(self, client) -> None:
        """Prometheus /metrics should return text content."""
        response = client.get("/metrics")
        assert response.status_code == 200
        # Prometheus text format returns text/plain or similar
        assert "text" in response.headers.get("content-type", "")

    def test_metrics_bypasses_rate_limit(self, client) -> None:
        """/metrics should bypass rate limiting like /health."""
        for _ in range(150):
            response = client.get("/metrics")
            assert response.status_code == 200


class TestSentimentAuthProtection:
    """Tests for sentiment endpoint auth when SAM_API_KEY is set."""

    def test_sentiment_requires_auth_when_key_set(self, monkeypatch) -> None:
        """Sentiment analyze should require API key when SAM_API_KEY is configured."""
        original_settings = get_settings()
        fake_settings = original_settings.model_copy(update={"api_key": "test-key"})
        monkeypatch.setattr("sam.api.middleware.get_settings", lambda: fake_settings)

        monkeypatch.setattr(deps, "get_session", _fake_get_session)
        monkeypatch.setattr(deps, "get_redis", lambda: _DummyRedis())
        monkeypatch.setattr("sam.cache.get_redis", lambda: _DummyRedis())

        with TestClient(api.app) as c:
            response = c.get(
                "/api/v1/sentiment/analyze",
                params={"text": "Hello world"},
            )
            assert response.status_code == 401

    def test_sentiment_accessible_with_key(self, monkeypatch) -> None:
        """Sentiment analyze should succeed when correct API key is provided."""
        original_settings = get_settings()
        fake_settings = original_settings.model_copy(update={"api_key": "test-key"})
        monkeypatch.setattr("sam.api.middleware.get_settings", lambda: fake_settings)

        monkeypatch.setattr(deps, "get_session", _fake_get_session)
        monkeypatch.setattr(deps, "get_redis", lambda: _DummyRedis())
        monkeypatch.setattr("sam.cache.get_redis", lambda: _DummyRedis())

        with TestClient(api.app) as c:
            response = c.get(
                "/api/v1/sentiment/analyze",
                params={"text": "Hello world"},
                headers={"X-API-Key": "test-key"},
            )
            assert response.status_code == 200
