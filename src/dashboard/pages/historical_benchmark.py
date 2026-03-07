"""📜 Historical Benchmark page — compare title trajectory against peers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.api_client import get_json
from dashboard.helpers import (
    build_title_options,
    get_trending_metrics,
    render_title_picker,
    title_option_label,
)
from dashboard.pages import PageContext


def _aggregate_target_points_by_day(
    target_points: list[dict[str, Any]],
    release_date: str | None,
    days: int,
) -> list[dict[str, float | int | None]]:
    if not release_date:
        return []

    try:
        release_dt = datetime.fromisoformat(release_date)
    except ValueError:
        return []

    day_buckets: dict[int, list[dict[str, Any]]] = {}
    for point in target_points:
        snapshot_time = point.get("snapshot_time")
        if not isinstance(snapshot_time, str):
            continue
        try:
            day_offset = (datetime.fromisoformat(snapshot_time) - release_dt).days
        except ValueError:
            continue
        if 0 <= day_offset < days:
            day_buckets.setdefault(day_offset, []).append(point)

    def _average(bucket: list[dict[str, Any]], key: str, precision: int) -> float | None:
        values = [float(value) for item in bucket if (value := item.get(key)) is not None]
        if not values:
            return None
        return round(sum(values) / len(values), precision)

    return [
        {
            "day": day,
            "attention_index": _average(bucket, "attention_index", 2),
            "mention_velocity": _average(bucket, "mention_velocity", 2),
            "avg_sentiment": _average(bucket, "avg_sentiment", 3),
        }
        for day, bucket in sorted(day_buckets.items())
    ]


def render(ctx: PageContext) -> None:
    """Render the Historical Benchmark page."""
    st.header("📜 Historical Benchmark")
    st.markdown("*Compare a title's early trajectory against similar past releases*")
    selected_title = render_title_picker(
        label="Select title",
        key_prefix="benchmark",
        window_hours=ctx.window_hours,
        st_module=st,
        format_func=title_option_label,
        fallback_options_loader=lambda: build_title_options(
            (get_trending_metrics(window_hours=ctx.window_hours, limit=20) or {}).get("items", [])
        ),
    )
    if selected_title is None:
        return

    col1, col2 = st.columns(2)
    with col1:
        comp_type = st.selectbox(
            "Comparison type",
            ["movie", "tv"],
            index=0 if selected_title.media_type == "movie" else 1,
            key="bench_type",
        )
    with col2:
        days = st.slider("Days from release", 1, 30, 7, key="bench_days")

    try:
        data = get_json(
            "/api/v1/metrics/benchmark",
            params={
                "title_id": selected_title.id,
                "comparison_type": comp_type,
                "days": days,
                "window_hours": ctx.window_hours,
            },
        )
    except Exception as e:
        st.error(f"Failed to load benchmark data: {e}")
        return

    target_points = data.get("target_points", [])
    avg_trajectory = data.get("avg_trajectory", [])
    comp_count = data.get("comparison_count", 0)
    comp_with_data = data.get("comparison_titles_with_data")
    title_info = data.get("title", {})

    if not target_points and not avg_trajectory:
        st.info("No benchmark data available. The title may not have a release date.")
        return

    if isinstance(comp_with_data, int):
        st.caption(
            f"Compared against {comp_count} {comp_type} titles "
            f"({comp_with_data} with benchmark data)"
        )
        if comp_count > 0 and comp_with_data == 0:
            st.warning(
                "No comparison titles have benchmark snapshots in the selected window. "
                "Average trajectory will show gaps until peer data is available."
            )
    else:
        st.caption(f"Compared against {comp_count} {comp_type} titles")

    # ── Overlay chart ────────────────────────────────────────────────────
    fig = go.Figure()

    release_date = title_info.get("release_date")
    target_daily = _aggregate_target_points_by_day(target_points, release_date, days)

    if target_daily:
        fig.add_trace(
            go.Scatter(
                x=[f"Day {int(p['day'])}" for p in target_daily],
                y=[p.get("attention_index", 0) for p in target_daily],
                mode="lines+markers",
                name=title_info.get("title", "Selected Title"),
                line={"width": 3, "color": "#4da6ff"},
            )
        )

    # Average trajectory
    if avg_trajectory:
        fig.add_trace(
            go.Scatter(
                x=[f"Day {a['day']}" for a in avg_trajectory],
                y=[a.get("avg_attention_index") for a in avg_trajectory],
                mode="lines+markers",
                name=f"Avg {comp_type.title()} ({comp_count} titles)",
                line={"width": 2, "dash": "dash", "color": "#888"},
                connectgaps=False,
            )
        )

    fig.update_layout(
        title=f"{title_info.get('title', '?')} vs. Historical Average",
        xaxis_title="Days from Release",
        yaxis_title="Attention Index",
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── Summary delta cards ──────────────────────────────────────────────
    if target_daily and avg_trajectory:
        st.subheader("📊 vs. Average Deltas")

        avg_by_day = {
            int(point["day"]): point.get("avg_attention_index")
            for point in avg_trajectory
            if point.get("avg_attention_index") is not None
        }
        comparable = [
            p
            for p in target_daily
            if p.get("attention_index") is not None and int(p["day"]) in avg_by_day
        ]

        latest_target_ai = 0.0
        latest_avg_ai = 0.0
        if comparable:
            latest_point = comparable[-1]
            latest_target_ai = float(latest_point.get("attention_index") or 0)
            latest_avg_ai = float(avg_by_day[int(latest_point["day"])])

        latest_target_vel = target_daily[-1].get("mention_velocity", 0) or 0
        latest_target_sent = target_daily[-1].get("avg_sentiment", 0) or 0

        c1, c2, c3 = st.columns(3)
        with c1:
            if comparable:
                delta = latest_target_ai - latest_avg_ai
                st.metric(
                    "Attention Index vs. Avg",
                    f"{latest_target_ai:.1f}",
                    f"{delta:+.1f}",
                )
            else:
                st.metric("Attention Index vs. Avg", "N/A")
        with c2:
            st.metric(
                "Current Velocity",
                f"{latest_target_vel:.1f}",
            )
        with c3:
            st.metric(
                "Current Sentiment",
                f"{latest_target_sent:.3f}",
            )

    # ── Average trajectory table ─────────────────────────────────────────
    if avg_trajectory:
        with st.expander("📋 Average Trajectory Data"):
            st.dataframe(
                pd.DataFrame(avg_trajectory).rename(
                    columns={
                        "day": "Day",
                        "avg_attention_index": "Avg Attention Index",
                        "sample_count": "Sample Count",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )
