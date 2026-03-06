"""🌍 Language Segmentation page — geographic/linguistic breakdown of mentions."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.api_client import get_json
from dashboard.helpers import build_title_options, get_trending_metrics, title_option_label
from dashboard.pages import PageContext


def render(ctx: PageContext) -> None:
    """Render the Language Segmentation page."""
    st.header("🌍 Language Segmentation")
    st.markdown("*Understand how reception differs across languages and regions*")

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
        key="lang_title",
    )

    try:
        data = get_json(
            "/api/v1/metrics/language-breakdown",
            params={"title_id": selected_title.id, "hours": ctx.hours},
        )
    except Exception as e:
        st.error(f"Failed to load language data: {e}")
        return

    languages = data.get("languages", [])
    if not languages:
        st.info(
            "No language data available for this title. "
            "Run the collector again after fresh mentions are ingested to populate it."
        )
        return

    df = pd.DataFrame(languages)

    col1, col2 = st.columns(2)

    # ── Donut chart: volume by language ───────────────────────────────────
    with col1:
        st.subheader("📊 Mention Volume by Language")
        fig_pie = px.pie(
            df,
            values="mention_count",
            names="language",
            hole=0.4,
            title="Language Distribution",
        )
        fig_pie.update_traces(textposition="inside", textinfo="percent+label")
        st.plotly_chart(fig_pie, use_container_width=True)

    # ── Bar chart: avg sentiment by language ──────────────────────────────
    with col2:
        st.subheader("💬 Avg Sentiment by Language")
        df_sentiment = df[df["avg_sentiment"].notna()].copy()
        if not df_sentiment.empty:
            fig_bar = px.bar(
                df_sentiment.sort_values("avg_sentiment", ascending=False),
                x="language",
                y="avg_sentiment",
                color="avg_sentiment",
                color_continuous_scale="RdYlGn",
                labels={"language": "Language", "avg_sentiment": "Avg Sentiment"},
                title="Sentiment by Language",
            )
            fig_bar.update_layout(showlegend=False)
            st.plotly_chart(fig_bar, use_container_width=True)
        else:
            st.info("No sentiment data per language.")

    # ── Data table ───────────────────────────────────────────────────────
    st.subheader("📋 Language Breakdown")
    st.dataframe(
        df.rename(
            columns={
                "language": "Language",
                "mention_count": "Mentions",
                "avg_sentiment": "Avg Sentiment",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )
