"""Tests for configurable cache TTL settings."""

from __future__ import annotations


def test_cache_ttl_defaults(monkeypatch) -> None:
    """Test that cache TTLs have sensible defaults."""
    monkeypatch.delenv("SAM_CACHE_TTL_TRENDING", raising=False)
    monkeypatch.delenv("SAM_CACHE_TTL_SEARCH", raising=False)
    monkeypatch.delenv("SAM_CACHE_TTL_METRICS", raising=False)
    monkeypatch.delenv("SAM_CACHE_TTL_PIPELINE_HEALTH", raising=False)

    from sam.config import get_settings

    get_settings.cache_clear()

    settings = get_settings()

    assert settings.cache_ttl_trending == 300
    assert settings.cache_ttl_search == 300
    assert settings.cache_ttl_metrics == 60
    assert settings.cache_ttl_pipeline_health == 30


def test_cache_ttl_from_env(monkeypatch) -> None:
    """Test that cache TTLs can be configured via environment."""
    monkeypatch.setenv("SAM_CACHE_TTL_TRENDING", "600")
    monkeypatch.setenv("SAM_CACHE_TTL_SEARCH", "120")
    monkeypatch.setenv("SAM_CACHE_TTL_METRICS", "30")
    monkeypatch.setenv("SAM_CACHE_TTL_PIPELINE_HEALTH", "10")

    from sam.config import get_settings

    get_settings.cache_clear()

    settings = get_settings()

    assert settings.cache_ttl_trending == 600
    assert settings.cache_ttl_search == 120
    assert settings.cache_ttl_metrics == 30
    assert settings.cache_ttl_pipeline_health == 10


def test_ws_cleanup_interval_default(monkeypatch) -> None:
    """Test WebSocket cleanup interval default."""
    monkeypatch.delenv("SAM_WS_CLEANUP_INTERVAL", raising=False)

    from sam.config import get_settings

    get_settings.cache_clear()

    settings = get_settings()

    assert settings.ws_cleanup_interval_seconds == 60


def test_ws_cleanup_interval_from_env(monkeypatch) -> None:
    """Test WebSocket cleanup interval from environment."""
    monkeypatch.setenv("SAM_WS_CLEANUP_INTERVAL", "120")

    from sam.config import get_settings

    get_settings.cache_clear()

    settings = get_settings()

    assert settings.ws_cleanup_interval_seconds == 120
