"""📊 Alpha Metrics page."""

from __future__ import annotations

from typing import Any, Literal

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.api_client import get_json
from dashboard.helpers import (
    COLOR_BLUESKY,
    COLOR_REDDIT,
    COLOR_YOUTUBE,
    build_title_options,
    get_trending_metrics,
    render_title_picker,
    title_option_label,
)
from dashboard.pages import PageContext


def render(ctx: PageContext) -> None:  # noqa: C901, PLR0912, PLR0915 — rich dashboard page
    """Render the Alpha Metrics page."""
    st.header("📊 Alpha Metrics")
    st.markdown("*Advanced signals for alpha extraction from social attention data*")

    selected_title = render_title_picker(
        label="Select title",
        key_prefix="alpha",
        window_hours=ctx.window_hours,
        st_module=st,
        format_func=title_option_label,
        fallback_options_loader=lambda: build_title_options(
            (get_trending_metrics(window_hours=ctx.window_hours, limit=20) or {}).get("items", [])
        ),
    )
    if selected_title is None:
        return
    selected_id = selected_title.id

    try:
        ts = get_json(
            "/api/v1/metrics/timeseries",
            params={"title_id": selected_id, "window_hours": ctx.window_hours, "hours": ctx.hours},
        )
    except Exception as e:
        st.error(f"Failed to load metrics: {e}")
        return

    points = ts.get("points", [])
    if not points:
        st.info("No snapshots found for this title.")
        return

    df = pd.DataFrame(points)
    df["snapshot_time"] = pd.to_datetime(df["snapshot_time"])
    df = df.sort_values("snapshot_time")

    # Safely extract raw_metrics fields
    if "raw_metrics" not in df.columns:
        df["raw_metrics"] = [{} for _ in range(len(df))]
    df["raw_metrics"] = df["raw_metrics"].apply(lambda x: x if isinstance(x, dict) else {})

    latest_raw = df["raw_metrics"].iloc[-1] if len(df) > 0 else {}

    # ── Metric cards ──────────────────────────────────────
    st.subheader("📈 Latest Snapshot")
    _render_metric_cards(latest_raw)

    # ── Creator vs Audience ──────────────────────────────
    _render_creator_vs_audience(latest_raw)

    # ── Cross-platform divergence ────────────────────────
    _render_cross_platform_divergence(latest_raw)

    # ── Sentiment momentum timeline ──────────────────────
    _render_momentum_timeline(df)

    # ── Snapshot keywords / hashtags ─────────────────────
    _render_keywords_hashtags(df, latest_raw)

    # ── Word cloud from mentions ─────────────────────────
    _render_word_cloud(selected_title, selected_id)


# ---------------------------------------------------------------------------
# Sub-renderers
# ---------------------------------------------------------------------------


def _render_metric_cards(latest_raw: dict[str, Any]) -> None:
    c1, c2, c3, c4, c5 = st.columns(5)

    ews = latest_raw.get("engagement_weighted_sentiment")
    momentum = latest_raw.get("sentiment_momentum")
    fatigue = latest_raw.get("audience_fatigue_index")
    viral = latest_raw.get("viral_coefficient")
    hhi = latest_raw.get("author_diversity_score")

    with c1:
        st.metric(
            "Eng-Weighted Sentiment",
            f"{float(ews):.3f}" if isinstance(ews, (int, float)) else "—",
            help="Sentiment weighted by engagement. High-engagement opinions carry more weight.",
        )
    with c2:
        delta_color: Literal["normal", "inverse"] = (
            "normal" if not isinstance(momentum, (int, float)) or momentum >= 0 else "inverse"
        )
        st.metric(
            "Sentiment Momentum",
            f"{float(momentum):+.3f}" if isinstance(momentum, (int, float)) else "—",
            delta=(
                "→"
                if isinstance(momentum, (int, float)) and momentum == 0
                else ("↑" if isinstance(momentum, (int, float)) and momentum > 0 else "↓")
                if isinstance(momentum, (int, float))
                else None
            ),
            delta_color=delta_color,
            help="Rate of change of average sentiment between snapshots.",
        )
    with c3:
        fatigue_color = (
            "🟢"
            if isinstance(fatigue, (int, float)) and fatigue < 0.3
            else ("🟡" if isinstance(fatigue, (int, float)) and fatigue < 0.6 else "🔴")
        )
        st.metric(
            "Audience Fatigue",
            (f"{fatigue_color} {float(fatigue):.2f}" if isinstance(fatigue, (int, float)) else "—"),
            help="0 = fresh engagement, 1 = severe fatigue."
            " Composite of declining engagement-per-mention + sentiment.",
        )
    with c4:
        st.metric(
            "Viral Coefficient",
            f"{float(viral):.2f}" if isinstance(viral, (int, float)) else "—",
            help="Repost/share ratio. Higher = more organic amplification.",
        )
    with c5:
        div_label = (
            "Diverse"
            if isinstance(hhi, (int, float)) and hhi < 0.1
            else ("Moderate" if isinstance(hhi, (int, float)) and hhi < 0.25 else "Concentrated")
        )
        repeat_ratio = latest_raw.get("repeat_author_ratio")
        repeat_str = (
            f" (repeat: {float(repeat_ratio):.2f})"
            if isinstance(repeat_ratio, (int, float))
            else ""
        )
        st.metric(
            "Author Diversity",
            f"{div_label} ({float(hhi):.3f}){repeat_str}" if isinstance(hhi, (int, float)) else "—",
            help="HHI index. Lower = more diverse. Repeat = fraction of authors posting 2+ times.",
        )


def _render_creator_vs_audience(latest_raw: dict[str, Any]) -> None:
    creator_s = latest_raw.get("creator_sentiment")
    audience_s = latest_raw.get("audience_sentiment")
    if creator_s is None and audience_s is None:
        return

    st.divider()
    st.subheader("🎬 Creator vs Audience Sentiment")
    cc1, cc2, cc3 = st.columns(3)
    with cc1:
        st.metric("Creator Sentiment", f"{creator_s:.3f}" if creator_s is not None else "—")
    with cc2:
        st.metric("Audience Sentiment", f"{audience_s:.3f}" if audience_s is not None else "—")
    with cc3:
        if creator_s is not None and audience_s is not None:
            gap = creator_s - audience_s
            st.metric("Gap (Creator − Audience)", f"{gap:+.3f}")
        else:
            st.metric("Gap", "—")


def _render_cross_platform_divergence(latest_raw: dict[str, Any]) -> None:
    divergence = latest_raw.get("sentiment_divergence", {})
    if not divergence:
        return

    st.divider()
    st.subheader("🔀 Cross-Platform Sentiment Divergence")

    avgs = {k: v for k, v in divergence.items() if "_avg" in k}
    pairs = {k: v for k, v in divergence.items() if "_vs_" in k}

    if avgs:
        avg_df = pd.DataFrame(
            [
                {"Platform": k.replace("_avg", "").title(), "Avg Sentiment": v}
                for k, v in avgs.items()
            ]
        )
        fig_bar = px.bar(
            avg_df,
            x="Platform",
            y="Avg Sentiment",
            color="Platform",
            color_discrete_map={
                "Reddit": COLOR_REDDIT,
                "Youtube": COLOR_YOUTUBE,
                "Bluesky": COLOR_BLUESKY,
            },
            title="Per-Platform Average Sentiment",
        )
        fig_bar.update_layout(showlegend=False)
        st.plotly_chart(fig_bar, use_container_width=True)

    if pairs:
        pair_text = " · ".join(
            f"{k.replace('_vs_', ' vs ').title()}: Δ={v:.3f}" for k, v in pairs.items()
        )
        st.caption(f"Pairwise divergences: {pair_text}")


def _render_momentum_timeline(df: pd.DataFrame) -> None:
    df["_momentum"] = df["raw_metrics"].apply(lambda x: x.get("sentiment_momentum", 0.0))
    df["_fatigue"] = df["raw_metrics"].apply(lambda x: x.get("audience_fatigue_index", 0.0))

    if df["_momentum"].abs().sum() <= 0:
        return

    st.divider()
    st.subheader("📉 Sentiment Momentum Over Time")
    fig_mom = px.bar(
        df,
        x="snapshot_time",
        y="_momentum",
        color="_momentum",
        color_continuous_scale=["#ef4444", "#fbbf24", "#22c55e"],
        labels={"_momentum": "Momentum", "snapshot_time": "Time"},
    )
    fig_mom.update_layout(coloraxis_showscale=False)
    st.plotly_chart(fig_mom, use_container_width=True)


def _render_keywords_hashtags(df: pd.DataFrame, latest_raw: dict[str, Any]) -> None:  # noqa: C901, PLR0912
    keyword_signals = latest_raw.get("keyword_signals")
    if not isinstance(keyword_signals, dict):
        return

    keywords_raw = keyword_signals.get("keywords")
    hashtags_raw = keyword_signals.get("hashtags")

    keyword_items = [
        item
        for item in (keywords_raw if isinstance(keywords_raw, list) else [])
        if isinstance(item, dict)
        and isinstance(item.get("term"), str)
        and isinstance(item.get("score"), (float, int))
    ]
    hashtag_items = [
        item
        for item in (hashtags_raw if isinstance(hashtags_raw, list) else [])
        if isinstance(item, dict)
        and isinstance(item.get("tag"), str)
        and isinstance(item.get("count"), (float, int))
    ]

    if not keyword_items and not hashtag_items:
        return

    st.divider()
    st.subheader("🏷️ Top Terms")
    k1, k2 = st.columns(2)

    with k1:
        st.caption("Keywords")
        if keyword_items:
            keyword_df = pd.DataFrame(
                [
                    {"Keyword": item["term"], "Relevance": float(item["score"])}
                    for item in keyword_items[:15]
                ]
            )
            st.dataframe(keyword_df, use_container_width=True, hide_index=True)
        else:
            st.info("No keyword signals for this snapshot.")

    with k2:
        st.caption("Hashtags")
        if hashtag_items:
            hashtag_df = pd.DataFrame(
                [
                    {"Hashtag": f"#{str(item['tag']).lstrip('#')}", "Count": int(item["count"])}
                    for item in hashtag_items[:20]
                ]
            )
            st.dataframe(hashtag_df, use_container_width=True, hide_index=True)
        else:
            st.info("No hashtag signals for this snapshot.")

    # Trend charts
    fig_keywords = _build_keyword_trend_chart(df, keyword_items)
    fig_hashtags = _build_hashtag_trend_chart(df, hashtag_items)

    if fig_keywords is not None or fig_hashtags is not None:
        st.subheader("📈 Term Trends")
        t1, t2 = st.columns(2)
        with t1:
            if fig_keywords is not None:
                st.plotly_chart(fig_keywords, use_container_width=True)
            else:
                st.info("No keyword trend data available.")
        with t2:
            if fig_hashtags is not None:
                st.plotly_chart(fig_hashtags, use_container_width=True)
            else:
                st.info("No hashtag trend data available.")


def _build_keyword_trend_chart(df: pd.DataFrame, keyword_items: list[dict[str, Any]]) -> Any | None:
    latest_top_terms = [
        str(item["term"]) for item in keyword_items[:5] if str(item.get("term", "")).strip()
    ]
    if not latest_top_terms:
        return None

    trend_rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        raw = row.get("raw_metrics", {})
        if not isinstance(raw, dict):
            continue
        signals = raw.get("keyword_signals")
        if not isinstance(signals, dict):
            continue
        snap_keywords = signals.get("keywords")
        if not isinstance(snap_keywords, list):
            continue

        score_by_term: dict[str, float] = {}
        for keyword in snap_keywords:
            if not isinstance(keyword, dict):
                continue
            term = keyword.get("term")
            score = keyword.get("score")
            if isinstance(term, str) and isinstance(score, (int, float)):
                score_by_term[term] = float(score)

        for term in latest_top_terms:
            trend_rows.append(
                {
                    "snapshot_time": row["snapshot_time"],
                    "keyword": term,
                    "score": score_by_term.get(term, 0.0),
                }
            )

    if not trend_rows:
        return None
    trend_df = pd.DataFrame(trend_rows)
    if trend_df["score"].sum() <= 0:
        return None
    return px.line(
        trend_df,
        x="snapshot_time",
        y="score",
        color="keyword",
        markers=True,
        labels={"snapshot_time": "Time", "score": "Relevance", "keyword": "Keyword"},
        title="Top keyword relevance across snapshots",
    )


def _build_hashtag_trend_chart(df: pd.DataFrame, hashtag_items: list[dict[str, Any]]) -> Any | None:
    latest_top_tags = [
        str(item["tag"]).lstrip("#")
        for item in hashtag_items[:5]
        if str(item.get("tag", "")).strip()
    ]
    if not latest_top_tags:
        return None

    hashtag_rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        raw = row.get("raw_metrics", {})
        if not isinstance(raw, dict):
            continue
        signals = raw.get("keyword_signals")
        if not isinstance(signals, dict):
            continue
        snap_hashtags = signals.get("hashtags")
        if not isinstance(snap_hashtags, list):
            continue

        count_by_tag: dict[str, int] = {}
        for hashtag in snap_hashtags:
            if not isinstance(hashtag, dict):
                continue
            tag = hashtag.get("tag")
            count = hashtag.get("count")
            if isinstance(tag, str) and isinstance(count, (int, float)):
                count_by_tag[tag.lstrip("#")] = int(count)

        for tag in latest_top_tags:
            hashtag_rows.append(
                {
                    "snapshot_time": row["snapshot_time"],
                    "hashtag": f"#{tag}",
                    "count": count_by_tag.get(tag, 0),
                }
            )

    if not hashtag_rows:
        return None
    hashtag_df = pd.DataFrame(hashtag_rows)
    if hashtag_df["count"].sum() <= 0:
        return None
    return px.line(
        hashtag_df,
        x="snapshot_time",
        y="count",
        color="hashtag",
        markers=True,
        labels={"snapshot_time": "Time", "count": "Count", "hashtag": "Hashtag"},
        title="Top hashtag counts across snapshots",
    )


def _render_word_cloud(selected_title: Any, selected_id: str) -> None:
    st.divider()
    st.subheader("☁️ Word Cloud")

    try:
        mention_texts: list[str] = []
        for platform in ("reddit", "youtube", "bluesky"):
            try:
                mentions_resp = get_json(
                    f"/api/v1/mentions/{platform}",
                    params={
                        "title": selected_title.name,
                        "title_id": selected_id,
                        "limit": 80,
                    },
                )
            except Exception:
                continue
            mention_texts.extend(
                [
                    m.get("content", "")
                    for m in mentions_resp.get("mentions", [])
                    if m.get("content")
                ]
            )
    except Exception:
        mention_texts = []

    if not mention_texts:
        st.info("No mention text available for word cloud generation.")
        return

    try:
        from io import BytesIO

        from wordcloud import WordCloud

        combined_text = " ".join(mention_texts)
        wc = WordCloud(
            width=800,
            height=400,
            background_color="#0e1117",
            colormap="cool",
            max_words=80,
            collocations=False,
            stopwords=set(WordCloud().stopwords)
            | {
                "https",
                "http",
                "com",
                "www",
                "youtube",
                "watch",
                "reddit",
                "bluesky",
                "bsky",
                "amp",
                selected_title.name.lower(),
            },
        ).generate(combined_text)

        buf = BytesIO()
        wc.to_image().save(buf, format="PNG")
        st.image(buf.getvalue(), use_container_width=True)
    except ImportError:
        st.info("Install `wordcloud` package for word cloud visualizations.")
    except Exception as e:
        st.warning(f"Could not generate word cloud: {e}")
