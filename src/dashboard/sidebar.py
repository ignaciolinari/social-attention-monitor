"""Sidebar rendering — navigation, settings, API health, collector toggles."""

from __future__ import annotations

import logging
from typing import Any

import streamlit as st

from dashboard.api_client import get_json, get_json_nocache, put_json
from dashboard.helpers import (
    DEFAULT_YOUTUBE_DAILY_BUDGET,
    time_range_to_hours,
    youtube_quota_reset_text,
)


def render_sidebar(
    page_options: list[str],
) -> tuple[str, int, int, set[str]]:
    """Render the sidebar and return ``(page, hours, window_hours, enabled_platforms)``.

    Parameters
    ----------
    page_options:
        Ordered list of page labels to show in the navigation radio.
    """

    with st.sidebar:
        st.header("🎯 Navigation")
        page: str = st.radio(
            "Select View",
            page_options,
            label_visibility="collapsed",
        )  # type: ignore[assignment]

        st.divider()
        st.header("⚙️ Settings")
        st.toggle(
            "☀️ Light Mode",
            value=st.session_state.get("theme_light", False),
            key="theme_light",
        )
        time_range = st.selectbox("Time range", ["Last 24 hours", "Last 7 days", "Last 30 days"])
        window_hours: int = st.selectbox("Metrics window", [1, 24], index=1)  # type: ignore[assignment]

        st.divider()
        st.header("🔌 API")
        from dashboard.api_client import api_base_url

        st.caption(api_base_url())
        try:
            health = get_json("/health")
            st.success(
                f"API OK (demo={health.get('demo_mode')}, "
                f"db={health.get('database_ok')}, redis={health.get('redis_ok')})"
            )
        except Exception as e:
            st.error(f"API unreachable: {e}")

        # YouTube quota mini-bar
        try:
            quota_data = get_json("/api/v1/pipeline/quota")
            yt = quota_data.get("youtube", {})
            used = yt.get("total_units", 0)
            budget = yt.get("daily_budget", DEFAULT_YOUTUBE_DAILY_BUDGET)
            used_pct = yt.get("budget_used_pct", 0)
            st.caption(f"YouTube API (PT day): {used:,} / {budget:,} units used ({used_pct:.0f}%)")
            st.progress(min(used_pct / 100, 1.0))
            st.caption(youtube_quota_reset_text())
        except Exception:
            logging.debug("Could not fetch YouTube quota for sidebar", exc_info=True)
        try:
            alert_counts = get_json("/api/v1/alerts/counts", params={"hours": 24})
            unack = alert_counts.get("unacknowledged", 0)
            if unack > 0:
                st.warning(f"🚨 {unack} unacknowledged alerts")
        except Exception:
            logging.debug("Could not fetch alert counts for sidebar", exc_info=True)
        st.divider()
        st.header("📡 Collectors")
        coll_data: dict[str, Any] | None = None
        try:
            coll_data = get_json_nocache("/api/v1/collectors/status")
            collectors = {c["platform"]: c for c in coll_data.get("collectors", [])}

            reddit_info = collectors.get("reddit", {})
            st.toggle(
                ":orange[Reddit]",
                value=reddit_info.get("enabled", False),
                disabled=True,
                key="toggle_reddit",
                help=reddit_info.get("message", "Reddit requires .env configuration"),
            )

            yt_info = collectors.get("youtube", {})
            yt_current = yt_info.get("enabled", True)
            yt_new = st.toggle(":red[YouTube]", value=yt_current, key="toggle_youtube")
            if yt_new != yt_current:
                put_json("/api/v1/collectors/youtube/toggle", params={"enabled": yt_new})
                st.rerun()

            bsky_info = collectors.get("bluesky", {})
            bsky_current = bsky_info.get("enabled", True)
            bsky_new = st.toggle(":blue[Bluesky]", value=bsky_current, key="toggle_bluesky")
            if bsky_new != bsky_current:
                put_json("/api/v1/collectors/bluesky/toggle", params={"enabled": bsky_new})
                st.rerun()

        except Exception as e:
            st.error(f"Could not load collector status: {e}")

        st.divider()
        if st.button("Refresh data"):
            get_json.clear()

        from sam import __version__

        st.caption(f"SAM v{__version__}")

    hours = time_range_to_hours(time_range)

    enabled_platforms: set[str] = {"reddit", "youtube", "bluesky"}
    if coll_data is not None:
        enabled_platforms = {
            c["platform"] for c in coll_data.get("collectors", []) if c.get("enabled")
        }

    return page, hours, window_hours, enabled_platforms
