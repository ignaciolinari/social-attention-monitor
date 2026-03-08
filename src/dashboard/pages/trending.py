"""🔥 Trending Now page."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.helpers import get_trending_metrics, render_metrics_approximation_notice
from dashboard.pages import PageContext


def render(ctx: PageContext) -> None:
    """Render the Trending Now page."""
    st.header("🔥 Trending Now")
    trending = get_trending_metrics(window_hours=ctx.window_hours, limit=20)
    if not trending:
        st.info("No trending data available yet. Run the collector first.")
        return

    items = trending.get("items", [])
    if not items:
        st.info("No metrics snapshots found. Run the collector to populate metrics.")
        return
    render_metrics_approximation_notice(items, label="Trending rankings", st_module=st)

    rows = []
    for it in items:
        t = it.get("title")
        m = it.get("metrics")
        if not t or not m:
            continue
        rows.append(
            {
                "title": t["title"],
                "media_type": t["media_type"],
                "attention_index": m.get("attention_index"),
                "hype_acceleration": m.get("hype_acceleration"),
                "mention_count": m.get("mention_count"),
                "mention_velocity": m.get("mention_velocity"),
                "avg_sentiment": m.get("avg_sentiment"),
                "reddit_mentions": m.get("reddit_mentions"),
                "youtube_mentions": m.get("youtube_mentions"),
                "bluesky_mentions": m.get("bluesky_mentions"),
                "snapshot_time": m.get("snapshot_time"),
                "approximate": "yes" if m.get("is_approximate") else "",
                "title_id": t["id"],
            }
        )

    df = pd.DataFrame(rows).sort_values(by="attention_index", ascending=False, na_position="last")

    # Share of voice: % of total mentions each title has
    total_mentions = df["mention_count"].fillna(0).sum()
    if total_mentions > 0:
        df["share_of_voice_pct"] = (df["mention_count"].fillna(0) / total_mentions * 100).round(1)
    else:
        df["share_of_voice_pct"] = 0.0

    # Hide mention columns for disabled collectors
    drop_cols = ["title_id"]
    platform_cols = {
        "reddit": "reddit_mentions",
        "youtube": "youtube_mentions",
        "bluesky": "bluesky_mentions",
    }
    for plat, col in platform_cols.items():
        if plat not in ctx.enabled_platforms and col in df.columns:
            drop_cols.append(col)
    display_df = df.drop(columns=drop_cols, errors="ignore")
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    csv_bytes = display_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "📥 Download as CSV",
        data=csv_bytes,
        file_name="trending.csv",
        mime="text/csv",
        key="trending_csv",
    )

    st.caption(f"Updated: {trending.get('collected_at')}")
