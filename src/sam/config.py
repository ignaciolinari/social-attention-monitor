"""
SAM Configuration

Centralized configuration using pydantic-settings.
All settings are loaded from environment variables with sensible defaults.
"""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = ".env"
_ENV_FILE_ENCODING = "utf-8"

# Keep "configured" checks honest when users copy `.env.example` without filling values.
_PLACEHOLDER_VALUES = {
    "your_client_id_here",
    "your_client_secret_here",
    "your_youtube_api_key_here",
    "your_tmdb_api_key_here",
    "your_tmdb_access_token_here",
    "your_handle.bsky.social",
    "your_app_password_here",
}


def _is_effectively_set(value: str) -> bool:
    v = (value or "").strip()
    if not v:
        return False
    return v.lower() not in _PLACEHOLDER_VALUES


class RedditSettings(BaseSettings):
    """Reddit API configuration."""

    model_config = SettingsConfigDict(
        env_prefix="REDDIT_",
        env_file=_ENV_FILE,
        env_file_encoding=_ENV_FILE_ENCODING,
        extra="ignore",
    )

    enabled: bool = Field(
        default=False,
        description="Enable Reddit collector (set to true when you have valid API keys)",
    )
    client_id: str = Field(default="", description="Reddit OAuth client ID")
    client_secret: str = Field(default="", description="Reddit OAuth client secret")
    user_agent: str = Field(
        default="SAM/0.2.0 (Social Attention Monitor)",
        description="User agent for Reddit API requests",
    )

    @property
    def has_credentials(self) -> bool:
        """Check if Reddit credentials are set (regardless of enabled flag)."""
        return _is_effectively_set(self.client_id) and _is_effectively_set(self.client_secret)

    @property
    def is_configured(self) -> bool:
        """Check if Reddit is enabled and credentials are configured."""
        return self.enabled and self.has_credentials


class YouTubeSettings(BaseSettings):
    """YouTube Data API configuration."""

    model_config = SettingsConfigDict(
        env_prefix="YOUTUBE_",
        env_file=_ENV_FILE,
        env_file_encoding=_ENV_FILE_ENCODING,
        extra="ignore",
    )

    enabled: bool = Field(
        default=True,
        description="Enable YouTube collector",
    )
    api_key: str = Field(default="", description="YouTube Data API v3 key")
    search_order: str = Field(
        default="relevance",
        description="YouTube search order (relevance, date, rating, viewCount, title, videoCount)",
    )
    published_after_days: int = Field(
        default=30,
        description="Only fetch videos published in the last N days",
    )

    @field_validator("published_after_days")
    @classmethod
    def _validate_published_after_days(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("YOUTUBE_PUBLISHED_AFTER_DAYS must be > 0")
        # Guardrails: huge windows are rarely useful for polling and can increase duplicates.
        return min(int(v), 3650)

    @field_validator("search_order")
    @classmethod
    def _validate_search_order(cls, v: str) -> str:
        allowed = {"relevance", "date", "rating", "viewCount", "title", "videoCount"}
        if v not in allowed:
            raise ValueError(f"YOUTUBE_SEARCH_ORDER must be one of: {', '.join(sorted(allowed))}")
        return v

    @property
    def has_credentials(self) -> bool:
        """Check if YouTube API key is set (regardless of enabled flag)."""
        return _is_effectively_set(self.api_key)

    @property
    def is_configured(self) -> bool:
        """Check if YouTube is enabled and API key is configured."""
        return self.enabled and self.has_credentials


class TMDBSettings(BaseSettings):
    """TMDB API configuration."""

    model_config = SettingsConfigDict(
        env_prefix="TMDB_",
        env_file=_ENV_FILE,
        env_file_encoding=_ENV_FILE_ENCODING,
        extra="ignore",
    )

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
        return _is_effectively_set(self.api_key) or _is_effectively_set(self.access_token)


class BlueskySettings(BaseSettings):
    """Bluesky API configuration."""

    model_config = SettingsConfigDict(
        env_prefix="BLUESKY_",
        env_file=_ENV_FILE,
        env_file_encoding=_ENV_FILE_ENCODING,
        extra="ignore",
    )

    enabled: bool = Field(
        default=True,
        description="Enable Bluesky collector",
    )
    identifier: str = Field(default="", description="Bluesky handle (e.g., user.bsky.social)")
    app_password: str = Field(default="", description="Bluesky app password")

    @property
    def has_credentials(self) -> bool:
        """Check if Bluesky credentials are set (regardless of enabled flag)."""
        return _is_effectively_set(self.identifier) and _is_effectively_set(self.app_password)

    @property
    def is_configured(self) -> bool:
        """Check if Bluesky is enabled and credentials are configured."""
        return self.enabled and self.has_credentials


class DatabaseSettings(BaseSettings):
    """Database configuration."""

    model_config = SettingsConfigDict(
        env_prefix="DATABASE_",
        env_file=_ENV_FILE,
        env_file_encoding=_ENV_FILE_ENCODING,
        extra="ignore",
    )

    url: str = Field(
        default="postgresql+asyncpg://sam:sam@localhost:5432/sam",
        description="Async database URL",
    )
    demo_url: str = Field(
        default="",
        validation_alias=AliasChoices("DATABASE_DEMO_URL", "SAM_DEMO_DATABASE_URL"),
        description="Async database URL to use when DEMO_MODE=true (optional)",
    )
    sync_url: str = Field(
        default="postgresql://sam:sam@localhost:5432/sam",
        description="Sync database URL (for migrations)",
    )
    demo_sync_url: str = Field(
        default="",
        validation_alias=AliasChoices("DATABASE_DEMO_SYNC_URL", "SAM_DEMO_DATABASE_SYNC_URL"),
        description="Sync database URL to use when DEMO_MODE=true (optional)",
    )
    echo: bool = Field(default=False, description="Echo SQL queries")
    pool_size: int = Field(default=5, description="Connection pool size")

    def effective_url(self, *, demo_mode: bool) -> str:
        if demo_mode and _is_effectively_set(self.demo_url):
            return self.demo_url
        return self.url


class RedisSettings(BaseSettings):
    """Redis configuration."""

    model_config = SettingsConfigDict(
        env_prefix="REDIS_",
        env_file=_ENV_FILE,
        env_file_encoding=_ENV_FILE_ENCODING,
        extra="ignore",
    )

    url: str = Field(default="redis://localhost:6379/0", description="Redis URL")


class CollectorSettings(BaseSettings):
    """Data collection configuration."""

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=_ENV_FILE,
        env_file_encoding=_ENV_FILE_ENCODING,
        extra="ignore",
    )

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

    model_config = SettingsConfigDict(
        env_prefix="SAM_STORAGE_",
        env_file=_ENV_FILE,
        env_file_encoding=_ENV_FILE_ENCODING,
        extra="ignore",
    )

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
        env_file=_ENV_FILE,
        env_file_encoding=_ENV_FILE_ENCODING,
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
    sentiment_model: Literal["vader", "roberta", "both"] = Field(
        default="vader",
        validation_alias=AliasChoices("SENTIMENT_MODEL", "SAM_SENTIMENT_MODEL"),
        description="Sentiment analysis model to use (vader, roberta, or both)",
    )
    translate_before_sentiment: bool = Field(
        default=False,
        validation_alias=AliasChoices("SAM_TRANSLATE_BEFORE_SENTIMENT"),
        description="Translate non-English content to English before running sentiment analysis",
    )
    sentiment_fallback_to_vader_on_error: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "SAM_SENTIMENT_FALLBACK_TO_VADER", "SENTIMENT_FALLBACK_TO_VADER"
        ),
        description=(
            "Fallback to VADER if the configured sentiment model fails to initialize "
            "(for example, missing transformers dependencies)"
        ),
    )

    # Pipeline feature flags
    enable_youtube_comments: bool = Field(
        default=True,
        validation_alias=AliasChoices("SAM_ENABLE_YOUTUBE_COMMENTS"),
        description="Collect YouTube video comments for richer sentiment data",
    )
    youtube_comments_per_video: int = Field(
        default=30,
        validation_alias=AliasChoices("SAM_YOUTUBE_COMMENTS_PER_VIDEO"),
        description="Number of comments to collect per YouTube video",
    )
    enable_emotion_detection: bool = Field(
        default=False,
        validation_alias=AliasChoices("SAM_ENABLE_EMOTION_DETECTION"),
        description="Run emotion classification (requires transformers extra)",
    )
    enable_sarcasm_detection: bool = Field(
        default=False,
        validation_alias=AliasChoices("SAM_ENABLE_SARCASM_DETECTION"),
        description="Run sarcasm detection (requires transformers extra)",
    )
    enable_spam_filter: bool = Field(
        default=True,
        validation_alias=AliasChoices("SAM_ENABLE_SPAM_FILTER"),
        description="Filter likely spam/bot content before sentiment analysis",
    )
    enable_keyword_extraction: bool = Field(
        default=True,
        validation_alias=AliasChoices("SAM_ENABLE_KEYWORD_EXTRACTION"),
        description="Extract trending keywords from mention content",
    )
    enable_aspect_sentiment: bool = Field(
        default=False,
        validation_alias=AliasChoices("SAM_ENABLE_ASPECT_SENTIMENT"),
        description="Run aspect-based sentiment on long-form content",
    )

    @field_validator("youtube_comments_per_video")
    @classmethod
    def validate_youtube_comments_per_video(cls, v: int) -> int:
        """Keep comment collection within API-supported and safe bounds."""
        if v < 1:
            raise ValueError("youtube_comments_per_video must be >= 1")
        if v > 100:
            raise ValueError("youtube_comments_per_video must be <= 100")
        return v

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

    # Security
    api_key: str = Field(
        default="",
        validation_alias=AliasChoices("SAM_API_KEY", "API_KEY"),
        description=(
            "API key for mutation/admin endpoints. "
            "When set, PUT/POST/DELETE requests to sensitive endpoints require "
            "an Authorization: Bearer <key> or X-API-Key: <key> header."
        ),
    )

    # Nested settings
    reddit: RedditSettings = Field(default_factory=RedditSettings)
    youtube: YouTubeSettings = Field(default_factory=YouTubeSettings)
    tmdb: TMDBSettings = Field(default_factory=TMDBSettings)
    bluesky: BlueskySettings = Field(default_factory=BlueskySettings)
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
    """Return the **singleton** application settings, cached for the process lifetime.

    Uses :func:`functools.lru_cache` so that the first call constructs a
    :class:`Settings` instance (reading env‑vars / ``.env``), and every
    subsequent call returns the *same* object with zero overhead.

    Lifecycle notes
    ---------------
    * The cache lives for the duration of the Python process.  In production
      (Uvicorn with ``--workers N``) each worker gets its own copy.
    * Changing an environment variable **after** the first call has no effect
      unless you clear the cache explicitly.
    * On Unix systems, sending ``SIGHUP`` to the process calls
      :func:`reload_settings`, which clears this cache so the next call
      picks up new environment variable values.

    Testing / invalidation
    ----------------------
    To reset the cached settings in tests, call::

        get_settings.cache_clear()

    or override the FastAPI dependency with ``app.dependency_overrides``.

    Returns
    -------
    Settings
        The validated, frozen configuration object.
    """
    return Settings()


def reload_settings() -> Settings:
    """Clear the settings cache and return a fresh :class:`Settings` instance.

    Useful for hot-reloading configuration without a full process restart.
    Called automatically by the ``SIGHUP`` signal handler when
    :func:`install_sighup_handler` has been invoked.
    """
    from loguru import logger

    get_settings.cache_clear()
    new = get_settings()
    logger.info("[config] Settings reloaded via cache clear")
    return new


def install_sighup_handler() -> None:
    """Register a ``SIGHUP`` handler that reloads settings.

    Safe to call on any platform — silently no-ops on Windows where
    ``SIGHUP`` does not exist.  Also no-ops when called from a non-main
    thread (e.g. during test-suite startup).

    Should be called once during application startup (e.g. in the FastAPI
    lifespan or scheduler entrypoint).
    """
    import signal
    import sys
    import threading

    if sys.platform == "win32":
        return

    if threading.current_thread() is not threading.main_thread():
        return

    def _on_sighup(signum: int, frame: object) -> None:  # noqa: ARG001
        reload_settings()

    signal.signal(signal.SIGHUP, _on_sighup)
