"""⚖️ Sentiment Comparison (VADER vs RoBERTa) page."""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

import pandas as pd
import streamlit as st

from dashboard.api_client import get_json_nocache
from dashboard.helpers import (
    SENTIMENT_COMPARISON_CACHE_MAX_ENTRIES,
    SENTIMENT_COMPARISON_CACHE_TTL_SECONDS,
    TitleOption,
    build_title_options,
    get_cached_analyzer,
    get_trending_metrics,
    prune_sentiment_comparison_cache,
    render_title_picker,
    sentiment_comparison_cache_key,
    title_option_label,
)
from dashboard.pages import PageContext
from dashboard.sentiment_analysis import build_sentiment_comparison_rows
from sam.config import get_settings


def render(ctx: PageContext) -> None:  # noqa: C901 — unavoidable UI complexity
    """Render the Sentiment Comparison page."""
    st.header("⚖️ Sentiment Comparison (VADER vs RoBERTa)")
    st.markdown(
        "Compare the baseline VADER model against the Transformer-based RoBERTa model "
        "on real social media mentions."
    )
    selected_title = render_title_picker(
        label="Select title",
        key_prefix="sentiment_comparison",
        window_hours=ctx.window_hours,
        st_module=st,
        format_func=title_option_label,
        fallback_options_loader=lambda: build_title_options(
            (get_trending_metrics(window_hours=ctx.window_hours, limit=20) or {}).get("items", [])
        ),
    )
    if selected_title is None:
        return

    if not ctx.enabled_platforms:
        st.warning("All collectors are disabled. Please enable at least one collector platform.")
        return

    platform_options = ["All"] + sorted(ctx.enabled_platforms)
    platform = st.selectbox("Select platform", platform_options, index=0)

    source_filter = "all"
    source_filter_label = "All mentions"
    if platform in {"youtube", "All"}:
        source_filter_options = {
            "All mentions": "all",
            "Video titles only": "video",
            "Comments only": "comment",
        }
        source_filter_label = st.selectbox(
            "Source type",
            list(source_filter_options.keys()),
            index=0,
            help=(
                "Filter by mention source type. "
                "Useful for YouTube when comparing video titles vs comments."
            ),
        )
        source_filter = source_filter_options[source_filter_label]

    limit = st.slider("Mentions to compare", min_value=5, max_value=100, value=10)

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
        f"{SENTIMENT_COMPARISON_CACHE_MAX_ENTRIES} entries max, "
        f"{SENTIMENT_COMPARISON_CACHE_TTL_SECONDS // 60} minute TTL (LRU eviction)."
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
        _run_comparison(
            selected_title=selected_title,
            platform=platform,
            source_filter=source_filter,
            source_filter_label=source_filter_label,
            limit=limit,
            translate_enabled=translate_enabled,
            comparison_cache=comparison_cache,
            cache_stats=cache_stats,
            enabled_platforms=ctx.enabled_platforms,
        )

    # Cache metrics row
    total_cache_uses = int(cache_stats.get("hits", 0)) + int(cache_stats.get("misses", 0))
    hit_rate = (
        (int(cache_stats.get("hits", 0)) / total_cache_uses) * 100 if total_cache_uses > 0 else 0.0
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

    _display_last_result(
        selected_title=selected_title,
        platform=platform,
        source_filter=source_filter,
        source_filter_label=source_filter_label,
        limit=limit,
        translate_enabled=translate_enabled,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_comparison(  # noqa: PLR0913
    *,
    selected_title: TitleOption,
    platform: str,
    source_filter: str,
    source_filter_label: str,
    limit: int,
    translate_enabled: bool,
    comparison_cache: dict[str, Any],
    cache_stats: dict[str, Any],
    enabled_platforms: set[str],
) -> None:
    """Fetch mentions and run VADER/RoBERTa comparison."""
    now = time.monotonic()
    prune_sentiment_comparison_cache(comparison_cache, now=now)

    fetch_platforms = sorted(enabled_platforms) if platform == "All" else [platform]
    with st.spinner(f"Fetching {limit} mentions from {platform}..."):
        try:
            mentions: list[dict[str, Any]] = []
            per_platform_limit = (
                limit if platform != "All" else max(limit // len(fetch_platforms), 5)
            )
            for plat in fetch_platforms:
                resp = get_json_nocache(
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

    if source_filter != "all":
        mentions = [m for m in mentions if str(m.get("source_type", "")).lower() == source_filter]

    if not mentions:
        if source_filter == "video":
            st.warning("No video-title mentions found for this title/platform.")
        elif source_filter == "comment":
            st.warning("No comment mentions found for this title/platform.")
        else:
            st.warning("No mentions found for this title/platform.")
        return

    from sam.processors.sentiment import SentimentModel

    runtime_settings = get_settings()
    sarcasm_enabled = bool(getattr(runtime_settings, "enable_sarcasm_detection", False))
    emotion_enabled = bool(getattr(runtime_settings, "enable_emotion_detection", False))

    content_fingerprint = [
        (m.get("content") or "").strip() for m in mentions if (m.get("content") or "").strip()
    ]
    if not content_fingerprint:
        st.info("No analyzable mention text found for this selection.")
        return

    cache_key = sentiment_comparison_cache_key(
        title_id=selected_title.id,
        platform=platform,
        source_filter=source_filter,
        translate_enabled=translate_enabled,
        sarcasm_enabled=sarcasm_enabled,
        emotion_enabled=emotion_enabled,
        contents=content_fingerprint,
    )
    cached_result = comparison_cache.get(cache_key)
    cache_hint = "fresh"

    if cached_result is not None:
        entry_created = float(cached_result.get("created_at", now))
        if now - entry_created <= SENTIMENT_COMPARISON_CACHE_TTL_SECONDS:
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
        vader = get_cached_analyzer(SentimentModel.VADER)

        progress.progress(0.45)
        with st.spinner("Initializing RoBERTa model (first time may take a moment)..."):
            try:
                roberta = get_cached_analyzer(SentimentModel.ROBERTA)
            except Exception as e:
                progress.empty()
                st.error(f"Failed to initialize RoBERTa model: {e}")
                return

        progress.progress(0.7)
        with st.spinner("Running batched sentiment analysis..."):
            data, disagreements = build_sentiment_comparison_rows(
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
        prune_sentiment_comparison_cache(comparison_cache, now=now)

    st.session_state["sentiment_comparison_last_result"] = {
        "rows": data,
        "disagreements": disagreements,
        "cache_source": cache_hint,
        "title_id": selected_title.id,
        "platform": platform,
        "source_filter": source_filter,
        "source_filter_label": source_filter_label,
        "translate_enabled": translate_enabled,
        "sarcasm_enabled": sarcasm_enabled,
        "emotion_enabled": emotion_enabled,
        "limit": limit,
    }


def _display_last_result(
    *,
    selected_title: TitleOption,
    platform: str,
    source_filter: str,
    source_filter_label: str,
    limit: int,
    translate_enabled: bool,
) -> None:
    """Show the most recent comparison result if it matches current filters."""
    last_result = st.session_state.get("sentiment_comparison_last_result")
    if not last_result:
        st.info("Run comparison to analyze mentions for the current selection.")
        return

    if (
        str(last_result.get("title_id")) != selected_title.id
        or str(last_result.get("platform")) != platform
        or str(last_result.get("source_filter", "all")) != source_filter
        or bool(last_result.get("translate_enabled")) != translate_enabled
        or bool(last_result.get("sarcasm_enabled", False))
        != bool(getattr(get_settings(), "enable_sarcasm_detection", False))
        or bool(last_result.get("emotion_enabled", False))
        != bool(getattr(get_settings(), "enable_emotion_detection", False))
        or int(last_result.get("limit", 0)) != limit
    ):
        st.info("Run comparison to analyze mentions for the current selection.")
        return

    rows = last_result.get("rows", [])
    disagreements = int(last_result.get("disagreements", 0))
    source = str(last_result.get("cache_source", "fresh"))
    selected_source_label = str(last_result.get("source_filter_label", source_filter_label))
    if source == "cache":
        st.caption("Last displayed comparison was loaded from cache.")

    st.subheader(f"Analysis: VADER vs RoBERTa ({len(rows)} mentions · {selected_source_label})")
    analyzed_count = len(rows)
    if analyzed_count == 0:
        st.info("No analyzable mention text found for this selection.")
        return

    st.metric(
        "Major Disagreements",
        f"{disagreements}",
        delta=f"{disagreements / analyzed_count * 100:.1f}%",
    )

    sarcastic_mentions = sum(1 for row in rows if row.get("_sarcastic") is True)
    emotion_labels = [
        str(label) for label in (row.get("_top_emotion") for row in rows) if isinstance(label, str)
    ]
    summary_col_1, summary_col_2 = st.columns(2)
    with summary_col_1:
        st.metric(
            "Sarcastic Mentions",
            f"{sarcastic_mentions}",
            delta=f"{(sarcastic_mentions / analyzed_count) * 100:.1f}%",
        )
    with summary_col_2:
        if emotion_labels:
            top_emotion, top_count = Counter(emotion_labels).most_common(1)[0]
            st.metric("Top Emotion", f"{top_emotion} ({top_count})")
        else:
            st.metric("Top Emotion", "—")

    df_mentions = pd.DataFrame(rows)
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
        with st.expander(f"View original text for {len(translated_rows)} translated mentions"):
            for idx, (row_num, r) in enumerate(translated_rows):
                st.markdown(
                    f"**Row {row_num}.** **Translated:** {r['Content'].lstrip('🌐 ')}\n\n"
                    f"&nbsp;&nbsp;&nbsp;&nbsp;**Original:** {r['_original']}"
                )
                if idx < len(translated_rows) - 1:
                    st.divider()
