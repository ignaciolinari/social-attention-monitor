"""📡 API Quota page."""

from __future__ import annotations

import streamlit as st

from dashboard.api_client import get_json
from dashboard.helpers import DEFAULT_YOUTUBE_DAILY_BUDGET, youtube_quota_reset_text
from dashboard.pages import PageContext


def render(ctx: PageContext) -> None:
    """Render the API Quota page."""
    st.header("📡 API quota usage")
    st.markdown("*Track API usage to stay within daily limits*")

    try:
        quota_data = get_json("/api/v1/pipeline/quota")
    except Exception as e:
        st.error(f"Failed to load quota data: {e}")
        quota_data = {}

    if not quota_data:
        return

    yt = quota_data.get("youtube", {})

    # YouTube section
    st.subheader("YouTube Data API v3")
    if "youtube" not in ctx.enabled_platforms:
        st.warning("YouTube collector is currently disabled.")
    else:
        budget = yt.get("daily_budget", DEFAULT_YOUTUBE_DAILY_BUDGET)
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
            st.metric("Units used", f"{used:,}")
        with c2:
            st.metric("Remaining", f"{remaining:,}")
        with c3:
            st.metric("Total calls", f"{total_calls:,}")
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

        st.caption(f"Quota date (PT): {quota_date} — {youtube_quota_reset_text()}")
        if quota_data.get("last_run_at"):
            st.caption(f"Last collection run: {quota_data['last_run_at']}")

        st.divider()

        # Cost reference
        st.subheader("Cost reference")
        st.markdown("""
| Endpoint | Cost | Description |
|---|---|---|
| `search.list` | 100 units | Search for videos by query |
| `videos.list` | 1 unit | Get video statistics/details |
| `commentThreads.list` | 1 unit | Fetch video comments |

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

    st.subheader("Bluesky (AT Protocol) API")
    if "bluesky" not in ctx.enabled_platforms:
        st.warning("Bluesky collector is currently disabled.")
    else:
        st.info(
            "Bluesky doesn't publish a clear daily quota like YouTube. "
            "Treat it as rate-limited (429/5xx) rather than a fixed per-day budget. "
            "No daily quota tracking needed — conservative pacing plus retry/backoff on 429 handles it."
        )
