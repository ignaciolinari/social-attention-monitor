"""Shared constants, dataclasses, and utility functions for the dashboard."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import streamlit as st

if TYPE_CHECKING:
    from sam.processors.sentiment import SentimentAnalyzer, SentimentModel

# ── Platform brand colours ──────────────────────────────────────────────────
COLOR_REDDIT = "#FF4500"
COLOR_YOUTUBE = "#FF0000"
COLOR_BLUESKY = "#0085FF"

PLATFORM_COLORS = {
    "reddit_mentions": COLOR_REDDIT,
    "youtube_mentions": COLOR_YOUTUBE,
    "bluesky_mentions": COLOR_BLUESKY,
}

# ── Sentiment comparison cache policy ──────────────────────────────────────
SENTIMENT_COMPARISON_CACHE_TTL_SECONDS = 10 * 60
SENTIMENT_COMPARISON_CACHE_MAX_ENTRIES = 20

# ── Auto-refresh interval (minutes) ───────────────────────────────────────
DASHBOARD_REFRESH_MINUTES = 5

# ── YouTube quota defaults ────────────────────────────────────────────────
DEFAULT_YOUTUBE_DAILY_BUDGET = 10_000


@dataclass(frozen=True)
class TitleOption:
    """A selectable title in dashboard dropdowns."""

    id: str
    name: str
    media_type: str
    release_year: str
    tmdb_id: int | None


# ── Pure utility helpers ───────────────────────────────────────────────────


def time_range_to_hours(label: str) -> int:
    """Convert a UI time-range label to integer hours."""
    if label == "Last 24 hours":
        return 24
    if label == "Last 7 days":
        return 7 * 24
    if label == "Last 30 days":
        return 30 * 24
    return 24


def youtube_quota_reset_text() -> str:
    """Return a human-readable countdown to the YouTube quota reset (midnight PT)."""
    from zoneinfo import ZoneInfo

    pacific = ZoneInfo("America/Los_Angeles")
    now_pt = datetime.now(pacific)
    next_midnight_pt = (now_pt + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    remaining = next_midnight_pt - now_pt
    hours_left = int(remaining.total_seconds() // 3600)
    mins_left = int((remaining.total_seconds() % 3600) // 60)
    return f"Resets in {hours_left}h {mins_left}m (midnight PT)"


@st.cache_resource
def get_cached_analyzer(model: SentimentModel) -> SentimentAnalyzer:
    """Cache sentiment analyzer instances across Streamlit reruns."""
    from sam.processors.sentiment import SentimentAnalyzer

    return SentimentAnalyzer(model=model)


def get_trending_metrics(
    window_hours: int,
    limit: int = 20,
) -> dict[str, Any] | None:
    """Load trending metrics with user-friendly error handling."""
    from dashboard.api_client import get_json

    try:
        return get_json(
            "/api/v1/metrics/trending",
            params={"window_hours": window_hours, "limit": limit},
        )
    except Exception as e:
        st.error(f"Failed to load trending metrics: {e}")
        return None


def build_title_options(items: list[dict[str, Any]]) -> list[TitleOption]:
    """Build stable select options that remain unique across duplicate names."""
    options: list[TitleOption] = []
    for item in items:
        title = item.get("title")
        if not isinstance(title, dict):
            continue
        title_id_raw = title.get("id")
        title_name_raw = title.get("title")
        media_type_raw = title.get("media_type")
        if not isinstance(title_id_raw, str) or not isinstance(title_name_raw, str):
            continue
        release_date_raw = title.get("release_date")
        release_year = (
            release_date_raw[:4]
            if isinstance(release_date_raw, str) and len(release_date_raw) >= 4
            else "n/a"
        )
        tmdb_id_raw = title.get("tmdb_id")
        tmdb_id = int(tmdb_id_raw) if isinstance(tmdb_id_raw, int) else None
        options.append(
            TitleOption(
                id=title_id_raw,
                name=title_name_raw,
                media_type=str(media_type_raw) if media_type_raw is not None else "unknown",
                release_year=release_year,
                tmdb_id=tmdb_id,
            )
        )
    return options


def title_option_label(option: TitleOption) -> str:
    """Format a :class:`TitleOption` as a human-readable dropdown label."""
    tmdb_text = f"TMDB {option.tmdb_id}" if option.tmdb_id is not None else option.id[:8]
    return f"{option.name} ({option.media_type}, {option.release_year}) · {tmdb_text}"


def sentiment_comparison_cache_key(
    *,
    title_id: str,
    platform: str,
    source_filter: str,
    translate_enabled: bool,
    sarcasm_enabled: bool,
    emotion_enabled: bool,
    contents: list[str],
) -> str:
    """Build a deterministic SHA-1 cache key for sentiment comparison results."""
    digest_source = "\x1f".join(contents)
    digest = hashlib.sha1(digest_source.encode("utf-8", errors="ignore")).hexdigest()
    return (
        f"{title_id}|{platform}|source={source_filter}|translate={translate_enabled}"
        f"|sarcasm={sarcasm_enabled}|emotion={emotion_enabled}|{digest}"
    )


def prune_sentiment_comparison_cache(
    cache: dict[str, dict[str, Any]],
    *,
    now: float,
) -> None:
    """Evict expired entries and enforce LRU size bound."""
    expired_keys = [
        key
        for key, entry in cache.items()
        if now - float(entry.get("created_at", now)) > SENTIMENT_COMPARISON_CACHE_TTL_SECONDS
    ]
    for key in expired_keys:
        cache.pop(key, None)

    if len(cache) <= SENTIMENT_COMPARISON_CACHE_MAX_ENTRIES:
        return
    while len(cache) > SENTIMENT_COMPARISON_CACHE_MAX_ENTRIES:
        lru_key = min(cache.items(), key=lambda kv: float(kv[1].get("last_access", now)))[0]
        cache.pop(lru_key, None)


def num_or_zero(value: Any) -> float:
    """Safely convert a value to float; returns 0.0 for None / NaN / strings."""
    if value is None:
        return 0.0
    try:
        import pandas as _pd

        if _pd.isna(value):
            return 0.0
    except Exception:
        return 0.0
    return float(value)
