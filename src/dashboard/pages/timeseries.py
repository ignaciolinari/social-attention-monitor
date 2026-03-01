"""📈 Time Series page."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.api_client import get_json
from dashboard.helpers import build_title_options, get_trending_metrics, title_option_label
from dashboard.pages import PageContext


def render(ctx: PageContext) -> None:
    """Render the Time Series page."""
    st.header("📈 Time series analysis")
    trending = get_trending_metrics(window_hours=ctx.window_hours, limit=20)
    if not trending or not trending.get("items"):
        st.info("No titles available yet. Populate the DB first.")
        return

    title_options = build_title_options(trending.get("items", []))
    if not title_options:
        st.info("No title options available yet.")
        return
    selected_title = st.selectbox(
        "Select title",
        title_options,
        format_func=title_option_label,
        key="timeseries_title",
    )
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
