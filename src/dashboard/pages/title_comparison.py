"""🔎 Title Comparison page — side-by-side analytics for multiple titles."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.api_client import get_json
from dashboard.helpers import (
    build_title_options,
    get_trending_metrics,
    render_title_multiselect,
    title_option_label,
)
from dashboard.pages import PageContext


def render(ctx: PageContext) -> None:
    """Render the Title Comparison page."""
    st.header("🔎 Compare Titles")
    st.markdown("*Side-by-side analysis of attention, velocity, and sentiment across titles*")

    selected = render_title_multiselect(
        label="Select titles to compare (2-5)",
        key_prefix="compare",
        window_hours=ctx.window_hours,
        default_count=2,
        max_selections=5,
        st_module=st,
        format_func=title_option_label,
        fallback_options_loader=lambda: build_title_options(
            (get_trending_metrics(window_hours=ctx.window_hours, limit=20) or {}).get("items", [])
        ),
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
                "Hype Accel": latest.get("hype_acceleration"),
                "Mentions": latest.get("mention_count"),
                "Velocity": latest.get("mention_velocity"),
                "Avg Sentiment": latest.get("avg_sentiment"),
                "Sentiment Vol": latest.get("sentiment_volatility"),
                "Neg Ratio": latest.get("negative_ratio"),
            }
        )

    if summary_rows:
        summary_df = pd.DataFrame(summary_rows)
        st.dataframe(summary_df, use_container_width=True, hide_index=True)
        csv_bytes = summary_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📥 Download comparison as CSV",
            data=csv_bytes,
            file_name="title_comparison.csv",
            mime="text/csv",
            key="compare_csv",
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
