"""
SAM Dashboard

Streamlit-based dashboard for visualizing social attention data.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from sam.config import get_settings

if TYPE_CHECKING:
    from sam.processors.sentiment import SentimentAnalyzer, SentimentModel

st.set_page_config(
    page_title="SAM - Social Attention Monitor",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Auto-refresh every 5 minutes so new collector data is picked up promptly.
# The refresh itself is lightweight (API re-fetch); only the collector is expensive.
_DASHBOARD_REFRESH_MINUTES = 5
st_autorefresh(interval=_DASHBOARD_REFRESH_MINUTES * 60 * 1000, key="data_refresh")

# Platform brand colours
COLOR_REDDIT = "#FF4500"  # Reddit orange
COLOR_YOUTUBE = "#FF0000"  # YouTube red
COLOR_BLUESKY = "#0085FF"  # Bluesky blue

_PLATFORM_COLORS = {
    "reddit_mentions": COLOR_REDDIT,
    "youtube_mentions": COLOR_YOUTUBE,
    "bluesky_mentions": COLOR_BLUESKY,
}

_SENTIMENT_COMPARISON_CACHE_TTL_SECONDS = 10 * 60
_SENTIMENT_COMPARISON_CACHE_MAX_ENTRIES = 20


@dataclass(frozen=True)
class _TitleOption:
    id: str
    name: str
    media_type: str
    release_year: str
    tmdb_id: int | None


def _api_base_url() -> str:
    # Allow explicit override
    override = os.getenv("SAM_API_BASE_URL")
    if override:
        return override.rstrip("/")

    host = os.getenv("API_HOST", "127.0.0.1")
    port = os.getenv("API_PORT", "8000")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    return f"http://{host}:{port}"


@st.cache_data(ttl=30)
def _get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = _api_base_url() + path
    timeout_s = get_settings().dashboard_http_timeout_seconds
    r = httpx.get(url, params=params, timeout=timeout_s)
    r.raise_for_status()
    data: Any = r.json()
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object from API")
    return data


def _get_json_nocache(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Like _get_json but never cached (for mutable state like toggles)."""
    url = _api_base_url() + path
    timeout_s = get_settings().dashboard_http_timeout_seconds
    r = httpx.get(url, params=params, timeout=timeout_s)
    r.raise_for_status()
    data: Any = r.json()
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object from API")
    return data


def _put_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send a PUT request to the API."""
    url = _api_base_url() + path
    timeout_s = get_settings().dashboard_http_timeout_seconds
    r = httpx.put(url, params=params, timeout=timeout_s)
    r.raise_for_status()
    data: Any = r.json()
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object from API")
    return data


def _time_range_to_hours(label: str) -> int:
    if label == "Last 24 hours":
        return 24
    if label == "Last 7 days":
        return 7 * 24
    if label == "Last 30 days":
        return 30 * 24
    return 24


def _youtube_quota_reset_text() -> str:
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
def _get_cached_analyzer(model: SentimentModel) -> SentimentAnalyzer:
    """Cache sentiment analyzer instances across Streamlit reruns."""
    from sam.processors.sentiment import SentimentAnalyzer

    return SentimentAnalyzer(model=model)


def _get_trending_metrics(window_hours: int, limit: int = 20) -> dict[str, Any] | None:
    """Load trending metrics with user-friendly error handling."""
    try:
        return _get_json(
            "/api/v1/metrics/trending",
            params={"window_hours": window_hours, "limit": limit},
        )
    except Exception as e:
        st.error(f"Failed to load trending metrics: {e}")
        return None


def _build_title_options(items: list[dict[str, Any]]) -> list[_TitleOption]:
    """Build stable select options that remain unique across duplicate names."""
    options: list[_TitleOption] = []
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
            _TitleOption(
                id=title_id_raw,
                name=title_name_raw,
                media_type=str(media_type_raw) if media_type_raw is not None else "unknown",
                release_year=release_year,
                tmdb_id=tmdb_id,
            )
        )
    return options


def _title_option_label(option: _TitleOption) -> str:
    tmdb_text = f"TMDB {option.tmdb_id}" if option.tmdb_id is not None else option.id[:8]
    return f"{option.name} ({option.media_type}, {option.release_year}) · {tmdb_text}"


def _sentiment_comparison_cache_key(
    *,
    title_id: str,
    platform: str,
    translate_enabled: bool,
    contents: list[str],
) -> str:
    digest_source = "\x1f".join(contents)
    digest = hashlib.sha1(digest_source.encode("utf-8", errors="ignore")).hexdigest()
    return f"{title_id}|{platform}|translate={translate_enabled}|{digest}"


def _prune_sentiment_comparison_cache(
    cache: dict[str, dict[str, Any]],
    *,
    now: float,
) -> None:
    """Evict expired entries and enforce LRU size bound."""
    expired_keys = [
        key
        for key, entry in cache.items()
        if now - float(entry.get("created_at", now)) > _SENTIMENT_COMPARISON_CACHE_TTL_SECONDS
    ]
    for key in expired_keys:
        cache.pop(key, None)

    if len(cache) <= _SENTIMENT_COMPARISON_CACHE_MAX_ENTRIES:
        return
    while len(cache) > _SENTIMENT_COMPARISON_CACHE_MAX_ENTRIES:
        lru_key = min(cache.items(), key=lambda kv: float(kv[1].get("last_access", now)))[0]
        cache.pop(lru_key, None)


def _build_sentiment_comparison_rows(
    mentions: list[dict[str, Any]],
    *,
    translate_enabled: bool,
    vader: SentimentAnalyzer,
    roberta: SentimentAnalyzer,
) -> tuple[list[dict[str, Any]], int]:
    analyzable_mentions: list[dict[str, Any]] = []
    original_texts: list[str] = []
    for mention in mentions:
        original = (mention.get("content") or "").strip()
        if not original:
            continue
        analyzable_mentions.append(mention)
        original_texts.append(original)

    if not analyzable_mentions:
        return [], 0

    processed_texts = original_texts
    if translate_enabled:
        from sam.utils.translation import translate_batch_to_english

        processed_texts, _changed_count, _failed_count = translate_batch_to_english(original_texts)

    vader_results = vader.analyze_batch(processed_texts)
    roberta_results = roberta.analyze_batch(processed_texts)

    disagreements = 0
    rows: list[dict[str, Any]] = []
    for mention, original_text, processed_text, v_res, r_res in zip(
        analyzable_mentions,
        original_texts,
        processed_texts,
        vader_results,
        roberta_results,
        strict=True,
    ):
        if (v_res.label == "positive" and r_res.label == "negative") or (
            v_res.label == "negative" and r_res.label == "positive"
        ):
            disagreements += 1

        content_display = processed_text.replace("\n", " ")
        if processed_text != original_text:
            content_display = f"🌐 {content_display}"

        rows.append(
            {
                "Platform": mention.get("platform"),
                "Content": content_display,
                "VADER": f"{v_res.compound:.2f} ({v_res.label})",
                "RoBERTa": f"{r_res.compound:.2f} ({r_res.label})",
                "Diff": abs(v_res.compound - r_res.compound),
                "_original": original_text.replace("\n", " ")
                if processed_text != original_text
                else None,
            }
        )

    return rows, disagreements


def main() -> None:
    """Main dashboard entry point."""
    st.title("📊 Social Attention Monitor")
    st.markdown("*Real-time social attention tracking for film & TV releases*")

    # Sidebar
    with st.sidebar:
        st.header("🎯 Navigation")
        page = st.radio(
            "Select View",
            [
                "🔥 Trending Now",
                "📈 Time Series",
                "🔄 Platform Comparison",
                "💬 Sentiment",
                "⚖️ Sentiment Comparison",
                "🚨 Alerts",
                "📡 API Quota",
            ],
            label_visibility="collapsed",
        )

        st.divider()
        st.header("⚙️ Settings")
        time_range = st.selectbox("Time range", ["Last 24 hours", "Last 7 days", "Last 30 days"])
        window_hours = st.selectbox("Metrics window", [1, 24], index=1)

        st.divider()
        st.header("🔌 API")
        api_base = _api_base_url()
        st.caption(api_base)
        try:
            health = _get_json("/health")
            st.success(
                f"API OK (demo={health.get('demo_mode')}, db={health.get('database_ok')}, redis={health.get('redis_ok')})"
            )
        except Exception as e:
            st.error(f"API unreachable: {e}")

        # YouTube quota mini-bar
        try:
            quota_data = _get_json("/api/v1/pipeline/quota")
            yt = quota_data.get("youtube", {})
            used = yt.get("total_units", 0)
            budget = yt.get("daily_budget", 10_000)
            used_pct = yt.get("budget_used_pct", 0)
            st.caption(f"YouTube API (PT day): {used:,} / {budget:,} units used ({used_pct:.0f}%)")
            st.progress(min(used_pct / 100, 1.0))
            st.caption(_youtube_quota_reset_text())
        except Exception:
            pass

        # Show alert badge
        try:
            alert_counts = _get_json("/api/v1/alerts/counts", params={"hours": 24})
            unack = alert_counts.get("unacknowledged", 0)
            if unack > 0:
                st.warning(f"🚨 {unack} unacknowledged alerts")
        except Exception:
            pass

        # Collector toggles
        st.divider()
        st.header("📡 Collectors")
        _sidebar_coll_data: dict[str, Any] | None = None
        try:
            _sidebar_coll_data = _get_json_nocache("/api/v1/collectors/status")
            collectors = {c["platform"]: c for c in _sidebar_coll_data.get("collectors", [])}

            # Reddit — always locked off, tooltip on hover
            reddit_info = collectors.get("reddit", {})
            st.toggle(
                ":orange[Reddit]",
                value=reddit_info.get("enabled", False),
                disabled=True,
                key="toggle_reddit",
                help=reddit_info.get("message", "Reddit requires .env configuration"),
            )

            # YouTube — toggleable
            yt_info = collectors.get("youtube", {})
            yt_current = yt_info.get("enabled", True)
            yt_new = st.toggle(":red[YouTube]", value=yt_current, key="toggle_youtube")
            if yt_new != yt_current:
                _put_json("/api/v1/collectors/youtube/toggle", params={"enabled": yt_new})
                st.rerun()

            # Bluesky — toggleable
            bsky_info = collectors.get("bluesky", {})
            bsky_current = bsky_info.get("enabled", True)
            bsky_new = st.toggle(":blue[Bluesky]", value=bsky_current, key="toggle_bluesky")
            if bsky_new != bsky_current:
                _put_json("/api/v1/collectors/bluesky/toggle", params={"enabled": bsky_new})
                st.rerun()

        except Exception as e:
            st.error(f"Could not load collector status: {e}")

        st.divider()
        if st.button("Refresh data"):
            _get_json.clear()

    # Main content
    hours = _time_range_to_hours(time_range)

    # Reuse collector status already fetched in the sidebar to avoid a duplicate HTTP call
    _enabled_platforms: set[str] = {"reddit", "youtube", "bluesky"}  # default: all
    if _sidebar_coll_data is not None:
        _enabled_platforms = {
            c["platform"] for c in _sidebar_coll_data.get("collectors", []) if c.get("enabled")
        }

    if page == "🔥 Trending Now":
        st.header("🔥 Trending Now")
        trending = _get_trending_metrics(window_hours=window_hours, limit=20)
        if not trending:
            st.info("No trending data available yet. Run the collector first.")
            return

        items = trending.get("items", [])
        if not items:
            st.info("No metrics snapshots found. Run the collector to populate metrics.")
            return

        rows = []
        for it in items:
            t = it["title"]
            m = it["metrics"]
            rows.append(
                {
                    "title": t["title"],
                    "media_type": t["media_type"],
                    "attention_index": m.get("attention_index"),
                    "mention_count": m.get("mention_count"),
                    "mention_velocity": m.get("mention_velocity"),
                    "avg_sentiment": m.get("avg_sentiment"),
                    "reddit_mentions": m.get("reddit_mentions"),
                    "youtube_mentions": m.get("youtube_mentions"),
                    "bluesky_mentions": m.get("bluesky_mentions"),
                    "snapshot_time": m.get("snapshot_time"),
                    "title_id": t["id"],
                }
            )

        df = pd.DataFrame(rows).sort_values(
            by="attention_index", ascending=False, na_position="last"
        )

        # Hide mention columns for disabled collectors
        _drop_cols = ["title_id"]
        _platform_cols = {
            "reddit": "reddit_mentions",
            "youtube": "youtube_mentions",
            "bluesky": "bluesky_mentions",
        }
        for plat, col in _platform_cols.items():
            if plat not in _enabled_platforms and col in df.columns:
                _drop_cols.append(col)
        st.dataframe(
            df.drop(columns=_drop_cols, errors="ignore"),
            width="stretch",
            hide_index=True,
        )

        st.caption(f"Updated: {trending.get('collected_at')}")

    elif page == "📈 Time Series":
        st.header("📈 Time series analysis")
        trending = _get_trending_metrics(window_hours=window_hours, limit=20)
        if not trending or not trending.get("items"):
            st.info("No titles available yet. Populate the DB first.")
            return

        title_options = _build_title_options(trending.get("items", []))
        if not title_options:
            st.info("No title options available yet.")
            return
        selected_title = st.selectbox(
            "Select title",
            title_options,
            format_func=_title_option_label,
            key="timeseries_title",
        )
        selected_id = selected_title.id

        try:
            ts = _get_json(
                "/api/v1/metrics/timeseries",
                params={"title_id": selected_id, "window_hours": window_hours, "hours": hours},
            )
        except Exception as e:
            st.error(f"Failed to load time series: {e}")
            return

        points = ts.get("points", [])
        if not points:
            st.info("No snapshots found for this title.")
            return

        df = pd.DataFrame(points)
        df["snapshot_time"] = pd.to_datetime(df["snapshot_time"])
        df = df.sort_values("snapshot_time")

        st.subheader("Mentions over time")
        fig1 = px.line(df, x="snapshot_time", y="mention_count", markers=True)
        st.plotly_chart(fig1, width="stretch")

        st.subheader("Attention index over time")
        fig2 = px.line(df, x="snapshot_time", y="attention_index", markers=True)
        st.plotly_chart(fig2, width="stretch")

    elif page == "🔄 Platform Comparison":
        st.header("🔄 Platform Comparison")
        trending = _get_trending_metrics(window_hours=window_hours, limit=20)
        if not trending or not trending.get("items"):
            st.info("No titles available yet. Populate the DB first.")
            return

        title_options = _build_title_options(trending.get("items", []))
        if not title_options:
            st.info("No title options available yet.")
            return
        selected_title = st.selectbox(
            "Select title",
            title_options,
            format_func=_title_option_label,
            key="platform_title",
        )
        selected_id = selected_title.id

        try:
            ts = _get_json(
                "/api/v1/metrics/timeseries",
                params={"title_id": selected_id, "window_hours": window_hours, "hours": hours},
            )
        except Exception as e:
            st.error(f"Failed to load platform comparison data: {e}")
            return
        points = ts.get("points", [])
        if not points:
            st.info("No snapshots found for this title.")
            return

        df = pd.DataFrame(points)
        df["snapshot_time"] = pd.to_datetime(df["snapshot_time"])
        df = df.sort_values("snapshot_time")

        # Only show platforms that are enabled
        _plat_vars = []
        if "reddit" in _enabled_platforms:
            _plat_vars.append("reddit_mentions")
        if "youtube" in _enabled_platforms:
            _plat_vars.append("youtube_mentions")
        if "bluesky" in _enabled_platforms:
            _plat_vars.append("bluesky_mentions")

        if not _plat_vars:
            st.info("All collectors are currently disabled.")
            return

        long = df.melt(
            id_vars=["snapshot_time"],
            value_vars=_plat_vars,
            var_name="platform",
            value_name="mentions",
        )
        fig = px.area(
            long,
            x="snapshot_time",
            y="mentions",
            color="platform",
            groupnorm=None,
            color_discrete_map=_PLATFORM_COLORS,
        )
        st.plotly_chart(fig, width="stretch")

    elif page == "💬 Sentiment":
        st.header("💬 Sentiment distribution")
        trending = _get_trending_metrics(window_hours=window_hours, limit=20)
        if not trending or not trending.get("items"):
            st.info("No titles available yet. Populate the DB first.")
            return

        title_options = _build_title_options(trending.get("items", []))
        if not title_options:
            st.info("No title options available yet.")
            return
        selected_title = st.selectbox(
            "Select title",
            title_options,
            format_func=_title_option_label,
            key="sentiment_title",
        )
        selected_id = selected_title.id

        sentiment_display = st.selectbox(
            "Sentiment model",
            ["VADER", "RoBERTa", "Both"],
            index=2,
            help="Controls the sentiment model used for charts and metrics on this page.",
            key="sentiment_display",
        )

        try:
            ts = _get_json(
                "/api/v1/metrics/timeseries",
                params={"title_id": selected_id, "window_hours": window_hours, "hours": hours},
            )
        except Exception as e:
            st.error(f"Failed to load sentiment data: {e}")
            return

        points = ts.get("points", [])
        if not points:
            st.info("No snapshots found for this title.")
            return

        df = pd.DataFrame(points)
        df["snapshot_time"] = pd.to_datetime(df["snapshot_time"])
        df = df.sort_values("snapshot_time")

        # Guard against None values in raw_metrics
        if "raw_metrics" not in df.columns:
            df["raw_metrics"] = [{} for _ in range(len(df))]
        df["raw_metrics"] = df["raw_metrics"].apply(lambda x: x if isinstance(x, dict) else {})
        configured_sentiment_model = get_settings().sentiment_model

        # Resolve RoBERTa values from either:
        # 1) secondary model aggregates from BOTH mode, or
        # 2) primary aggregates when the primary model is RoBERTa.
        resolved_roberta = df.apply(
            lambda row: (
                (
                    ((row["raw_metrics"].get("sentiment_secondary") or {}).get("avg_sentiment")),
                    ((row["raw_metrics"].get("sentiment_secondary") or {}).get("positive_ratio")),
                )
                if isinstance(row["raw_metrics"].get("sentiment_secondary"), dict)
                else (
                    (row.get("avg_sentiment"), row.get("positive_ratio"))
                    if (
                        row["raw_metrics"].get("sentiment_primary_model") == "roberta"
                        or (
                            row["raw_metrics"].get("sentiment_primary_model") is None
                            and configured_sentiment_model == "roberta"
                        )
                    )
                    else (None, None)
                )
            ),
            axis=1,
            result_type="expand",
        )
        resolved_roberta.columns = ["_roberta_avg", "_roberta_pos"]
        df["_roberta_avg"] = resolved_roberta["_roberta_avg"]
        df["_roberta_pos"] = resolved_roberta["_roberta_pos"]

        def _num_or_zero(value: Any) -> float:
            if value is None:
                return 0.0
            try:
                if pd.isna(value):
                    return 0.0
            except Exception:
                return 0.0
            return float(value)

        if sentiment_display == "RoBERTa":
            # Prefer secondary RoBERTa values from BOTH mode, otherwise use primary
            # values when snapshots were generated with RoBERTa as primary model.
            df["avg_sentiment"] = df["_roberta_avg"]
            df["positive_ratio"] = df["_roberta_pos"]
            if df["avg_sentiment"].isna().all() and df["positive_ratio"].isna().all():
                st.info("No RoBERTa sentiment data available for this title/time window.")
                return
            model_label = "RoBERTa"
        elif sentiment_display == "VADER":
            model_label = "VADER"
        else:
            model_label = "Both"

        c1, c2 = st.columns(2)
        with c1:
            if sentiment_display == "Both":
                # primary (vader)
                avg_s = df["avg_sentiment"].iloc[-1]
                st.metric("Latest VADER", value=f"{_num_or_zero(avg_s):.2f}")

                # secondary (roberta)
                last_raw = df["raw_metrics"].iloc[-1]
                sec_s = (last_raw.get("sentiment_secondary") or {}).get("avg_sentiment")
                st.metric("Latest RoBERTa", value=f"{_num_or_zero(sec_s):.2f}")
            else:
                avg_s = df["avg_sentiment"].iloc[-1]
                st.metric(
                    f"Latest avg sentiment ({model_label})",
                    value=f"{_num_or_zero(avg_s):.2f}",
                )

        with c2:
            if sentiment_display == "Both":
                pr = df["positive_ratio"].iloc[-1]
                st.metric("Latest VADER positive %", value=f"{_num_or_zero(pr) * 100:.1f}%")

                last_raw = df["raw_metrics"].iloc[-1]
                sec_pr = (last_raw.get("sentiment_secondary") or {}).get("positive_ratio")
                st.metric(
                    "Latest RoBERTa positive %",
                    value=f"{_num_or_zero(sec_pr) * 100:.1f}%",
                )
            else:
                pr = df["positive_ratio"].iloc[-1]
                st.metric(
                    f"Latest positive ratio ({model_label})",
                    value=f"{_num_or_zero(pr) * 100:.1f}%",
                )

        if sentiment_display == "Both":
            # Prepare data for multi-line plot
            df["roberta_avg"] = df["raw_metrics"].apply(
                lambda x: (x.get("sentiment_secondary") or {}).get("avg_sentiment")
            )
            df["roberta_pos"] = df["raw_metrics"].apply(
                lambda x: (x.get("sentiment_secondary") or {}).get("positive_ratio")
            )

            long_avg = df.melt(
                id_vars=["snapshot_time"],
                value_vars=["avg_sentiment", "roberta_avg"],
                var_name="Model",
                value_name="Score",
            )
            long_avg["Model"] = long_avg["Model"].map(
                {"avg_sentiment": "VADER", "roberta_avg": "RoBERTa"}
            )

            fig1 = px.line(
                long_avg,
                x="snapshot_time",
                y="Score",
                color="Model",
                markers=True,
                title="Average Sentiment Comparison",
            )
            st.plotly_chart(fig1, width="stretch")

            long_pos = df.melt(
                id_vars=["snapshot_time"],
                value_vars=["positive_ratio", "roberta_pos"],
                var_name="Model",
                value_name="Ratio",
            )
            long_pos["Model"] = long_pos["Model"].map(
                {"positive_ratio": "VADER", "roberta_pos": "RoBERTa"}
            )

            fig2 = px.line(
                long_pos,
                x="snapshot_time",
                y="Ratio",
                color="Model",
                markers=True,
                title="Positive Ratio Comparison",
            )
            st.plotly_chart(fig2, width="stretch")

        else:
            fig1 = px.line(
                df,
                x="snapshot_time",
                y="avg_sentiment",
                markers=True,
                title=f"Average Sentiment ({model_label})",
            )
            st.plotly_chart(fig1, width="stretch")

            fig2 = px.line(
                df,
                x="snapshot_time",
                y="positive_ratio",
                markers=True,
                title=f"Positive Ratio ({model_label})",
            )
            st.plotly_chart(fig2, width="stretch")

    elif page == "⚖️ Sentiment Comparison":
        st.header("⚖️ Sentiment Comparison (VADER vs RoBERTa)")
        st.markdown(
            "Compare the baseline VADER model against the Transformer-based RoBERTa model "
            "on real social media mentions."
        )

        trending = _get_trending_metrics(window_hours=window_hours, limit=20)
        if not trending or not trending.get("items"):
            st.info("No titles available yet. Populate the DB first.")
            return

        title_options = _build_title_options(trending.get("items", []))
        if not title_options:
            st.info("No title options available yet.")
            return
        selected_title = st.selectbox(
            "Select title",
            title_options,
            format_func=_title_option_label,
            key="comparison_title",
        )

        if not _enabled_platforms:
            st.warning(
                "All collectors are disabled. Please enable at least one collector platform."
            )
            return

        platform_options = ["All"] + sorted(_enabled_platforms)
        _default_plat = platform_options.index("bluesky") if "bluesky" in platform_options else 0
        platform = st.selectbox("Select platform", platform_options, index=_default_plat)

        limit = st.slider("Mentions to compare", min_value=5, max_value=50, value=10)

        translate_enabled = st.checkbox(
            "Translate non-English mentions",
            value=False,
            help=(
                "Translate mentions to English before sentiment analysis. "
                "Does not change collector/pipeline translation settings."
            ),
            key="translate_enabled",
        )
        comparison_cache = st.session_state.setdefault("sentiment_comparison_cache", {})
        cache_stats = st.session_state.setdefault(
            "sentiment_comparison_cache_stats",
            {"hits": 0, "misses": 0, "last_run_source": "—"},
        )
        st.caption(
            "Cache policy: "
            f"{_SENTIMENT_COMPARISON_CACHE_MAX_ENTRIES} entries max, "
            f"{_SENTIMENT_COMPARISON_CACHE_TTL_SECONDS // 60} minute TTL (LRU eviction)."
        )

        cache_controls_col1, cache_controls_col2 = st.columns([3, 2])
        with cache_controls_col1:
            run_clicked = st.button("Run comparison", key="run_sentiment_comparison")
        with cache_controls_col2:
            reset_clicked = st.button("Reset cache", key="reset_comparison_cache")

        if reset_clicked:
            comparison_cache.clear()
            cache_stats["hits"] = 0
            cache_stats["misses"] = 0
            cache_stats["last_run_source"] = "—"
            st.session_state.pop("sentiment_comparison_last_result", None)
            st.rerun()

        if run_clicked:
            now = time.monotonic()
            _prune_sentiment_comparison_cache(comparison_cache, now=now)

            fetch_platforms = sorted(_enabled_platforms) if platform == "All" else [platform]
            with st.spinner(f"Fetching {limit} mentions from {platform}..."):
                try:
                    mentions: list[dict[str, Any]] = []
                    per_platform_limit = (
                        limit if platform != "All" else max(limit // len(fetch_platforms), 5)
                    )
                    for plat in fetch_platforms:
                        resp = _get_json_nocache(
                            f"/api/v1/mentions/{plat}",
                            params={
                                "title": selected_title.name,
                                "title_id": selected_title.id,
                                "limit": per_platform_limit,
                            },
                        )
                        mentions.extend(resp.get("mentions", []))
                except Exception as e:
                    st.error(f"Failed to fetch mentions: {e}")
                    return

            if not mentions:
                st.warning("No mentions found for this title/platform.")
                return

            from sam.processors.sentiment import SentimentModel

            content_fingerprint = [
                (m.get("content") or "").strip()
                for m in mentions
                if (m.get("content") or "").strip()
            ]
            if not content_fingerprint:
                st.info("No analyzable mention text found for this selection.")
                return

            cache_key = _sentiment_comparison_cache_key(
                title_id=selected_title.id,
                platform=platform,
                translate_enabled=translate_enabled,
                contents=content_fingerprint,
            )
            cached_result = comparison_cache.get(cache_key)
            cache_hint = "fresh"

            if cached_result is not None:
                entry_created = float(cached_result.get("created_at", now))
                if now - entry_created <= _SENTIMENT_COMPARISON_CACHE_TTL_SECONDS:
                    data = cached_result.get("rows", [])
                    disagreements = int(cached_result.get("disagreements", 0))
                    cached_result["last_access"] = now
                    cache_hint = "cache"
                else:
                    comparison_cache.pop(cache_key, None)
                    cached_result = None

            if cached_result is not None and cache_hint == "cache":
                cache_stats["hits"] = int(cache_stats.get("hits", 0)) + 1
                cache_stats["last_run_source"] = "cache"
                st.caption("Using cached comparison results for this mention set.")
            else:
                cache_stats["misses"] = int(cache_stats.get("misses", 0)) + 1
                cache_stats["last_run_source"] = "fresh"
                progress = st.progress(0)
                progress.progress(0.2)
                vader = _get_cached_analyzer(SentimentModel.VADER)

                progress.progress(0.45)
                with st.spinner("Initializing RoBERTa model (first time may take a moment)..."):
                    try:
                        roberta = _get_cached_analyzer(SentimentModel.ROBERTA)
                    except Exception as e:
                        progress.empty()
                        st.error(f"Failed to initialize RoBERTa model: {e}")
                        return

                progress.progress(0.7)
                with st.spinner("Running batched sentiment analysis..."):
                    data, disagreements = _build_sentiment_comparison_rows(
                        mentions,
                        translate_enabled=translate_enabled,
                        vader=vader,
                        roberta=roberta,
                    )
                progress.progress(1.0)
                progress.empty()

                comparison_cache[cache_key] = {
                    "rows": data,
                    "disagreements": disagreements,
                    "created_at": now,
                    "last_access": now,
                }
                _prune_sentiment_comparison_cache(comparison_cache, now=now)

            st.session_state["sentiment_comparison_last_result"] = {
                "rows": data,
                "disagreements": disagreements,
                "cache_source": cache_hint,
                "title_id": selected_title.id,
                "platform": platform,
                "translate_enabled": translate_enabled,
                "limit": limit,
            }

        total_cache_uses = int(cache_stats.get("hits", 0)) + int(cache_stats.get("misses", 0))
        hit_rate = (
            (int(cache_stats.get("hits", 0)) / total_cache_uses) * 100
            if total_cache_uses > 0
            else 0.0
        )
        ccache1, ccache2, ccache3, ccache4 = st.columns(4)
        with ccache1:
            st.metric("Cache hits", int(cache_stats.get("hits", 0)))
        with ccache2:
            st.metric("Cache misses", int(cache_stats.get("misses", 0)))
        with ccache3:
            st.metric("Hit rate", f"{hit_rate:.1f}%")
        with ccache4:
            raw_last_run = str(cache_stats.get("last_run_source", "—"))
            last_run_label = {"cache": "Cached", "fresh": "Fresh"}.get(raw_last_run, raw_last_run)
            st.metric("Last run source", last_run_label)

        last_result = st.session_state.get("sentiment_comparison_last_result")
        if (
            last_result
            and str(last_result.get("title_id")) == selected_title.id
            and str(last_result.get("platform")) == platform
            and bool(last_result.get("translate_enabled")) == translate_enabled
            and int(last_result.get("limit", 0)) == limit
        ):
            rows = last_result.get("rows", [])
            disagreements = int(last_result.get("disagreements", 0))
            source = str(last_result.get("cache_source", "fresh"))
            if source == "cache":
                st.caption("Last displayed comparison was loaded from cache.")

            st.subheader(f"Analysis: VADER vs RoBERTa ({len(rows)} mentions)")
            analyzed_count = len(rows)
            if analyzed_count > 0:
                st.metric(
                    "Major Disagreements",
                    f"{disagreements}",
                    delta=f"{disagreements / analyzed_count * 100:.1f}%",
                )
            else:
                st.info("No analyzable mention text found for this selection.")
                return

            df_mentions = pd.DataFrame(rows)
            # Keep _original data for the expander but hide it from the table
            display_cols = [c for c in df_mentions.columns if not c.startswith("_")]
            st.dataframe(
                df_mentions[display_cols].style.background_gradient(subset=["Diff"], cmap="Reds"),
                use_container_width=True,
                column_config={
                    "Content": st.column_config.TextColumn(
                        "Content",
                        width="large",
                        help="Analyzed text. 🌐 = translated from original language.",
                    ),
                },
            )

            # Translation details
            translated_rows = [(i, r) for i, r in enumerate(rows) if r.get("_original") is not None]
            if translated_rows:
                st.caption("🌐 = content was translated to English before analysis.")
                with st.expander(
                    f"View original text for {len(translated_rows)} translated mentions"
                ):
                    for idx, (row_num, r) in enumerate(translated_rows):
                        st.markdown(
                            f"**Row {row_num}.** **Translated:** {r['Content'].lstrip('🌐 ')}\n\n"
                            f"&nbsp;&nbsp;&nbsp;&nbsp;**Original:** {r['_original']}"
                        )
                        if idx < len(translated_rows) - 1:
                            st.divider()
        else:
            st.info("Run comparison to analyze mentions for the current selection.")

    elif page == "🚨 Alerts":
        st.header("🚨 Alerts and anomalies")
        st.markdown("*Real-time anomaly detection for tracked titles*")

        # Alert summary cards
        try:
            counts_data = _get_json("/api/v1/alerts/counts", params={"hours": hours})
            counts = counts_data.get("counts", {})
            total = counts_data.get("total", 0)
            unack = counts_data.get("unacknowledged", 0)

            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.metric("Total alerts", total)
            with c2:
                st.metric("🔴 Critical", counts.get("critical", 0))
            with c3:
                st.metric("🟡 Warning", counts.get("warning", 0))
            with c4:
                st.metric("🟢 Info", counts.get("info", 0))

            if unack > 0:
                st.warning(f"⚠️ {unack} unacknowledged alert(s) require attention")

        except Exception as e:
            st.error(f"Failed to load alert counts: {e}")

        st.divider()

        # Recent alerts list
        st.subheader("Recent alerts")

        severity_filter = st.selectbox("Filter by severity", ["All", "critical", "warning", "info"])

        try:
            params: dict[str, Any] = {"hours": hours, "limit": 50}
            if severity_filter != "All":
                params["severity"] = severity_filter

            alerts_data = _get_json("/api/v1/alerts", params=params)
            alerts = alerts_data.get("alerts", [])

            if not alerts:
                st.info("No alerts in the selected time range. 🎉")
            else:
                for alert in alerts:
                    severity = alert.get("severity", "info")
                    icon = {"critical": "🔴", "warning": "🟡", "info": "🟢"}.get(severity, "⚪")
                    ack_status = "✅" if alert.get("acknowledged_at") else "⏳"

                    with st.expander(
                        f"{icon} {ack_status} {alert.get('message', 'Unknown alert')[:80]}",
                        expanded=severity == "critical" and not alert.get("acknowledged_at"),
                    ):
                        col1, col2 = st.columns([3, 1])
                        with col1:
                            st.markdown(f"**Type:** `{alert.get('alert_type')}`")
                            st.markdown(f"**Severity:** {severity}")
                            st.markdown(f"**Created:** {alert.get('created_at')}")
                            if alert.get("acknowledged_at"):
                                st.markdown(f"**Acknowledged:** {alert.get('acknowledged_at')}")

                        with col2:
                            details = alert.get("details", {})
                            if details:
                                st.json(details)

        except Exception as e:
            st.error(f"Failed to load alerts: {e}")

        st.divider()

        # Pipeline health
        st.subheader("🔧 Pipeline health")
        try:
            pipeline = _get_json("/api/v1/pipeline/health")

            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("Active Titles", pipeline.get("active_titles", 0))
            with c2:
                st.metric("Total Mentions", pipeline.get("total_mentions", 0))
            with c3:
                mentions_24h = pipeline.get("mentions_last_24h", {})
                total_24h = sum(mentions_24h.values())
                st.metric("Mentions (24h)", total_24h)

            # Newest mention ages
            ages = pipeline.get("newest_mention_age_seconds", {})
            if ages:
                st.markdown("**Data Freshness:**")
                for platform, age_sec in ages.items():
                    if age_sec is not None:
                        if age_sec < 60:
                            age_str = f"{int(age_sec)}s ago"
                        elif age_sec < 3600:
                            age_str = f"{int(age_sec / 60)}m ago"
                        else:
                            age_str = f"{age_sec / 3600:.1f}h ago"
                        st.caption(f"  • {platform}: {age_str}")
                    else:
                        st.caption(f"  • {platform}: No data")

            # Latest pipeline runs
            runs = pipeline.get("latest_pipeline_runs", [])
            if runs:
                st.markdown("**Latest Pipeline Runs:**")
                for run in runs[:5]:
                    status_icon = {"success": "✅", "failed": "❌", "running": "🔄"}.get(
                        run.get("status"), "⚪"
                    )
                    st.caption(f"  • {run.get('job_name')}: {status_icon} {run.get('status')}")

            sentiment_stats = pipeline.get("sentiment_stats") or {}
            if sentiment_stats:
                st.markdown("**Sentiment Processing:**")
                c4, c5, c6, c7, c8 = st.columns(5)
                with c4:
                    st.metric(
                        "Translated (attempted)",
                        int(sentiment_stats.get("translate_attempted", 0)),
                    )
                with c5:
                    st.metric(
                        "Translated (changed)", int(sentiment_stats.get("translate_count", 0))
                    )
                with c6:
                    st.metric(
                        "Skipped (English)",
                        int(sentiment_stats.get("translate_skipped_english", 0)),
                    )
                with c7:
                    st.metric(
                        "Translation Failures", int(sentiment_stats.get("translate_failures", 0))
                    )
                with c8:
                    st.metric(
                        "Sentiment Time",
                        f"{float(sentiment_stats.get('sentiment_ms_total', 0.0)):.0f} ms",
                    )

        except Exception as e:
            st.error(f"Failed to load pipeline health: {e}")

    elif page == "📡 API Quota":
        st.header("📡 API quota usage")
        st.markdown("*Track API usage to stay within daily limits*")

        try:
            quota_data = _get_json("/api/v1/pipeline/quota")
        except Exception as e:
            st.error(f"Failed to load quota data: {e}")
            quota_data = {}

        if quota_data:
            yt = quota_data.get("youtube", {})

            # YouTube section
            st.subheader("YouTube Data API v3")
            if "youtube" not in _enabled_platforms:
                st.warning("YouTube collector is currently disabled.")
            else:
                budget = yt.get("daily_budget", 10_000)
                used = yt.get("total_units", 0)
                remaining = yt.get("budget_remaining", budget)
                used_pct = yt.get("budget_used_pct", 0.0)
                total_calls = yt.get("total_calls", 0)
                quota_date = yt.get("date", "—")

                # Status color
                if used_pct < 50:
                    status_color = "🟢"
                    status_text = "Healthy"
                elif used_pct < 80:
                    status_color = "🟡"
                    status_text = "Moderate"
                else:
                    status_color = "🔴"
                    status_text = "Critical"

                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    st.metric("Units used", f"{used:,}")
                with c2:
                    st.metric("Remaining", f"{remaining:,}")
                with c3:
                    st.metric("Total calls", f"{total_calls:,}")
                with c4:
                    st.metric("Status", f"{status_color} {status_text}")

                # Big progress bar
                st.markdown(
                    f"**Daily Budget (PT day): {used:,} / {budget:,} units ({used_pct:.1f}%)**"
                )
                st.progress(min(used_pct / 100, 1.0))

                # Call breakdown
                calls_by_endpoint = yt.get("calls_by_endpoint", {})
                if calls_by_endpoint:
                    st.markdown("**Calls by endpoint:**")
                    for endpoint, count in sorted(calls_by_endpoint.items()):
                        cost_per_call = 100 if "search" in endpoint else 1
                        st.caption(
                            f"  • `{endpoint}`: {count} calls "
                            f"({count * cost_per_call:,} units @ {cost_per_call} units/call)"
                        )

                # YouTube resets at midnight Pacific Time, not UTC.
                st.caption(f"Quota date (PT): {quota_date} — {_youtube_quota_reset_text()}")
                if quota_data.get("last_run_at"):
                    st.caption(f"Last collection run: {quota_data['last_run_at']}")

                st.divider()

                # Cost reference
                st.subheader("Cost reference")
                st.markdown("""
| Endpoint | Cost | Description |
|---|---|---|
| `search.list` | 100 units | Search for videos by query |
| `videos.list` | 1 unit | Get video statistics/details |

**Daily budget:** 10,000 units (default YouTube project quota)

**Tip:** Each title costs ~101 units (1 search + 1 video details batch).
With 20 titles, that's ~2,020 units per collection cycle.
At 5-minute polling, budget allows ~4 full cycles per day.
""")

            st.divider()
            st.subheader("TMDB API")
            st.info(
                "TMDB uses per-second rate limiting (~40 req/s), not a daily quota. "
                "No tracking needed — the built-in retry-on-429 handles it."
            )

            st.subheader("Bluesky (AT Protocol) API")
            if "bluesky" not in _enabled_platforms:
                st.warning("Bluesky collector is currently disabled.")
            else:
                st.info(
                    "Bluesky doesn't publish a clear daily quota like YouTube. "
                    "Treat it as rate-limited (429/5xx) rather than a fixed per-day budget. "
                    "No daily quota tracking needed — conservative pacing plus retry/backoff on 429 handles it."
                )

    # Footer
    st.divider()
    col1, col2, col3 = st.columns(3)
    with col1:
        try:
            h = _get_json("/health")
            demo = h.get("demo_mode")
            st.caption("🟢 Demo Mode Active" if demo else "🔵 Live Mode Active")
        except Exception:
            st.caption("—")
    with col2:
        st.caption(f"Last updated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    with col3:
        st.caption("SAM v0.1.0")


if __name__ == "__main__":
    main()
