"""🚨 Alerts and pipeline health page."""

from __future__ import annotations

from typing import Any

import streamlit as st

from dashboard.api_client import get_json, post_json
from dashboard.pages import PageContext


def render(ctx: PageContext) -> None:
    """Render the Alerts and anomalies page."""
    st.header("🚨 Alerts and anomalies")
    st.markdown("*Real-time anomaly detection for tracked titles*")

    # Alert summary cards
    try:
        counts_data = get_json("/api/v1/alerts/counts", params={"hours": ctx.hours})
        counts = counts_data.get("counts", {})
        total = counts_data.get("total", 0)
        unack = counts_data.get("unacknowledged", 0)

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Total alerts", total)
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
    st.subheader("Recent alerts")

    severity_filter = st.selectbox(
        "Filter by severity",
        ["All", "critical", "warning", "info"],
        key="alert_severity_filter",
    )
    show_acknowledged = st.toggle("Show acknowledged alerts", value=False)

    try:
        params: dict[str, Any] = {"hours": ctx.hours, "limit": 50}
        if severity_filter != "All":
            params["severity"] = severity_filter

        alerts_data = get_json("/api/v1/alerts", params=params)
        alerts = alerts_data.get("alerts", [])
        if not show_acknowledged:
            alerts = [a for a in alerts if not a.get("acknowledged_at")]

        if not alerts:
            st.info("No alerts in the selected time range. 🎉")
        else:
            for alert in alerts:
                severity = alert.get("severity", "info")
                icon = {"critical": "🔴", "warning": "🟡", "info": "🟢"}.get(severity, "⚪")
                alert_type = alert.get("alert_type", "")
                type_icon = {
                    "mention_spike": "🔥",
                    "sentiment_shift": "📉",
                    "velocity_surge": "⚡",
                    "viral_breakout": "🚀",
                    "attention_spike": "🎯",
                    "sentiment_divergence": "🔀",
                    "diversity_drop": "🔄",
                }.get(alert_type, "")
                ack_status = "✅" if alert.get("acknowledged_at") else "⏳"

                with st.expander(
                    f"{icon} {type_icon} {ack_status} {alert.get('message', 'Unknown alert')[:80]}",
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
                        if (not alert.get("acknowledged_at")) and st.button(
                            "Acknowledge",
                            key=f"ack_{alert.get('id')}",
                            type="secondary",
                            use_container_width=True,
                        ):
                            try:
                                post_json(f"/api/v1/alerts/{alert.get('id')}/acknowledge")
                                get_json.clear()
                                st.rerun()
                            except Exception as exc:
                                st.error(f"Failed to acknowledge alert: {exc}")

    except Exception as e:
        st.error(f"Failed to load alerts: {e}")

    st.divider()

    # Pipeline health
    _render_pipeline_health(ctx)


def _render_pipeline_health(_ctx: PageContext) -> None:
    """Render the pipeline health summary widget."""
    st.subheader("🔧 Pipeline health")
    try:
        pipeline = get_json("/api/v1/pipeline/health")

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
                status_icon = {
                    "success": "✅",
                    "degraded": "⚠️",
                    "failed": "❌",
                    "running": "🔄",
                }.get(run.get("status"), "⚪")
                st.caption(f"  • {run.get('job_name')}: {status_icon} {run.get('status')}")

        sentiment_stats = pipeline.get("sentiment_stats") or {}
        if sentiment_stats:
            st.markdown("**Sentiment Processing:**")
            c4, c5, c6, c7, c8 = st.columns(5)
            with c4:
                st.metric(
                    "Translated (attempted)",
                    int(sentiment_stats.get("translate_attempted", 0)),
                )
            with c5:
                st.metric("Translated (changed)", int(sentiment_stats.get("translate_count", 0)))
            with c6:
                st.metric(
                    "Skipped (English)",
                    int(sentiment_stats.get("translate_skipped_english", 0)),
                )
            with c7:
                st.metric("Translation Failures", int(sentiment_stats.get("translate_failures", 0)))
            with c8:
                st.metric(
                    "Sentiment Time",
                    f"{float(sentiment_stats.get('sentiment_ms_total', 0.0)):.0f} ms",
                )

    except Exception as e:
        st.error(f"Failed to load pipeline health: {e}")
