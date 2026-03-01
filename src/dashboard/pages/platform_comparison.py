"""🔄 Platform Comparison page."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.api_client import get_json
from dashboard.helpers import (
    PLATFORM_COLORS,
    build_title_options,
    get_trending_metrics,
    title_option_label,
)
from dashboard.pages import PageContext


def render(ctx: PageContext) -> None:
    """Render the Platform Comparison page."""
    st.header("🔄 Platform Comparison")
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
        key="platform_title",
    )
    selected_id = selected_title.id

    try:
        ts = get_json(
            "/api/v1/metrics/timeseries",
            params={"title_id": selected_id, "window_hours": ctx.window_hours, "hours": ctx.hours},
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
    plat_vars = []
    if "reddit" in ctx.enabled_platforms:
        plat_vars.append("reddit_mentions")
    if "youtube" in ctx.enabled_platforms:
        plat_vars.append("youtube_mentions")
    if "bluesky" in ctx.enabled_platforms:
        plat_vars.append("bluesky_mentions")

    if not plat_vars:
        st.info("All collectors are currently disabled.")
        return

    # Only keep vars that actually exist in the DataFrame to avoid KeyError
    plat_vars = [v for v in plat_vars if v in df.columns]

    if not plat_vars:
        st.info("No platform data available for the selected titles.")
        return

    long = df.melt(
        id_vars=["snapshot_time"],
        value_vars=plat_vars,
        var_name="platform",
        value_name="mentions",
    )
    fig = px.area(
        long,
        x="snapshot_time",
        y="mentions",
        color="platform",
        groupnorm=None,
        color_discrete_map=PLATFORM_COLORS,
    )
    st.plotly_chart(fig, use_container_width=True)
