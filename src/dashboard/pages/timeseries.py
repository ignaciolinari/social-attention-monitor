"""📈 Time Series page."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.api_client import get_json
from dashboard.helpers import (
    build_title_options,
    get_trending_metrics,
    render_title_picker,
    title_option_label,
)
from dashboard.pages import PageContext


def render(ctx: PageContext) -> None:
    """Render the Time Series page."""
    st.header("📈 Time series analysis")
    selected_title = render_title_picker(
        label="Select title",
        key_prefix="timeseries",
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
    st.plotly_chart(fig1, use_container_width=True)

    st.subheader("Attention index over time")
    fig2 = px.line(df, x="snapshot_time", y="attention_index", markers=True)
    st.plotly_chart(fig2, use_container_width=True)

    if "hype_acceleration" in df.columns and df["hype_acceleration"].notna().any():
        st.subheader("Hype acceleration over time")
        st.caption("Second derivative of mentions — positive = hype building, negative = declining")
        fig3 = px.line(df, x="snapshot_time", y="hype_acceleration", markers=True)
        st.plotly_chart(fig3, use_container_width=True)

    if "sentiment_volatility" in df.columns and df["sentiment_volatility"].notna().any():
        st.subheader("Sentiment volatility over time")
        st.caption("Standard deviation of sentiment scores — higher = more polarized discussion")
        fig4 = px.line(df, x="snapshot_time", y="sentiment_volatility", markers=True)
        st.plotly_chart(fig4, use_container_width=True)
