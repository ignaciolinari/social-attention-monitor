"""
SAM Dashboard

Streamlit-based dashboard for visualizing social attention data.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

from sam.config import get_settings

st.set_page_config(
    page_title="SAM - Social Attention Monitor",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _api_base_url() -> str:
    # Allow explicit override
    override = os.getenv("SAM_API_BASE_URL")
    if override:
        return override.rstrip("/")

    host = os.getenv("API_HOST", "127.0.0.1")
    port = os.getenv("API_PORT", "8000")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    return f"http://{host}:{port}"


@st.cache_data(ttl=30)
def _get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = _api_base_url() + path
    timeout_s = get_settings().dashboard_http_timeout_seconds
    r = httpx.get(url, params=params, timeout=timeout_s)
    r.raise_for_status()
    data: Any = r.json()
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object from API")
    return data


def _time_range_to_hours(label: str) -> int:
    if label == "Last 24 hours":
        return 24
    if label == "Last 7 days":
        return 7 * 24
    if label == "Last 30 days":
        return 30 * 24
    return 24


def _youtube_quota_reset_text() -> str:
    """Return a human-readable countdown to the YouTube quota reset (midnight PT)."""
    from zoneinfo import ZoneInfo

    pacific = ZoneInfo("America/Los_Angeles")
    now_pt = datetime.now(pacific)
    next_midnight_pt = (now_pt + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    remaining = next_midnight_pt - now_pt
    hours_left = int(remaining.total_seconds() // 3600)
    mins_left = int((remaining.total_seconds() % 3600) // 60)
    return f"Resets in {hours_left}h {mins_left}m (midnight PT)"


def main() -> None:
    """Main dashboard entry point."""
    st.title("📊 Social Attention Monitor")
    st.markdown("*Real-time social attention tracking for film & TV releases*")

    # Sidebar
    with st.sidebar:
        st.header("🎯 Navigation")
        page = st.radio(
            "Select View",
            [
                "🔥 Trending Now",
                "📈 Time Series",
                "🔄 Platform Comparison",
                "💬 Sentiment",
                "🚨 Alerts",
                "📡 API Quota",
            ],
            label_visibility="collapsed",
        )

        st.divider()
        st.header("⚙️ Settings")
        time_range = st.selectbox("Time Range", ["Last 24 hours", "Last 7 days", "Last 30 days"])
        window_hours = st.selectbox("Metrics Window", [1, 24], index=1)

        st.divider()
        st.header("🔌 API")
        api_base = _api_base_url()
        st.caption(api_base)
        try:
            health = _get_json("/health")
            st.success(
                f"API OK (demo={health.get('demo_mode')}, db={health.get('database_ok')}, redis={health.get('redis_ok')})"
            )
        except Exception as e:
            st.error(f"API unreachable: {e}")

        # YouTube quota mini-bar
        try:
            quota_data = _get_json("/api/v1/pipeline/quota")
            yt = quota_data.get("youtube", {})
            used = yt.get("total_units", 0)
            budget = yt.get("daily_budget", 10_000)
            used_pct = yt.get("budget_used_pct", 0)
            st.caption(f"YouTube API (PT day): {used:,} / {budget:,} units used ({used_pct:.0f}%)")
            st.progress(min(used_pct / 100, 1.0))
            st.caption(_youtube_quota_reset_text())
        except Exception:
            pass

        # Show alert badge
        try:
            alert_counts = _get_json("/api/v1/alerts/counts", params={"hours": 24})
            unack = alert_counts.get("unacknowledged", 0)
            if unack > 0:
                st.warning(f"🚨 {unack} unacknowledged alerts")
        except Exception:
            pass

        st.divider()
        if st.button("Refresh data"):
            _get_json.clear()

    # Main content
    hours = _time_range_to_hours(time_range)

    trending: dict[str, Any] | None = None
    try:
        trending = _get_json(
            "/api/v1/metrics/trending",
            params={"window_hours": window_hours, "limit": 20},
        )
    except Exception as e:
        st.error(f"Failed to load trending metrics: {e}")

    if page == "🔥 Trending Now":
        st.header("🔥 Trending Now")
        if not trending:
            st.info("No trending data available yet. Run the collector first.")
            return

        items = trending.get("items", [])
        if not items:
            st.info("No metrics snapshots found. Run the collector to populate metrics.")
            return

        rows = []
        for it in items:
            t = it["title"]
            m = it["metrics"]
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
                    "snapshot_time": m.get("snapshot_time"),
                    "title_id": t["id"],
                }
            )

        df = pd.DataFrame(rows).sort_values(
            by="attention_index", ascending=False, na_position="last"
        )
        st.dataframe(
            df.drop(columns=["title_id"]),
            width="stretch",
            hide_index=True,
        )

        st.caption(f"Updated: {trending.get('collected_at')}")

    elif page == "📈 Time Series":
        st.header("📈 Time Series Analysis")
        if not trending or not trending.get("items"):
            st.info("No titles available yet. Populate the DB first.")
            return

        title_options = [
            (it["title"]["title"], it["title"]["id"]) for it in trending.get("items", [])
        ]
        selected_label = st.selectbox("Select title", [t[0] for t in title_options])
        selected_id = dict(title_options)[selected_label]

        try:
            ts = _get_json(
                "/api/v1/metrics/timeseries",
                params={"title_id": selected_id, "window_hours": window_hours, "hours": hours},
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
        st.plotly_chart(fig1, width="stretch")

        st.subheader("Attention Index over time")
        fig2 = px.line(df, x="snapshot_time", y="attention_index", markers=True)
        st.plotly_chart(fig2, width="stretch")

    elif page == "🔄 Platform Comparison":
        st.header("🔄 Platform Comparison")
        if not trending or not trending.get("items"):
            st.info("No titles available yet. Populate the DB first.")
            return

        title_options = [
            (it["title"]["title"], it["title"]["id"]) for it in trending.get("items", [])
        ]
        selected_label = st.selectbox("Select title", [t[0] for t in title_options])
        selected_id = dict(title_options)[selected_label]

        try:
            ts = _get_json(
                "/api/v1/metrics/timeseries",
                params={"title_id": selected_id, "window_hours": window_hours, "hours": hours},
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
        long = df.melt(
            id_vars=["snapshot_time"],
            value_vars=["reddit_mentions", "youtube_mentions"],
            var_name="platform",
            value_name="mentions",
        )
        fig = px.area(long, x="snapshot_time", y="mentions", color="platform", groupnorm=None)
        st.plotly_chart(fig, width="stretch")

    elif page == "💬 Sentiment":
        st.header("💬 Sentiment Distribution")
        if not trending or not trending.get("items"):
            st.info("No titles available yet. Populate the DB first.")
            return

        title_options = [
            (it["title"]["title"], it["title"]["id"]) for it in trending.get("items", [])
        ]
        selected_label = st.selectbox("Select title", [t[0] for t in title_options])
        selected_id = dict(title_options)[selected_label]

        ts = _get_json(
            "/api/v1/metrics/timeseries",
            params={"title_id": selected_id, "window_hours": window_hours, "hours": hours},
        )
        points = ts.get("points", [])
        if not points:
            st.info("No snapshots found for this title.")
            return

        df = pd.DataFrame(points)
        df["snapshot_time"] = pd.to_datetime(df["snapshot_time"])
        df = df.sort_values("snapshot_time")

        c1, c2 = st.columns(2)
        with c1:
            avg_s = df["avg_sentiment"].iloc[-1]
            st.metric("Latest avg sentiment", value=f"{(avg_s if avg_s is not None else 0.0):.2f}")
        with c2:
            pr = df["positive_ratio"].iloc[-1]
            st.metric(
                "Latest positive ratio", value=f"{(pr if pr is not None else 0.0) * 100:.1f}%"
            )

        fig1 = px.line(df, x="snapshot_time", y="avg_sentiment", markers=True)
        st.plotly_chart(fig1, width="stretch")

        fig2 = px.line(df, x="snapshot_time", y="positive_ratio", markers=True)
        st.plotly_chart(fig2, width="stretch")

    elif page == "🚨 Alerts":
        st.header("🚨 Alerts & Anomalies")
        st.markdown("*Real-time anomaly detection for tracked titles*")

        # Alert summary cards
        try:
            counts_data = _get_json("/api/v1/alerts/counts", params={"hours": hours})
            counts = counts_data.get("counts", {})
            total = counts_data.get("total", 0)
            unack = counts_data.get("unacknowledged", 0)

            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.metric("Total Alerts", total)
            with c2:
                st.metric("🔴 Critical", counts.get("critical", 0))
            with c3:
                st.metric("🟡 Warning", counts.get("warning", 0))
            with c4:
                st.metric("🟢 Info", counts.get("info", 0))

            if unack > 0:
                st.warning(f"⚠️ {unack} unacknowledged alert(s) require attention")

        except Exception as e:
            st.error(f"Failed to load alert counts: {e}")

        st.divider()

        # Recent alerts list
        st.subheader("Recent Alerts")

        severity_filter = st.selectbox("Filter by severity", ["All", "critical", "warning", "info"])

        try:
            params: dict[str, Any] = {"hours": hours, "limit": 50}
            if severity_filter != "All":
                params["severity"] = severity_filter

            alerts_data = _get_json("/api/v1/alerts", params=params)
            alerts = alerts_data.get("alerts", [])

            if not alerts:
                st.info("No alerts in the selected time range. 🎉")
            else:
                for alert in alerts:
                    severity = alert.get("severity", "info")
                    icon = {"critical": "🔴", "warning": "🟡", "info": "🟢"}.get(severity, "⚪")
                    ack_status = "✅" if alert.get("acknowledged_at") else "⏳"

                    with st.expander(
                        f"{icon} {ack_status} {alert.get('message', 'Unknown alert')[:80]}",
                        expanded=severity == "critical" and not alert.get("acknowledged_at"),
                    ):
                        col1, col2 = st.columns([3, 1])
                        with col1:
                            st.markdown(f"**Type:** `{alert.get('alert_type')}`")
                            st.markdown(f"**Severity:** {severity}")
                            st.markdown(f"**Created:** {alert.get('created_at')}")
                            if alert.get("acknowledged_at"):
                                st.markdown(f"**Acknowledged:** {alert.get('acknowledged_at')}")

                        with col2:
                            details = alert.get("details", {})
                            if details:
                                st.json(details)

        except Exception as e:
            st.error(f"Failed to load alerts: {e}")

        st.divider()

        # Pipeline health
        st.subheader("🔧 Pipeline Health")
        try:
            pipeline = _get_json("/api/v1/pipeline/health")

            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("Active Titles", pipeline.get("active_titles", 0))
            with c2:
                st.metric("Total Mentions", pipeline.get("total_mentions", 0))
            with c3:
                mentions_24h = pipeline.get("mentions_last_24h", {})
                total_24h = sum(mentions_24h.values())
                st.metric("Mentions (24h)", total_24h)

            # Newest mention ages
            ages = pipeline.get("newest_mention_age_seconds", {})
            if ages:
                st.markdown("**Data Freshness:**")
                for platform, age_sec in ages.items():
                    if age_sec is not None:
                        if age_sec < 60:
                            age_str = f"{int(age_sec)}s ago"
                        elif age_sec < 3600:
                            age_str = f"{int(age_sec / 60)}m ago"
                        else:
                            age_str = f"{age_sec / 3600:.1f}h ago"
                        st.caption(f"  • {platform}: {age_str}")
                    else:
                        st.caption(f"  • {platform}: No data")

            # Latest pipeline runs
            runs = pipeline.get("latest_pipeline_runs", [])
            if runs:
                st.markdown("**Latest Pipeline Runs:**")
                for run in runs[:5]:
                    status_icon = {"success": "✅", "failed": "❌", "running": "🔄"}.get(
                        run.get("status"), "⚪"
                    )
                    st.caption(f"  • {run.get('job_name')}: {status_icon} {run.get('status')}")

        except Exception as e:
            st.error(f"Failed to load pipeline health: {e}")

    elif page == "📡 API Quota":
        st.header("📡 API Quota Usage")
        st.markdown("*Track API usage to stay within daily limits*")

        try:
            quota_data = _get_json("/api/v1/pipeline/quota")
        except Exception as e:
            st.error(f"Failed to load quota data: {e}")
            quota_data = {}

        if quota_data:
            yt = quota_data.get("youtube", {})

            # YouTube section
            st.subheader("YouTube Data API v3")

            budget = yt.get("daily_budget", 10_000)
            used = yt.get("total_units", 0)
            remaining = yt.get("budget_remaining", budget)
            used_pct = yt.get("budget_used_pct", 0.0)
            total_calls = yt.get("total_calls", 0)
            quota_date = yt.get("date", "—")

            # Status color
            if used_pct < 50:
                status_color = "🟢"
                status_text = "Healthy"
            elif used_pct < 80:
                status_color = "🟡"
                status_text = "Moderate"
            else:
                status_color = "🔴"
                status_text = "Critical"

            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.metric("Units Used", f"{used:,}")
            with c2:
                st.metric("Remaining", f"{remaining:,}")
            with c3:
                st.metric("Total Calls", f"{total_calls:,}")
            with c4:
                st.metric("Status", f"{status_color} {status_text}")

            # Big progress bar
            st.markdown(f"**Daily Budget (PT day): {used:,} / {budget:,} units ({used_pct:.1f}%)**")
            st.progress(min(used_pct / 100, 1.0))

            # Call breakdown
            calls_by_endpoint = yt.get("calls_by_endpoint", {})
            if calls_by_endpoint:
                st.markdown("**Calls by endpoint:**")
                for endpoint, count in sorted(calls_by_endpoint.items()):
                    cost_per_call = 100 if "search" in endpoint else 1
                    st.caption(
                        f"  • `{endpoint}`: {count} calls "
                        f"({count * cost_per_call:,} units @ {cost_per_call} units/call)"
                    )

            # YouTube resets at midnight Pacific Time, not UTC.
            st.caption(f"Quota date (PT): {quota_date} — {_youtube_quota_reset_text()}")
            if quota_data.get("last_run_at"):
                st.caption(f"Last collection run: {quota_data['last_run_at']}")

            st.divider()

            # Cost reference
            st.subheader("Cost Reference")
            st.markdown("""
| Endpoint | Cost | Description |
|---|---|---|
| `search.list` | 100 units | Search for videos by query |
| `videos.list` | 1 unit | Get video statistics/details |

**Daily budget:** 10,000 units (default YouTube project quota)

**Tip:** Each title costs ~101 units (1 search + 1 video details batch).
With 20 titles, that's ~2,020 units per collection cycle.
At 5-minute polling, budget allows ~4 full cycles per day.
""")

            st.divider()
            st.subheader("TMDB API")
            st.info(
                "TMDB uses per-second rate limiting (~40 req/s), not a daily quota. "
                "No tracking needed — the built-in retry-on-429 handles it."
            )

    # Footer
    st.divider()
    col1, col2, col3 = st.columns(3)
    with col1:
        try:
            h = _get_json("/health")
            demo = h.get("demo_mode")
            st.caption("🟢 Demo Mode Active" if demo else "🔵 Live Mode Active")
        except Exception:
            st.caption("—")
    with col2:
        st.caption(f"Last updated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    with col3:
        st.caption("SAM v0.1.0")


if __name__ == "__main__":
    main()
