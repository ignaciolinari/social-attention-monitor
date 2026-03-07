"""🔧 Pipeline Observability page.

Dedicated page for operational insight into the collection pipeline:
run duration trends, per-title processing times, quota burn rate,
error frequency, and translation/sentiment throughput.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.api_client import get_json
from dashboard.helpers import DEFAULT_YOUTUBE_DAILY_BUDGET, youtube_quota_reset_text
from dashboard.pages import PageContext


def render(ctx: PageContext) -> None:
    """Render the Pipeline Observability page."""
    st.header("🔧 Pipeline Observability")
    st.markdown(
        "*Run duration trends, per-title processing times, quota burn rate,"
        " and sentiment throughput.*"
    )

    # Fetch recent pipeline runs (up to 50 for meaningful charts)
    try:
        runs_resp = get_json("/api/v1/pipeline/runs", params={"limit": 50})
    except Exception as e:
        st.error(f"Failed to load pipeline runs: {e}")
        return

    runs: list[dict[str, Any]] = runs_resp.get("runs", [])
    if not runs:
        st.info("No pipeline runs recorded yet. Start the collector to populate data.")
        return

    # ── Run overview cards ────────────────────────────────
    _render_run_overview(runs)

    # ── Run duration trend ────────────────────────────────
    _render_duration_trend(runs)

    # ── Success / failure distribution ────────────────────
    _render_status_distribution(runs)

    # ── Per-title processing breakdown ────────────────────
    _render_per_title_breakdown(runs)

    # ── Quota burn rate ───────────────────────────────────
    _render_quota_burn(runs, ctx)

    # ── Sentiment & translation throughput ────────────────
    _render_sentiment_throughput(runs)

    # ── Error log ─────────────────────────────────────────
    _render_error_log(runs)


# ---------------------------------------------------------------------------
# Sub-renderers
# ---------------------------------------------------------------------------


def _render_run_overview(runs: list[dict[str, Any]]) -> None:
    """Key metrics cards."""
    st.subheader("📋 Run Overview")

    total_runs = len(runs)
    success_runs = sum(1 for r in runs if r.get("status") == "success")
    failed_runs = sum(1 for r in runs if r.get("status") == "failed")
    elapsed_values = [
        r["elapsed_seconds"] for r in runs if isinstance(r.get("elapsed_seconds"), (int, float))
    ]
    avg_duration = sum(elapsed_values) / len(elapsed_values) if elapsed_values else 0
    last_run = runs[0] if runs else None

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Total Runs (shown)", total_runs)
    with c2:
        st.metric("✅ Successful", success_runs)
    with c3:
        st.metric("❌ Failed", failed_runs)
    with c4:
        st.metric("Avg Duration", f"{avg_duration:.0f}s")
    with c5:
        if last_run:
            status_icon = {"success": "✅", "failed": "❌", "running": "🔄"}.get(
                last_run.get("status", ""), "⚪"
            )
            st.metric("Last Status", f"{status_icon} {last_run.get('status', '?')}")


def _render_duration_trend(runs: list[dict[str, Any]]) -> None:
    """Run duration over time chart."""
    rows_with_time = [
        {
            "started_at": r["started_at"],
            "duration_s": r["elapsed_seconds"],
            "status": r.get("status", "unknown"),
        }
        for r in runs
        if r.get("started_at") and isinstance(r.get("elapsed_seconds"), (int, float))
    ]
    if not rows_with_time:
        return

    st.divider()
    st.subheader("⏱️ Run Duration Trend")

    df = pd.DataFrame(rows_with_time)
    df["started_at"] = pd.to_datetime(df["started_at"])
    df = df.sort_values("started_at")

    fig = px.bar(
        df,
        x="started_at",
        y="duration_s",
        color="status",
        color_discrete_map={"success": "#22c55e", "failed": "#ef4444", "running": "#3b82f6"},
        labels={"started_at": "Run Start", "duration_s": "Duration (seconds)", "status": "Status"},
        title="Pipeline run duration over time",
    )
    fig.update_layout(bargap=0.3)
    st.plotly_chart(fig, use_container_width=True)


def _render_status_distribution(runs: list[dict[str, Any]]) -> None:
    """Pie chart of run statuses."""
    status_counts: dict[str, int] = {}
    for r in runs:
        s = r.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    if len(status_counts) <= 1 and "success" in status_counts:
        return  # No point showing a pie that's all green

    st.divider()
    st.subheader("📊 Run Status Distribution")

    fig = px.pie(
        names=list(status_counts.keys()),
        values=list(status_counts.values()),
        color=list(status_counts.keys()),
        color_discrete_map={"success": "#22c55e", "failed": "#ef4444", "running": "#3b82f6"},
        title="Run outcomes in the displayed window",
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_per_title_breakdown(runs: list[dict[str, Any]]) -> None:
    """Per-title processing times extracted from run stats."""
    # Collect per-title timing from the most recent successful run with stats
    latest_stats: dict[str, Any] = {}
    for r in runs:
        if r.get("status") != "success":
            continue
        stats = r.get("stats")
        if isinstance(stats, dict) and stats.get("per_title_ms"):
            latest_stats = stats
            break

    per_title_ms = latest_stats.get("per_title_ms")
    if not isinstance(per_title_ms, dict) or not per_title_ms:
        # Try alternative: titles_processed list with timing
        titles_processed = latest_stats.get("titles_processed")
        if isinstance(titles_processed, list) and titles_processed:
            _render_titles_processed_table(titles_processed)
        return

    st.divider()
    st.subheader("🎬 Per-Title Processing Time (Latest Run)")

    title_rows = [
        {"title": title, "ms": float(ms)}
        for title, ms in per_title_ms.items()
        if isinstance(ms, (int, float))
    ]
    if not title_rows:
        return

    df = pd.DataFrame(title_rows).sort_values("ms", ascending=False)
    fig = px.bar(
        df,
        x="title",
        y="ms",
        labels={"title": "Title", "ms": "Processing Time (ms)"},
        title="Per-title pipeline processing time",
        color="ms",
        color_continuous_scale=["#22c55e", "#fbbf24", "#ef4444"],
    )
    fig.update_layout(coloraxis_showscale=False, xaxis_tickangle=-45)
    st.plotly_chart(fig, use_container_width=True)


def _render_titles_processed_table(titles: list[Any]) -> None:
    """Fallback: show titles_processed as a table if per_title_ms is unavailable."""
    st.divider()
    st.subheader("🎬 Titles Processed (Latest Run)")

    rows = []
    for t in titles:
        if isinstance(t, dict):
            rows.append(
                {
                    "Title": t.get("title", t.get("name", "?")),
                    "Mentions": t.get("mention_count", t.get("mentions", "?")),
                    "Duration (ms)": t.get("elapsed_ms", t.get("duration_ms", "—")),
                }
            )
        elif isinstance(t, str):
            rows.append({"Title": t})
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_quota_burn(runs: list[dict[str, Any]], _ctx: PageContext) -> None:
    """YouTube quota units consumed per run over time."""
    quota_rows = []
    for r in runs:
        stats = r.get("stats")
        if not isinstance(stats, dict):
            continue
        yt_units = stats.get("youtube_quota_units") or stats.get("quota_units_used")
        if not isinstance(yt_units, (int, float)):
            api_quota = stats.get("api_quota")
            if isinstance(api_quota, dict):
                youtube = api_quota.get("youtube", {})
                if isinstance(youtube, dict):
                    yt_units = youtube.get("total_units")
        if isinstance(yt_units, (int, float)) and r.get("started_at"):
            quota_rows.append({"started_at": r["started_at"], "units": int(yt_units)})

    if not quota_rows:
        # Fall back to current quota endpoint
        try:
            quota_data = get_json("/api/v1/pipeline/quota")
            yt = quota_data.get("youtube", {})
            used = yt.get("total_units", 0)
            budget = yt.get("daily_budget", DEFAULT_YOUTUBE_DAILY_BUDGET)
            if used > 0:
                st.divider()
                st.subheader("📡 YouTube Quota Burn")
                st.metric("Today's Usage", f"{used:,} / {budget:,} units")
                st.progress(min(used / max(budget, 1), 1.0))
                st.caption(youtube_quota_reset_text())
        except Exception:
            logging.debug("Could not fetch quota data for YouTube Quota Burn", exc_info=True)
        return

    st.divider()
    st.subheader("📡 YouTube Quota Burn Rate")

    df = pd.DataFrame(quota_rows)
    df["started_at"] = pd.to_datetime(df["started_at"])
    df = df.sort_values("started_at")
    df["cumulative_units"] = df["units"].cumsum()

    fig = px.area(
        df,
        x="started_at",
        y="cumulative_units",
        labels={"started_at": "Run Start", "cumulative_units": "Cumulative Units"},
        title="YouTube API quota consumption over time",
    )
    st.plotly_chart(fig, use_container_width=True)

    # Per-run bar
    fig2 = px.bar(
        df,
        x="started_at",
        y="units",
        labels={"started_at": "Run Start", "units": "Units Used"},
        title="Quota units per run",
    )
    st.plotly_chart(fig2, use_container_width=True)
    st.caption(youtube_quota_reset_text())


def _render_sentiment_throughput(runs: list[dict[str, Any]]) -> None:
    """Sentiment analysis and translation throughput across runs."""
    throughput_rows = []
    for r in runs:
        stats = r.get("stats")
        if not isinstance(stats, dict) or not r.get("started_at"):
            continue

        row: dict[str, Any] = {"started_at": r["started_at"]}
        has_data = False

        for key in (
            "sentiment_ms_total",
            "translate_attempted",
            "translate_count",
            "translate_failures",
            "translate_skipped_english",
            "mentions_inserted",
            "spam_filtered",
            "duplicates_removed",
            "matching_filtered",
            "languages_detected",
            "raw_storage_failures",
            "mentions_capped_titles",
            "titles_deactivated",
        ):
            val = stats.get(key)
            if isinstance(val, (int, float)):
                row[key] = val
                has_data = True

        if has_data:
            throughput_rows.append(row)

    if not throughput_rows:
        return

    st.divider()
    st.subheader("🧠 Sentiment & Translation Throughput")

    df = pd.DataFrame(throughput_rows)
    df["started_at"] = pd.to_datetime(df["started_at"])
    df = df.sort_values("started_at")

    # Sentiment analysis time trend
    if "sentiment_ms_total" in df.columns and df["sentiment_ms_total"].sum() > 0:
        fig = px.line(
            df,
            x="started_at",
            y="sentiment_ms_total",
            markers=True,
            labels={"started_at": "Run Start", "sentiment_ms_total": "Sentiment Time (ms)"},
            title="Sentiment analysis duration per run",
        )
        st.plotly_chart(fig, use_container_width=True)

    # Translation stats
    translate_cols = [
        c
        for c in ("translate_attempted", "translate_count", "translate_failures")
        if c in df.columns
    ]
    if translate_cols and df[translate_cols].sum().sum() > 0:
        long = df.melt(
            id_vars=["started_at"],
            value_vars=translate_cols,
            var_name="metric",
            value_name="count",
        )
        long["metric"] = (
            long["metric"].str.replace("translate_", "").str.replace("_", " ").str.title()
        )
        fig2 = px.bar(
            long,
            x="started_at",
            y="count",
            color="metric",
            barmode="group",
            labels={"started_at": "Run Start", "count": "Count", "metric": "Metric"},
            title="Translation activity per run",
        )
        st.plotly_chart(fig2, use_container_width=True)

    # Data quality metrics
    quality_cols = [
        c
        for c in (
            "mentions_inserted",
            "spam_filtered",
            "duplicates_removed",
            "matching_filtered",
            "languages_detected",
            "raw_storage_failures",
            "mentions_capped_titles",
            "titles_deactivated",
        )
        if c in df.columns
    ]
    if quality_cols and df[quality_cols].sum().sum() > 0:
        long_q = df.melt(
            id_vars=["started_at"],
            value_vars=quality_cols,
            var_name="metric",
            value_name="count",
        )
        long_q["metric"] = long_q["metric"].str.replace("_", " ").str.title()
        fig3 = px.bar(
            long_q,
            x="started_at",
            y="count",
            color="metric",
            barmode="group",
            labels={"started_at": "Run Start", "count": "Count", "metric": "Metric"},
            title="Data quality metrics per run",
        )
        st.plotly_chart(fig3, use_container_width=True)

    latest_with_caps = next(
        (
            r
            for r in runs
            if isinstance(r.get("stats"), dict)
            and isinstance(r["stats"].get("mentions_capped_title_names"), list)
            and r["stats"]["mentions_capped_title_names"]
        ),
        None,
    )
    if latest_with_caps is not None:
        capped_titles = latest_with_caps["stats"]["mentions_capped_title_names"]
        st.caption("Titles that hit the snapshot mention cap in the latest affected run:")
        st.write(", ".join(str(title) for title in capped_titles))


def _render_error_log(runs: list[dict[str, Any]]) -> None:
    """Show recent failures with their error messages."""
    failed = [r for r in runs if r.get("status") == "failed" and r.get("error")]
    if not failed:
        return

    st.divider()
    st.subheader("🚫 Recent Failures")

    for r in failed[:10]:
        with st.expander(f"❌ {r.get('started_at', 'Unknown time')} — {r.get('job_name', '')}"):
            st.code(r.get("error", "No error message"), language="text")
            stats = r.get("stats")
            if isinstance(stats, dict) and stats:
                st.json(stats)
