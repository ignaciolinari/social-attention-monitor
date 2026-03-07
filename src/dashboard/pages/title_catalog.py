"""🗂️ Title Catalog page — browse all titles stored in the database."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from dashboard.api_client import get_json
from dashboard.helpers import (
    TITLE_MEDIA_TYPE_OPTIONS,
    TITLE_STATUS_OPTIONS,
    build_title_options,
    get_trending_title_options,
    title_option_label,
)
from dashboard.pages import PageContext

_TITLE_CATALOG_PAGE_SIZE = 200
_TITLE_CATALOG_MAX_PAGES = 20
_TITLE_CATALOG_DESTINATIONS = {
    "📈 Time Series": "timeseries",
    "💬 Sentiment": "sentiment",
    "🔄 Platform Comparison": "platform",
    "📊 Alpha Metrics": "alpha",
    "🌍 Language": "language",
    "📜 Benchmark": "benchmark",
    "⚖️ Sentiment Comparison": "sentiment_comparison",
}


def _load_all_titles(
    *,
    query: str | None,
    include_inactive: bool,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Load all matching DB titles by following paginated API responses."""
    titles: list[dict[str, Any]] = []
    offset = 0
    total_count = 0
    truncated = False

    for _ in range(_TITLE_CATALOG_MAX_PAGES):
        params: dict[str, Any] = {
            "include_inactive": include_inactive,
            "limit": _TITLE_CATALOG_PAGE_SIZE,
            "offset": offset,
        }
        normalized_query = (query or "").strip()
        if normalized_query:
            params["q"] = normalized_query

        data = get_json("/api/v1/db/titles", params=params)
        total_count_raw = data.get("total_count")
        if isinstance(total_count_raw, int):
            total_count = total_count_raw

        page_titles = data.get("titles", [])
        if not isinstance(page_titles, list):
            raise ValueError("Unexpected DB titles payload.")

        titles.extend(item for item in page_titles if isinstance(item, dict))
        next_offset = data.get("next_offset")
        if not isinstance(next_offset, int):
            break
        offset = next_offset
    else:
        truncated = True

    return titles, total_count or len(titles), truncated


def render(ctx: PageContext) -> None:
    """Render the DB-backed title catalog page."""
    st.header("🗂️ Title Catalog")
    st.markdown("*Browse the full title set currently stored in the SAM database*")

    search_col, media_col, status_col, trending_col = st.columns([3, 1, 1, 1])
    with search_col:
        query = st.text_input(
            "Search DB titles",
            key="title_catalog_query",
            placeholder="Filter by title name",
        )
    with media_col:
        media_type_filter_label = st.selectbox(
            "Type",
            list(TITLE_MEDIA_TYPE_OPTIONS.keys()),
            key="title_catalog_media_type",
        )
    with status_col:
        status_filter_label = st.selectbox(
            "Status",
            list(TITLE_STATUS_OPTIONS.keys()),
            index=1,
            key="title_catalog_status",
        )
    with trending_col:
        trending_only = st.checkbox(
            "Trending only",
            value=False,
            key="title_catalog_trending_only",
            help="Limit the catalog to titles currently in the trending set.",
        )
    sort_col, _ = st.columns([1, 3])
    with sort_col:
        sort_by = st.selectbox(
            "Sort by",
            ["Popularity", "Release date", "Title A-Z"],
            key="title_catalog_sort",
        )

    status_filter = TITLE_STATUS_OPTIONS[status_filter_label]
    media_type_filter = TITLE_MEDIA_TYPE_OPTIONS[media_type_filter_label]
    include_inactive = status_filter != "active"

    try:
        titles, total_count, truncated = _load_all_titles(
            query=query,
            include_inactive=include_inactive,
        )
    except Exception as e:
        st.error(f"Failed to load DB titles: {e}")
        return

    if not titles:
        if query.strip():
            st.info(f"No titles matched `{query.strip()}`.")
        else:
            st.info("No titles found in the database yet.")
        return

    df = pd.DataFrame(titles)
    if "is_active" not in df.columns:
        df["is_active"] = True
    if "media_type" not in df.columns:
        df["media_type"] = "unknown"

    trending_ids = {
        option.id for option in get_trending_title_options(window_hours=ctx.window_hours, limit=50)
    }
    df["is_trending"] = df["id"].isin(trending_ids)

    if media_type_filter != "all":
        df = df[df["media_type"] == media_type_filter]
    if status_filter == "active":
        df = df[df["is_active"].fillna(False).astype(bool)]
    elif status_filter == "inactive":
        df = df[~df["is_active"].fillna(False).astype(bool)]
    if trending_only:
        df = df[df["is_trending"]]

    if df.empty:
        st.info("No titles matched the selected filters.")
        return

    if sort_by == "Title A-Z":
        df = df.sort_values("title", ascending=True, na_position="last")
    elif sort_by == "Release date":
        df = df.sort_values("release_date", ascending=False, na_position="last")
    else:
        df = df.sort_values("popularity", ascending=False, na_position="last")

    active_count = int(df["is_active"].fillna(False).astype(bool).sum())
    inactive_count = len(df) - active_count
    trending_count = int(df["is_trending"].fillna(False).astype(bool).sum())

    metric_col1, metric_col2, metric_col3 = st.columns(3)
    with metric_col1:
        st.metric("Titles shown", len(df))
    with metric_col2:
        st.metric("Active shown", active_count)
    with metric_col3:
        st.metric("Trending shown", trending_count)

    display_columns = [
        "title",
        "media_type",
        "release_date",
        "tmdb_id",
        "popularity",
        "budget",
        "revenue",
        "is_active",
        "id",
    ]
    available_columns = [column for column in display_columns if column in df.columns]
    display_df = (
        df[available_columns]
        .copy()
        .rename(
            columns={
                "title": "Title",
                "media_type": "Type",
                "release_date": "Release Date",
                "tmdb_id": "TMDB ID",
                "popularity": "Popularity",
                "budget": "Budget",
                "revenue": "Revenue",
                "is_active": "Active",
                "id": "DB ID",
            }
        )
    )
    display_df["Title"] = [
        f"🔥 {title}" if is_trending else str(title)
        for title, is_trending in zip(display_df["Title"], df["is_trending"], strict=False)
    ]

    st.dataframe(display_df, use_container_width=True, hide_index=True)
    st.caption(
        f"Loaded {total_count} DB match(es) before client-side filters. "
        f"Inactive titles shown after filtering: {inactive_count}. `🔥` marks trending titles."
    )

    csv_bytes = display_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "📥 Download title catalog as CSV",
        data=csv_bytes,
        file_name="title_catalog.csv",
        mime="text/csv",
        key="title_catalog_csv",
    )

    if truncated:
        st.warning(
            "The title catalog was truncated while loading. "
            "Refine the search term if you need a narrower slice."
        )

    title_options = build_title_options(df.to_dict(orient="records"))
    if title_options:
        st.divider()
        st.subheader("Open Title in Analysis Tab")
        nav_col1, nav_col2, nav_col3 = st.columns([3, 2, 1])
        with nav_col1:
            selected_title = st.selectbox(
                "Selected title",
                title_options,
                format_func=title_option_label,
                key="title_catalog_jump_title",
            )
        with nav_col2:
            destination_page = st.selectbox(
                "Destination tab",
                list(_TITLE_CATALOG_DESTINATIONS.keys()),
                key="title_catalog_jump_destination",
            )
        with nav_col3:
            st.write("")
            st.write("")
            if st.button("Open tab", key="title_catalog_jump_button"):
                target_prefix = _TITLE_CATALOG_DESTINATIONS[destination_page]
                st.session_state["selected_page"] = destination_page
                st.session_state[f"{target_prefix}_pending_title_id"] = selected_title.id
                st.rerun()
