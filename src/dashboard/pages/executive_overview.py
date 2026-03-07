"""📋 Executive Overview — landing page with top titles, system health, alerts, key metrics."""

from __future__ import annotations

import streamlit as st

from dashboard.api_client import get_json
from dashboard.helpers import get_trending_metrics
from dashboard.pages import PageContext


def render(ctx: PageContext) -> None:
    """Render the Executive Overview landing page."""
    st.header("📋 Executive Overview")
    st.markdown("*At-a-glance summary: top titles, system health, recent alerts, and key metrics*")

    # Four main sections in 2x2 grid
    col_top, col_health = st.columns(2)

    with col_top:
        st.subheader("🔥 Top Titles")
        trending = get_trending_metrics(window_hours=ctx.window_hours, limit=5)
        items = trending.get("items", []) if trending else []
        if not items:
            st.info("No trending data yet. Run the collector first.")
        else:
            for i, it in enumerate(items[:5], 1):
                t = it.get("title", {})
                m = it.get("metrics", {})
                ai = m.get("attention_index") or 0
                mentions = m.get("mention_count") or 0
                st.caption(f"**{i}.** {t.get('title', '?')} — AI: {ai:.1f} · {mentions} mentions")
            st.caption(f"*Window: {ctx.window_hours}h · Use Trending for full table*")

    with col_health:
        st.subheader("🏥 System Health")
        try:
            health = get_json("/api/v1/alerts/system-health")
            if health.get("healthy", True) and not health.get("issues", []):
                st.success("All systems operational")
            else:
                issues = health.get("issues", [])
                for iss in issues[:3]:
                    sev = iss.get("severity", "info")
                    icon = {"critical": "🔴", "warning": "🟡", "info": "🟢"}.get(sev, "⚪")
                    st.warning(f"{icon} {iss.get('message', '')[:60]}")
                if len(issues) > 3:
                    st.caption(f"… and {len(issues) - 3} more")
            st.caption("*Use Alerts page for details*")
        except Exception as e:
            st.error(f"Health check failed: {e}")

    st.divider()

    col_alerts, col_metrics = st.columns(2)

    with col_alerts:
        st.subheader("🚨 Recent Alerts")
        try:
            counts = get_json("/api/v1/alerts/counts", params={"hours": ctx.hours})
            total = counts.get("total", 0)
            unack = counts.get("unacknowledged", 0)
            by_sev = counts.get("counts", {})
            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("Total", total)
            with c2:
                st.metric("Critical", by_sev.get("critical", 0))
            with c3:
                st.metric("Unack", unack)
            alerts_data = get_json(
                "/api/v1/alerts",
                params={"hours": ctx.hours, "limit": 3, "unacknowledged_only": True},
            )
            alerts = alerts_data.get("alerts", [])
            for a in alerts:
                st.caption(f"• {a.get('message', '')[:55]}…")
            st.caption("*Use Alerts page for full list*")
        except Exception as e:
            st.error(f"Alerts failed: {e}")

    with col_metrics:
        st.subheader("📊 Pipeline Metrics")
        try:
            pipeline = get_json("/api/v1/pipeline/health")
            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("Active Titles", pipeline.get("active_titles", 0))
            with c2:
                total_m = pipeline.get("total_mentions", 0)
                st.metric("Total Mentions", f"{total_m:,}")
            with c3:
                m24 = pipeline.get("mentions_last_24h", {})
                st.metric("24h Mentions", sum(m24.values()))
            runs = pipeline.get("latest_pipeline_runs", [])
            if runs:
                last = runs[0]
                status = last.get("status", "?")
                icon = {"success": "✅", "failed": "❌", "degraded": "⚠️"}.get(status, "⚪")
                st.caption(f"Last run: {icon} {status}")
            st.caption("*Use Pipeline Observability for details*")
        except Exception as e:
            st.error(f"Pipeline metrics failed: {e}")
