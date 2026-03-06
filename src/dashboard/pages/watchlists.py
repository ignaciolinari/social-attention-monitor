"""📌 Watchlists page — manage user-defined watchlists."""

from __future__ import annotations

import streamlit as st

from dashboard.api_client import delete_json, get_json, post_json, put_json
from dashboard.pages import PageContext


def _parse_tmdb_ids(raw_ids: str) -> list[int]:
    if not raw_ids.strip():
        return []
    return [int(value.strip()) for value in raw_ids.split(",") if value.strip()]


def render(_ctx: PageContext) -> None:
    """Render the Watchlists management page."""
    st.header("📌 Watchlists")
    st.markdown("*Create and manage custom watchlists to track specific titles*")

    # ── Create new watchlist ─────────────────────────────────────────────
    with st.expander("➕ Create New Watchlist", expanded=False):
        new_name = st.text_input("Watchlist name", key="wl_new_name")
        new_ids_str = st.text_input(
            "TMDB IDs (comma-separated)",
            key="wl_new_ids",
            help="e.g. 1396, 438631, 95396",
        )
        if st.button("Create Watchlist", key="wl_create_btn"):
            if not new_name.strip():
                st.error("Please enter a watchlist name.")
            else:
                tmdb_ids: list[int] = []
                if new_ids_str.strip():
                    try:
                        tmdb_ids = _parse_tmdb_ids(new_ids_str)
                    except ValueError:
                        st.error("Invalid TMDB IDs. Use comma-separated integers.")
                        return

                try:
                    post_json(
                        "/api/v1/watchlists",
                        json_body={"name": new_name.strip(), "tmdb_ids": tmdb_ids},
                    )
                    st.success(f"Created watchlist '{new_name.strip()}'!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to create watchlist: {e}")

    # ── List existing watchlists ─────────────────────────────────────────
    try:
        data = get_json("/api/v1/watchlists", params={"limit": 50})
    except Exception as e:
        st.error(f"Failed to load watchlists: {e}")
        return

    watchlists = data.get("watchlists", [])
    total = data.get("total_count", 0)

    if not watchlists:
        st.info("No watchlists yet. Create one above to start tracking custom titles.")
        return

    st.caption(f"Total watchlists: {total}")

    for wl in watchlists:
        wl_id = wl.get("id", "")
        wl_name = wl.get("name", "Untitled")
        tmdb_ids = wl.get("tmdb_ids", [])

        with st.container():
            col1, col2, col3 = st.columns([4, 2, 1])
            with col1:
                st.markdown(f"**{wl_name}**")
                if tmdb_ids:
                    st.caption(
                        f"TMDB IDs: {', '.join(str(i) for i in tmdb_ids[:10])}"
                        + (f" … +{len(tmdb_ids) - 10} more" if len(tmdb_ids) > 10 else "")
                    )
                else:
                    st.caption("No titles added yet")
            with col2:
                st.caption(f"Created: {wl.get('created_at', '—')[:10]}")
            with col3:
                if st.button("🗑️", key=f"wl_del_{wl_id}", help="Delete this watchlist"):
                    try:
                        delete_json(f"/api/v1/watchlists/{wl_id}")
                        st.success(f"Deleted '{wl_name}'")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to delete: {e}")

            with st.expander("Edit watchlist", expanded=False):
                edited_name = st.text_input("Watchlist name", value=wl_name, key=f"wl_name_{wl_id}")
                edited_ids = st.text_input(
                    "TMDB IDs (comma-separated)",
                    value=", ".join(str(i) for i in tmdb_ids),
                    key=f"wl_ids_{wl_id}",
                )
                if st.button("Save changes", key=f"wl_save_{wl_id}"):
                    if not edited_name.strip():
                        st.error("Please enter a watchlist name.")
                    else:
                        try:
                            parsed_ids = _parse_tmdb_ids(edited_ids)
                        except ValueError:
                            st.error("Invalid TMDB IDs. Use comma-separated integers.")
                        else:
                            try:
                                put_json(
                                    f"/api/v1/watchlists/{wl_id}",
                                    json_body={
                                        "name": edited_name.strip(),
                                        "tmdb_ids": parsed_ids,
                                    },
                                )
                                st.success(f"Updated '{edited_name.strip()}'")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed to update watchlist: {e}")

            st.divider()
