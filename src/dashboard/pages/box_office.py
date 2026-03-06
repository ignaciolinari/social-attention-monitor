"""💰 Box Office page — correlation between social attention and commercial performance."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.api_client import get_json
from dashboard.pages import PageContext


def render(ctx: PageContext) -> None:
    """Render the Box Office correlation page."""
    st.header("💰 Box Office Correlation")
    st.markdown("*Explore the relationship between social attention and commercial performance*")

    try:
        data = get_json(
            "/api/v1/metrics/box-office",
            params={"window_hours": ctx.window_hours, "limit": 30},
        )
    except Exception as e:
        st.error(f"Failed to load box office data: {e}")
        return

    items = data.get("items", [])
    if not items:
        st.info("No box office data available. Run the collector to populate data.")
        return

    df = pd.DataFrame(items)

    # Filter to only titles with revenue data
    df_revenue = df[df["revenue"].notna() & (df["revenue"] > 0)].copy()

    if df_revenue.empty:
        st.info(
            "No revenue data available yet. Revenue data is fetched from TMDB "
            "and is available for movies only."
        )
        _render_all_titles_table(df)
        return

    # ── Scatter: Attention Index vs Revenue ──────────────────────────────
    st.subheader("📈 Attention Index vs. Revenue")
    fig_scatter = px.scatter(
        df_revenue,
        x="attention_index",
        y="revenue",
        size="mention_count",
        color="avg_sentiment",
        hover_name="title",
        color_continuous_scale="RdYlGn",
        labels={
            "attention_index": "Attention Index",
            "revenue": "Revenue ($)",
            "mention_count": "Mentions",
            "avg_sentiment": "Avg Sentiment",
        },
        title="Social Attention vs. Box Office Revenue",
    )
    fig_scatter.update_layout(
        xaxis_title="Attention Index",
        yaxis_title="Revenue ($)",
        yaxis_tickprefix="$",
    )
    st.plotly_chart(fig_scatter, use_container_width=True)

    # ── ROI bar chart ────────────────────────────────────────────────────
    df_roi = df_revenue[df_revenue["budget"].notna() & (df_revenue["budget"] > 0)].copy()
    if not df_roi.empty:
        st.subheader("💵 ROI (Revenue / Budget)")
        df_roi["roi"] = df_roi["revenue"] / df_roi["budget"]
        df_roi = df_roi.sort_values("roi", ascending=False)

        fig_roi = px.bar(
            df_roi,
            x="title",
            y="roi",
            color="attention_index",
            color_continuous_scale="Viridis",
            labels={"roi": "ROI (×)", "title": "Title", "attention_index": "Attention Index"},
            title="Return on Investment by Title",
        )
        fig_roi.update_layout(xaxis_tickangle=-45)
        st.plotly_chart(fig_roi, use_container_width=True)

    # ── Full table ───────────────────────────────────────────────────────
    _render_all_titles_table(df_revenue)


def _render_all_titles_table(df: pd.DataFrame) -> None:
    st.subheader("📊 Data Table")
    display_cols = ["title", "media_type", "attention_index", "mention_count", "avg_sentiment"]
    if "revenue" in df.columns:
        display_cols.extend(["revenue", "budget"])
    available = [c for c in display_cols if c in df.columns]
    st.dataframe(
        df[available].sort_values("attention_index", ascending=False, na_position="last"),
        use_container_width=True,
        hide_index=True,
    )
