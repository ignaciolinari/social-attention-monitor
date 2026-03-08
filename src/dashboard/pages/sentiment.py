"""💬 Sentiment distribution page."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.api_client import get_json
from dashboard.helpers import (
    build_title_options,
    get_trending_metrics,
    num_or_zero,
    render_metrics_approximation_notice,
    render_title_picker,
    title_option_label,
)
from dashboard.pages import PageContext
from sam.config import get_settings


def render(ctx: PageContext) -> None:
    """Render the Sentiment distribution page."""
    st.header("💬 Sentiment distribution")
    selected_title = render_title_picker(
        label="Select title",
        key_prefix="sentiment",
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

    sentiment_display = st.selectbox(
        "Sentiment model",
        ["VADER", "RoBERTa", "Both"],
        index=2,
        help="Controls the sentiment model used for charts and metrics on this page.",
        key="sentiment_display",
    )

    try:
        ts = get_json(
            "/api/v1/metrics/timeseries",
            params={"title_id": selected_id, "window_hours": ctx.window_hours, "hours": ctx.hours},
        )
    except Exception as e:
        st.error(f"Failed to load sentiment data: {e}")
        return

    points = ts.get("points", [])
    if not points:
        st.info("No snapshots found for this title.")
        return
    render_metrics_approximation_notice(points, label="Sentiment charts", st_module=st)

    df = pd.DataFrame(points)
    df["snapshot_time"] = pd.to_datetime(df["snapshot_time"])
    df = df.sort_values("snapshot_time")

    # Guard against None values in raw_metrics
    if "raw_metrics" not in df.columns:
        df["raw_metrics"] = [{} for _ in range(len(df))]
    df["raw_metrics"] = df["raw_metrics"].apply(lambda x: x if isinstance(x, dict) else {})
    configured_sentiment_model = get_settings().sentiment_model

    # Resolve RoBERTa values
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

    if sentiment_display == "RoBERTa":
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
            avg_s = df["avg_sentiment"].iloc[-1]
            st.metric("Latest VADER", value=f"{num_or_zero(avg_s):.2f}")
            last_raw = df["raw_metrics"].iloc[-1]
            sec_s = (last_raw.get("sentiment_secondary") or {}).get("avg_sentiment")
            st.metric("Latest RoBERTa", value=f"{num_or_zero(sec_s):.2f}")
        else:
            avg_s = df["avg_sentiment"].iloc[-1]
            st.metric(
                f"Latest avg sentiment ({model_label})",
                value=f"{num_or_zero(avg_s):.2f}",
            )

    with c2:
        if sentiment_display == "Both":
            pr = df["positive_ratio"].iloc[-1]
            st.metric("Latest VADER positive %", value=f"{num_or_zero(pr) * 100:.1f}%")
            last_raw = df["raw_metrics"].iloc[-1]
            sec_pr = (last_raw.get("sentiment_secondary") or {}).get("positive_ratio")
            st.metric(
                "Latest RoBERTa positive %",
                value=f"{num_or_zero(sec_pr) * 100:.1f}%",
            )
        else:
            pr = df["positive_ratio"].iloc[-1]
            st.metric(
                f"Latest positive ratio ({model_label})",
                value=f"{num_or_zero(pr) * 100:.1f}%",
            )

    if sentiment_display == "Both":
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
        st.plotly_chart(fig1, use_container_width=True)

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
        st.plotly_chart(fig2, use_container_width=True)
    else:
        fig1 = px.line(
            df,
            x="snapshot_time",
            y="avg_sentiment",
            markers=True,
            title=f"Average Sentiment ({model_label})",
        )
        st.plotly_chart(fig1, use_container_width=True)

        fig2 = px.line(
            df,
            x="snapshot_time",
            y="positive_ratio",
            markers=True,
            title=f"Positive Ratio ({model_label})",
        )
        st.plotly_chart(fig2, use_container_width=True)
