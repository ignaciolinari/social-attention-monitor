"""
SAM Configuration

Centralized configuration using pydantic-settings.
All settings are loaded from environment variables with sensible defaults.
"""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RedditSettings(BaseSettings):
    """Reddit API configuration."""

    model_config = SettingsConfigDict(env_prefix="REDDIT_")

    client_id: str = Field(default="", description="Reddit OAuth client ID")
    client_secret: str = Field(default="", description="Reddit OAuth client secret")
    user_agent: str = Field(
        default="SAM/0.1.0 (Social Attention Monitor)",
        description="User agent for Reddit API requests",
    )

    @property
    def is_configured(self) -> bool:
        """Check if Reddit credentials are configured."""
        return bool(self.client_id and self.client_secret)


class YouTubeSettings(BaseSettings):
    """YouTube Data API configuration."""

    model_config = SettingsConfigDict(env_prefix="YOUTUBE_")

    api_key: str = Field(default="", description="YouTube Data API v3 key")

    @property
    def is_configured(self) -> bool:
        """Check if YouTube API is configured."""
        return bool(self.api_key)


class TMDBSettings(BaseSettings):
    """TMDB API configuration."""

    model_config = SettingsConfigDict(env_prefix="TMDB_")

    api_key: str = Field(default="", description="TMDB API key (v3) for query param auth")
    access_token: str = Field(
        default="",
        validation_alias=AliasChoices("TMDB_ACCESS_TOKEN", "TMDB_BEARER_TOKEN"),
        description="TMDB API access token (v4) for bearer auth",
    )
    base_url: str = Field(default="https://api.themoviedb.org/3")

    @property
    def is_configured(self) -> bool:
        """Check if TMDB API is configured."""
        return bool(self.api_key or self.access_token)


class DatabaseSettings(BaseSettings):
    """Database configuration."""

    model_config = SettingsConfigDict(env_prefix="DATABASE_")

    url: str = Field(
        default="postgresql+asyncpg://sam:sam@localhost:5432/sam",
        description="Async database URL",
    )
    sync_url: str = Field(
        default="postgresql://sam:sam@localhost:5432/sam",
        description="Sync database URL (for migrations)",
    )
    echo: bool = Field(default=False, description="Echo SQL queries")
    pool_size: int = Field(default=5, description="Connection pool size")


class RedisSettings(BaseSettings):
    """Redis configuration."""

    model_config = SettingsConfigDict(env_prefix="REDIS_")

    url: str = Field(default="redis://localhost:6379/0", description="Redis URL")


class CollectorSettings(BaseSettings):
    """Data collection configuration."""

    model_config = SettingsConfigDict(env_prefix="")

    polling_interval_minutes: int = Field(default=5, description="Polling interval in minutes")
    target_subreddits: str = Field(
        default="movies,television,netflix,DisneyPlus,amazonprime,appletv",
        description="Comma-separated list of subreddits to monitor",
    )
    max_posts_per_subreddit: int = Field(
        default=100, description="Maximum posts to fetch per subreddit per poll"
    )

    @property
    def subreddit_list(self) -> list[str]:
        """Parse subreddits into a list."""
        return [s.strip() for s in self.target_subreddits.split(",") if s.strip()]


class StorageSettings(BaseSettings):
    """Local filesystem storage settings (raw data, artifacts)."""

    model_config = SettingsConfigDict(env_prefix="SAM_STORAGE_")

    enable_raw_data_storage: bool = Field(
        default=False,
        description="Persist collected raw data to local filesystem",
    )
    raw_data_dir: str = Field(
        default="data/raw",
        description="Directory for raw data dumps (JSONL)",
    )


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Environment
    sam_env: Literal["development", "staging", "production"] = Field(
        default="development", description="Application environment"
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO", description="Logging level"
    )
    log_json: bool = Field(
        default=False,
        validation_alias=AliasChoices("LOG_JSON", "SAM_LOG_JSON"),
        description="Emit structured JSON logs",
    )

    # Demo mode
    demo_mode: bool = Field(
        default=True,
        validation_alias=AliasChoices("DEMO_MODE", "SAM_DEMO_MODE"),
        description="Use demo/mock data instead of live APIs",
    )

    # CORS
    cors_allow_origins: str = Field(
        default="*",
        validation_alias=AliasChoices("CORS_ALLOW_ORIGINS"),
        description="Comma-separated CORS allowlist (use '*' only for development)",
    )

    @property
    def cors_allow_origins_list(self) -> list[str]:
        """Parse CORS origins into a list."""
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    # API Server
    api_host: str = Field(default="0.0.0.0", description="API server host")
    api_port: int = Field(default=8000, description="API server port")

    # Dashboard
    dashboard_port: int = Field(default=8501, description="Streamlit dashboard port")
    dashboard_http_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices("SAM_DASHBOARD_HTTP_TIMEOUT"),
        description="Dashboard HTTP timeout in seconds",
    )

    # API behavior
    mentions_refresh_stale_minutes: int = Field(
        default=30,
        description="Minutes after which mentions data is considered stale",
    )

    # Cache TTLs (seconds)
    cache_ttl_trending: int = Field(
        default=300,
        validation_alias=AliasChoices("SAM_CACHE_TTL_TRENDING"),
        description="Cache TTL for trending endpoints (seconds)",
    )
    cache_ttl_search: int = Field(
        default=300,
        validation_alias=AliasChoices("SAM_CACHE_TTL_SEARCH"),
        description="Cache TTL for search endpoints (seconds)",
    )
    cache_ttl_metrics: int = Field(
        default=60,
        validation_alias=AliasChoices("SAM_CACHE_TTL_METRICS"),
        description="Cache TTL for metrics endpoints (seconds)",
    )
    cache_ttl_pipeline_health: int = Field(
        default=30,
        validation_alias=AliasChoices("SAM_CACHE_TTL_PIPELINE_HEALTH"),
        description="Cache TTL for pipeline health endpoint (seconds)",
    )

    # WebSocket settings
    ws_cleanup_interval_seconds: int = Field(
        default=60,
        validation_alias=AliasChoices("SAM_WS_CLEANUP_INTERVAL"),
        description="Interval for WebSocket dead connection cleanup (seconds)",
    )

    # Nested settings
    reddit: RedditSettings = Field(default_factory=RedditSettings)
    youtube: YouTubeSettings = Field(default_factory=YouTubeSettings)
    tmdb: TMDBSettings = Field(default_factory=TMDBSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    collector: CollectorSettings = Field(default_factory=CollectorSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)

    @field_validator("sam_env", mode="before")
    @classmethod
    def lowercase_env(cls, v: str) -> str:
        """Ensure environment is lowercase."""
        return v.lower() if isinstance(v, str) else v

    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.sam_env == "development"

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.sam_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
