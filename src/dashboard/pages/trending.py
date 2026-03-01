"""🔥 Trending Now page."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.helpers import get_trending_metrics
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
                "mention_count": m.get("mention_count"),
                "mention_velocity": m.get("mention_velocity"),
                "avg_sentiment": m.get("avg_sentiment"),
                "reddit_mentions": m.get("reddit_mentions"),
                "youtube_mentions": m.get("youtube_mentions"),
                "bluesky_mentions": m.get("bluesky_mentions"),
                "snapshot_time": m.get("snapshot_time"),
                "title_id": t["id"],
            }
        )

    df = pd.DataFrame(rows).sort_values(by="attention_index", ascending=False, na_position="last")

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
    st.dataframe(
        df.drop(columns=drop_cols, errors="ignore"),
        use_container_width=True,
        hide_index=True,
    )

    st.caption(f"Updated: {trending.get('collected_at')}")
