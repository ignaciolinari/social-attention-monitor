"""🔎 Title Comparison page — side-by-side analytics for multiple titles."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.api_client import get_json
from dashboard.helpers import build_title_options, get_trending_metrics, title_option_label
from dashboard.pages import PageContext


def render(ctx: PageContext) -> None:
    """Render the Title Comparison page."""
    st.header("🔎 Compare Titles")
    st.markdown("*Side-by-side analysis of attention, velocity, and sentiment across titles*")

    trending = get_trending_metrics(window_hours=ctx.window_hours, limit=20)
    if not trending or not trending.get("items"):
        st.info("No titles available yet. Populate the DB first.")
        return

    title_options = build_title_options(trending.get("items", []))
    if len(title_options) < 2:
        st.info("At least 2 titles are needed for comparison.")
        return

    selected = st.multiselect(
        "Select titles to compare (2-5)",
        title_options,
        default=title_options[:2],
        format_func=title_option_label,
        max_selections=5,
        key="compare_titles",
    )

    if len(selected) < 2:
        st.warning("Select at least 2 titles to compare.")
        return

    title_ids = ",".join(t.id for t in selected)

    try:
        data = get_json(
            "/api/v1/metrics/compare",
            params={
                "title_ids": title_ids,
                "window_hours": ctx.window_hours,
                "hours": ctx.hours,
            },
        )
    except Exception as e:
        st.error(f"Failed to load comparison data: {e}")
        return

    series_list = data.get("series", [])
    missing_ids = data.get("missing_ids", [])
    if missing_ids:
        st.warning(
            f"{len(missing_ids)} selected title(s) could not be loaded: " + ", ".join(missing_ids)
        )
    if not series_list:
        st.info("No comparison data available.")
        return

    # ── Attention Index overlay ──────────────────────────────────────────
    st.subheader("📈 Attention Index Over Time")
    _render_metric_overlay(series_list, "attention_index", "Attention Index")

    # ── Mention Velocity overlay ─────────────────────────────────────────
    st.subheader("🚀 Mention Velocity Over Time")
    _render_metric_overlay(series_list, "mention_velocity", "Mention Velocity")

    # ── Avg Sentiment overlay ────────────────────────────────────────────
    st.subheader("💬 Avg Sentiment Over Time")
    _render_metric_overlay(series_list, "avg_sentiment", "Avg Sentiment")

    # ── Summary table ────────────────────────────────────────────────────
    st.subheader("📊 Latest Snapshot Comparison")
    summary_rows = []
    for s in series_list:
        title_info = s.get("title", {})
        points = s.get("points", [])
        latest = points[-1] if points else {}
        summary_rows.append(
            {
                "Title": title_info.get("title", "?"),
                "Type": title_info.get("media_type", "?"),
                "Attention Index": latest.get("attention_index"),
                "Mentions": latest.get("mention_count"),
                "Velocity": latest.get("mention_velocity"),
                "Avg Sentiment": latest.get("avg_sentiment"),
            }
        )

    if summary_rows:
        st.dataframe(
            pd.DataFrame(summary_rows),
            use_container_width=True,
            hide_index=True,
        )


def _render_metric_overlay(
    series_list: list[dict[str, Any]],
    metric_key: str,
    label: str,
) -> None:
    """Build an overlaid line chart for a single metric across all titles."""
    fig = go.Figure()

    for s in series_list:
        title_name = s.get("title", {}).get("title", "?")
        points = s.get("points", [])
        if not points:
            continue

        times = [p["snapshot_time"] for p in points]
        values = [p.get(metric_key) for p in points]

        fig.add_trace(
            go.Scatter(
                x=times,
                y=values,
                mode="lines+markers",
                name=title_name,
            )
        )

    fig.update_layout(
        xaxis_title="Time",
        yaxis_title=label,
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
    )
    st.plotly_chart(fig, use_container_width=True)
