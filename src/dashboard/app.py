"""
SAM Dashboard

Streamlit-based dashboard for visualizing social attention data.

Page modules live in :mod:`dashboard.pages.*`, sidebar logic in
:mod:`dashboard.sidebar`, HTTP helpers in :mod:`dashboard.api_client`,
shared utilities in :mod:`dashboard.helpers`, and heavyweight NLP
comparison logic in :mod:`dashboard.sentiment_analysis`.

This file is the thin entry-point / dispatcher only.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from dashboard.api_client import api_base_url, get_json
from dashboard.helpers import DASHBOARD_REFRESH_MINUTES
from dashboard.pages import PageContext
from dashboard.pages import alerts as page_alerts
from dashboard.pages import alpha_metrics as page_alpha
from dashboard.pages import pipeline_observability as page_observability
from dashboard.pages import platform_comparison as page_platform
from dashboard.pages import quota as page_quota
from dashboard.pages import sentiment as page_sentiment
from dashboard.pages import sentiment_comparison as page_sentiment_cmp
from dashboard.pages import timeseries as page_timeseries
from dashboard.pages import trending as page_trending
from dashboard.sidebar import render_sidebar

st.set_page_config(
    page_title="SAM - Social Attention Monitor",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st_autorefresh(interval=DASHBOARD_REFRESH_MINUTES * 60 * 1000, key="data_refresh")


# Page label -> renderer mapping
_PAGES = {
    "🔥 Trending Now": page_trending.render,
    "📈 Time Series": page_timeseries.render,
    "🔄 Platform Comparison": page_platform.render,
    "💬 Sentiment": page_sentiment.render,
    "⚖️ Sentiment Comparison": page_sentiment_cmp.render,
    "📊 Alpha Metrics": page_alpha.render,
    "🚨 Alerts": page_alerts.render,
    "📡 API Quota": page_quota.render,
    "🔧 Pipeline Observability": page_observability.render,
}


def main() -> None:
    """Main dashboard entry point."""
    st.title("📊 Social Attention Monitor")
    st.markdown("*Real-time social attention tracking for film & TV releases*")

    # Early API connectivity check — fail fast (3s) if API is unreachable
    base = api_base_url()
    try:
        with st.spinner("Connecting to API…"):
            r = httpx.get(f"{base}/health", timeout=3.0)
            r.raise_for_status()
    except Exception as e:
        st.error(
            f"**Cannot connect to SAM API** at `{base}`. "
            "Start it with: `make run-api` or `uvicorn sam.api.main:app --port 8000`"
        )
        st.exception(e)
        st.stop()

    page, hours, window_hours, enabled_platforms = render_sidebar(list(_PAGES.keys()))

    ctx = PageContext(
        hours=hours,
        window_hours=window_hours,
        enabled_platforms=enabled_platforms,
    )

    renderer = _PAGES.get(page)
    if renderer is not None:
        renderer(ctx)

    # Footer
    st.divider()
    col1, col2, col3 = st.columns(3)
    with col1:
        try:
            h = get_json("/health")
            demo = h.get("demo_mode")
            st.caption("🟢 Demo Mode Active" if demo else "🔵 Live Mode Active")
        except Exception:
            st.caption("—")
    with col2:
        st.caption(f"Last updated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    with col3:
        from sam import __version__

        st.caption(f"SAM v{__version__}")


if __name__ == "__main__":
    main()
