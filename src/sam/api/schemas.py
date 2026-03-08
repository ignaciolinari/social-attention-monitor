"""Pydantic response/request schemas for the SAM API."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Consistent API error schema."""

    error: str
    detail: Any | None = None


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    timestamp: str
    demo_mode: bool
    reddit_configured: bool
    youtube_configured: bool
    tmdb_configured: bool
    bluesky_configured: bool
    reddit_enabled: bool = False
    youtube_enabled: bool = True
    bluesky_enabled: bool = True
    youtube_reachable: bool | None = None
    tmdb_reachable: bool | None = None
    bluesky_reachable: bool | None = None
    database_ok: bool
    redis_ok: bool


class ReadinessResponse(BaseModel):
    """Readiness probe response."""

    status: str
    timestamp: str
    database_ok: bool
    redis_ok: bool


class PipelineRunInfo(BaseModel):
    """Pipeline run information."""

    job_name: str
    status: str
    started_at: str | None
    finished_at: str | None
    error: str | None
    stats: dict[str, Any] = Field(default_factory=dict)


class PipelineSentimentStats(BaseModel):
    """Sentiment/translation observability stats from latest collector run."""

    translate_attempted: int = 0
    translate_count: int = 0
    translate_failures: int = 0
    translate_skipped_english: int = 0
    sentiment_ms_total: float = 0.0


class ApiQuotaInfo(BaseModel):
    """API quota usage for a single platform."""

    date: str
    total_units: int
    total_calls: int
    calls_by_endpoint: dict[str, int]
    daily_budget: int | None = None
    budget_used_pct: float | None = None
    budget_remaining: int | None = None


class PipelineQuotaResponse(BaseModel):
    """API quota usage summary across all platforms."""

    youtube: ApiQuotaInfo
    last_run_at: str | None = None


class PipelineHealthResponse(BaseModel):
    """Pipeline health statistics."""

    timestamp: str
    active_titles: int
    total_mentions: int
    mentions_last_24h: dict[str, int]
    newest_mention_age_seconds: dict[str, float | None]
    newest_mention_at: dict[str, str | None]
    latest_pipeline_runs: list[PipelineRunInfo]
    sentiment_stats: PipelineSentimentStats | None = None
    api_quota: dict[str, ApiQuotaInfo] = Field(default_factory=dict)


class PipelineRunDetail(BaseModel):
    """Single pipeline run record."""

    id: str
    job_name: str
    status: str
    started_at: str | None
    finished_at: str | None
    elapsed_seconds: int | None = None
    error: str | None
    stats: dict[str, Any] = Field(default_factory=dict)


class PipelineRunsResponse(BaseModel):
    """Paginated pipeline run history."""

    runs: list[PipelineRunDetail]
    total: int
    limit: int
    offset: int


class TitleResponse(BaseModel):
    """Title metadata response."""

    tmdb_id: int
    title: str
    media_type: str
    release_date: str | None
    overview: str
    popularity: float
    vote_average: float


class MentionResponse(BaseModel):
    """Social media mention response."""

    platform: str
    source_id: str
    source_type: str
    content: str
    content_truncated: bool = False
    author: str | None
    url: str | None
    created_at: str
    metrics: dict[str, Any]
    sentiment: dict[str, Any] | None


class TrendingResponse(BaseModel):
    """Trending titles response."""

    titles: list[TitleResponse]
    collected_at: str


class MentionsResponse(BaseModel):
    """Mentions collection response."""

    title: str
    platform: str
    mentions: list[MentionResponse]
    total_count: int
    next_offset: int | None = None
    collected_at: str


class DbTitleResponse(BaseModel):
    """Title from our DB."""

    id: str
    tmdb_id: int
    title: str
    media_type: str
    release_date: str | None
    popularity: float | None
    revenue: int | None = None
    budget: int | None = None
    is_active: bool = True


class TitlesResponse(BaseModel):
    titles: list[DbTitleResponse]
    total_count: int
    next_offset: int | None = None


class MetricsSnapshotResponse(BaseModel):
    title_id: str
    snapshot_time: str
    window_hours: int

    mention_count: int
    unique_authors: int
    reddit_mentions: int
    youtube_mentions: int
    bluesky_mentions: int = 0

    mention_velocity: float | None
    velocity_change: float | None

    avg_sentiment: float | None
    sentiment_volatility: float | None
    positive_ratio: float | None
    negative_ratio: float | None = None

    attention_index: float | None
    hype_acceleration: float | None
    mentions_capped: bool = False
    mentions_fetch_limit: int | None = None
    is_approximate: bool = False
    raw_metrics: dict[str, Any] | None = None


class TrendingMetricsItem(BaseModel):
    title: DbTitleResponse
    metrics: MetricsSnapshotResponse


class TrendingMetricsResponse(BaseModel):
    window_hours: int
    collected_at: str
    items: list[TrendingMetricsItem]


class MetricsTimeseriesResponse(BaseModel):
    title: DbTitleResponse
    window_hours: int
    since: str
    until: str
    points: list[MetricsSnapshotResponse]


class AlertResponse(BaseModel):
    """Alert response schema."""

    id: str
    title_id: str
    alert_type: str
    severity: str
    message: str
    details: dict[str, Any] | None
    created_at: str
    acknowledged_at: str | None


class AlertsListResponse(BaseModel):
    """List of alerts response."""

    alerts: list[AlertResponse]
    total_count: int
    next_offset: int | None = None
    unacknowledged_count_total: int
    unacknowledged_count: int


class AlertCountsResponse(BaseModel):
    """Alert counts by severity."""

    counts: dict[str, int]
    total: int
    unacknowledged: int
    unacknowledged_in_window: int


class AlertAckResponse(BaseModel):
    """Alert acknowledgment response."""

    acknowledged: bool
    alert_id: str


class CollectorPlatformStatus(BaseModel):
    """Status of a single collector platform."""

    platform: str
    enabled: bool
    api_configured: bool
    toggleable: bool = True
    message: str | None = None


class CollectorStatusResponse(BaseModel):
    """Status of all collector platforms."""

    collectors: list[CollectorPlatformStatus]


# ── Watchlist schemas ────────────────────────────────────────────────────────


class WatchlistCreateRequest(BaseModel):
    """Request to create a watchlist."""

    name: str = Field(..., min_length=1, max_length=200)
    tmdb_ids: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list)


class WatchlistResponse(BaseModel):
    """Single watchlist response."""

    id: str
    name: str
    tmdb_ids: list[int]
    created_at: str
    updated_at: str


class WatchlistsListResponse(BaseModel):
    """List of watchlists response."""

    watchlists: list[WatchlistResponse]
    total_count: int


# ── Box Office schemas ──────────────────────────────────────────────────────


class BoxOfficeItem(BaseModel):
    """Single box-office correlation item."""

    title_id: str
    title: str
    media_type: str
    release_date: str | None
    revenue: int | None
    budget: int | None
    attention_index: float | None
    mention_count: int
    avg_sentiment: float | None


class BoxOfficeResponse(BaseModel):
    """Box-office correlation response."""

    window_hours: int
    collected_at: str
    items: list[BoxOfficeItem]


# ── Language Breakdown schemas ──────────────────────────────────────────────


class LanguageItem(BaseModel):
    """Per-language mention aggregate."""

    language: str
    mention_count: int
    avg_sentiment: float | None


class LanguageBreakdownResponse(BaseModel):
    """Language breakdown response."""

    title_id: str
    hours: int
    collected_at: str
    languages: list[LanguageItem]


# ── Compare schemas ────────────────────────────────────────────────────────


class CompareSeries(BaseModel):
    """Single title's timeseries in a compare response."""

    title: DbTitleResponse
    points: list[MetricsSnapshotResponse]


class CompareResponse(BaseModel):
    """Compare titles response."""

    window_hours: int
    since: str
    until: str
    series: list[CompareSeries]
    missing_ids: list[str] = Field(default_factory=list)


# ── Benchmark schemas ──────────────────────────────────────────────────────


class BenchmarkDayPoint(BaseModel):
    """Single day in the average trajectory."""

    day: int
    avg_attention_index: float | None
    sample_count: int


class BenchmarkResponse(BaseModel):
    """Historical benchmark response."""

    title: DbTitleResponse
    days: int
    window_hours: int
    target_points: list[MetricsSnapshotResponse]
    avg_trajectory: list[BenchmarkDayPoint]
    comparison_count: int
    comparison_titles_with_data: int
