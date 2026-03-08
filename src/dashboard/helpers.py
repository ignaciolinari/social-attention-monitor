"""Shared constants, dataclasses, and utility functions for the dashboard."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

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
TITLE_MEDIA_TYPE_OPTIONS = {
    "All types": "all",
    "Movies": "movie",
    "TV": "tv",
}
TITLE_STATUS_OPTIONS = {
    "Active only": "active",
    "All statuses": "all",
    "Inactive only": "inactive",
}


@dataclass(frozen=True)
class TitleOption:
    """A selectable title in dashboard dropdowns."""

    id: str
    name: str
    media_type: str
    release_year: str
    tmdb_id: int | None
    is_active: bool = True
    is_trending: bool = False


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


def metrics_snapshot_is_approximate(snapshot: dict[str, Any] | None) -> bool:
    """Return whether a metrics snapshot is known to be approximate."""
    if not isinstance(snapshot, dict):
        return False
    if isinstance(snapshot.get("is_approximate"), bool):
        return snapshot["is_approximate"]
    raw_metrics = snapshot.get("raw_metrics")
    return isinstance(raw_metrics, dict) and bool(raw_metrics.get("mentions_capped"))


def render_metrics_approximation_notice(
    items_or_points: list[dict[str, Any]],
    *,
    label: str,
    st_module: Any = st,
) -> None:
    """Show a warning when a metrics view contains capped snapshots."""
    approximate_count = 0
    for item in items_or_points:
        snapshot = item.get("metrics") if isinstance(item.get("metrics"), dict) else item
        if isinstance(snapshot, dict) and metrics_snapshot_is_approximate(snapshot):
            approximate_count += 1

    if approximate_count == 0:
        return

    noun = "snapshot" if approximate_count == 1 else "snapshots"
    st_module.warning(
        f"{label} includes {approximate_count} approximate {noun} because the current "
        "metrics pipeline caps each window at 10,000 mentions. Counts, rankings, and "
        "share-of-voice may be understated for high-volume titles."
    )


def build_title_options(items: list[dict[str, Any]]) -> list[TitleOption]:
    """Build stable select options that remain unique across duplicate names."""
    options: list[TitleOption] = []
    for item in items:
        title: Any = item.get("title") if isinstance(item.get("title"), dict) else item
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
        is_active_raw = title.get("is_active")
        is_trending_raw = title.get("is_trending")
        options.append(
            TitleOption(
                id=title_id_raw,
                name=title_name_raw,
                media_type=str(media_type_raw) if media_type_raw is not None else "unknown",
                release_year=release_year,
                tmdb_id=tmdb_id,
                is_active=is_active_raw is not False,
                is_trending=bool(is_trending_raw),
            )
        )
    return options


def title_option_label(option: TitleOption) -> str:
    """Format a :class:`TitleOption` as a human-readable dropdown label."""
    trending_prefix = "🔥 " if option.is_trending else ""
    tmdb_text = f"TMDB {option.tmdb_id}" if option.tmdb_id is not None else option.id[:8]
    status_text = "" if option.is_active else " · inactive"
    return f"{trending_prefix}{option.name} ({option.media_type}, {option.release_year}) · {tmdb_text}{status_text}"


def get_trending_title_options(
    *,
    window_hours: int,
    limit: int = 50,
) -> list[TitleOption]:
    """Return current trending titles as :class:`TitleOption` entries."""
    trending = get_trending_metrics(window_hours=window_hours, limit=min(limit, 50))
    options = build_title_options(trending.get("items", [])) if trending else []
    return [replace(option, is_trending=True) for option in options]


def get_db_titles(
    *,
    query: str | None = None,
    include_inactive: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any] | None:
    """Load titles directly from the DB listing endpoint."""
    from dashboard.api_client import get_json

    params: dict[str, Any] = {
        "include_inactive": include_inactive,
        "limit": limit,
        "offset": offset,
    }
    normalized_query = (query or "").strip()
    if normalized_query:
        params["q"] = normalized_query
    try:
        return get_json("/api/v1/db/titles", params=params)
    except Exception as e:
        st.error(f"Failed to load DB titles: {e}")
        return None


def _merge_title_options(*option_groups: list[TitleOption]) -> list[TitleOption]:
    """Merge title option groups while preserving first-seen ordering."""
    merged: dict[str, TitleOption] = {}
    for group in option_groups:
        for option in group:
            merged.setdefault(option.id, option)
    return list(merged.values())


def _mark_trending_titles(
    options: list[TitleOption],
    *,
    trending_ids: set[str],
) -> list[TitleOption]:
    """Copy title options and mark currently trending entries."""
    return [
        replace(option, is_trending=option.is_trending or option.id in trending_ids)
        for option in options
    ]


def _matches_title_option(
    option: TitleOption,
    *,
    query: str,
    media_type_filter: str,
    status_filter: str,
    trending_only: bool,
) -> bool:
    """Return whether an option matches the current picker filters."""
    normalized_query = query.strip().lower()
    if normalized_query and normalized_query not in option.name.lower():
        return False
    if media_type_filter != "all" and option.media_type != media_type_filter:
        return False
    if status_filter == "active" and not option.is_active:
        return False
    if status_filter == "inactive" and option.is_active:
        return False
    return not (trending_only and not option.is_trending)


def load_dashboard_title_options(
    *,
    window_hours: int,
    query: str | None = None,
    status_filter: str = "active",
    media_type_filter: str = "all",
    trending_only: bool = False,
    limit: int = 200,
) -> list[TitleOption]:
    """Return title options for dashboard pickers.

    Without a search term, the list prioritizes the current trending titles and then
    appends more DB-backed titles so pages are no longer restricted to the trending set.
    """
    normalized_query = (query or "").strip()
    trending_options = get_trending_title_options(window_hours=window_hours, limit=50)
    trending_ids = {option.id for option in trending_options}
    db_result = get_db_titles(
        query=normalized_query or None,
        include_inactive=status_filter != "active",
        limit=limit,
    )
    db_options = (
        _mark_trending_titles(
            build_title_options(db_result.get("titles", [])), trending_ids=trending_ids
        )
        if db_result
        else []
    )
    combined = _merge_title_options(trending_options, db_options)
    return [
        option
        for option in combined
        if _matches_title_option(
            option,
            query=normalized_query,
            media_type_filter=media_type_filter,
            status_filter=status_filter,
            trending_only=trending_only,
        )
    ]


def render_title_picker(
    *,
    label: str,
    key_prefix: str,
    window_hours: int,
    st_module: Any | None = None,
    format_func: Callable[[TitleOption], str] = title_option_label,
    fallback_options_loader: Callable[[], list[TitleOption]] | None = None,
) -> TitleOption | None:
    """Render a single-title picker backed by the DB title list."""
    ui = st if st_module is None else st_module
    if not hasattr(ui, "text_input") or not hasattr(ui, "checkbox"):
        if fallback_options_loader is None:
            if hasattr(ui, "info"):
                ui.info("No titles available yet. Populate the DB first.")
            return None
        options = fallback_options_loader()
        if not options:
            if hasattr(ui, "info"):
                ui.info("No titles available yet. Populate the DB first.")
            return None
        return cast(
            TitleOption,
            ui.selectbox(
                label,
                options,
                format_func=format_func,
                key=f"{key_prefix}_title_select",
            ),
        )

    search_col, media_col, status_col, trending_col = ui.columns([3, 1, 1, 1])
    with search_col:
        query = ui.text_input(
            "Find title in DB",
            key=f"{key_prefix}_title_query",
            placeholder="Search any title stored in the database",
        )
    with media_col:
        media_type_filter_label = ui.selectbox(
            "Type",
            list(TITLE_MEDIA_TYPE_OPTIONS.keys()),
            key=f"{key_prefix}_media_type_filter",
        )
    with status_col:
        status_filter_label = ui.selectbox(
            "Status",
            list(TITLE_STATUS_OPTIONS.keys()),
            key=f"{key_prefix}_status_filter",
        )
    with trending_col:
        trending_only = ui.checkbox(
            "Trending only",
            value=False,
            key=f"{key_prefix}_trending_only",
            help="Limit the picker to titles currently in the trending set.",
        )

    options = load_dashboard_title_options(
        window_hours=window_hours,
        query=query,
        status_filter=TITLE_STATUS_OPTIONS[status_filter_label],
        media_type_filter=TITLE_MEDIA_TYPE_OPTIONS[media_type_filter_label],
        trending_only=trending_only,
    )
    if not options:
        if query.strip():
            ui.info(f"No DB titles matched `{query.strip()}`.")
        else:
            ui.info("No titles available yet. Populate the DB first.")
        return None

    if query.strip():
        ui.caption(
            f"Showing {len(options)} matching title(s). `🔥` marks currently trending titles."
        )
    else:
        ui.caption(
            "Showing current trending titles first, plus more titles from the database. `🔥` marks trending titles."
        )

    session_state = getattr(ui, "session_state", None)
    if session_state is not None:
        pending_key = f"{key_prefix}_pending_title_id"
        pending_title_id = session_state.pop(pending_key, None)
        if isinstance(pending_title_id, str):
            for option in options:
                if option.id == pending_title_id:
                    session_state[f"{key_prefix}_title_select"] = option
                    break

    return cast(
        TitleOption,
        ui.selectbox(
            label,
            options,
            format_func=format_func,
            key=f"{key_prefix}_title_select",
        ),
    )


def render_title_multiselect(
    *,
    label: str,
    key_prefix: str,
    window_hours: int,
    default_count: int = 2,
    max_selections: int = 5,
    st_module: Any | None = None,
    format_func: Callable[[TitleOption], str] = title_option_label,
    fallback_options_loader: Callable[[], list[TitleOption]] | None = None,
) -> list[TitleOption]:
    """Render a multi-title picker backed by the DB title list."""
    ui = st if st_module is None else st_module
    if not hasattr(ui, "text_input") or not hasattr(ui, "checkbox"):
        if fallback_options_loader is None:
            if hasattr(ui, "info"):
                ui.info("No titles available yet. Populate the DB first.")
            return []
        options = fallback_options_loader()
        if not options:
            if hasattr(ui, "info"):
                ui.info("No titles available yet. Populate the DB first.")
            return []
        default_options = options[: min(default_count, len(options))]
        return cast(
            list[TitleOption],
            ui.multiselect(
                label,
                options,
                default=default_options,
                format_func=format_func,
                max_selections=max_selections,
                key=f"{key_prefix}_titles_select",
            ),
        )

    search_col, media_col, status_col, trending_col = ui.columns([3, 1, 1, 1])
    with search_col:
        query = ui.text_input(
            "Find titles in DB",
            key=f"{key_prefix}_titles_query",
            placeholder="Search any titles stored in the database",
        )
    with media_col:
        media_type_filter_label = ui.selectbox(
            "Type",
            list(TITLE_MEDIA_TYPE_OPTIONS.keys()),
            key=f"{key_prefix}_titles_media_type_filter",
        )
    with status_col:
        status_filter_label = ui.selectbox(
            "Status",
            list(TITLE_STATUS_OPTIONS.keys()),
            key=f"{key_prefix}_titles_status_filter",
        )
    with trending_col:
        trending_only = ui.checkbox(
            "Trending only",
            value=False,
            key=f"{key_prefix}_titles_trending_only",
            help="Limit the picker to titles currently in the trending set.",
        )

    options = load_dashboard_title_options(
        window_hours=window_hours,
        query=query,
        status_filter=TITLE_STATUS_OPTIONS[status_filter_label],
        media_type_filter=TITLE_MEDIA_TYPE_OPTIONS[media_type_filter_label],
        trending_only=trending_only,
    )
    if not options:
        if query.strip():
            ui.info(f"No DB titles matched `{query.strip()}`.")
        else:
            ui.info("No titles available yet. Populate the DB first.")
        return []

    if query.strip():
        ui.caption(
            f"Showing {len(options)} matching title(s). `🔥` marks currently trending titles."
        )
    else:
        ui.caption(
            "Showing current trending titles first, plus more titles from the database. `🔥` marks trending titles."
        )

    default_options = options[: min(default_count, len(options))]
    return cast(
        list[TitleOption],
        ui.multiselect(
            label,
            options,
            default=default_options,
            format_func=format_func,
            max_selections=max_selections,
            key=f"{key_prefix}_titles_select",
        ),
    )


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
