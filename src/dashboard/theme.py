"""Dashboard theme management — dark/light mode toggle."""

from __future__ import annotations

import streamlit as st

# ── Dark theme (default) ─────────────────────────────────────────────────────
DARK_CSS = """
<style>
    :root {
        --bg-primary: #0e1117;
        --bg-secondary: #1a1d24;
        --text-primary: #fafafa;
        --text-secondary: #b0b8c4;
        --accent: #4da6ff;
        --border: #2e333b;
    }
    .stApp { background-color: var(--bg-primary); color: var(--text-primary); }
    section[data-testid="stSidebar"] { background-color: var(--bg-secondary); }
    .stMarkdown, .stText { color: var(--text-primary); }
</style>
"""

# ── Light theme ──────────────────────────────────────────────────────────────
LIGHT_CSS = """
<style>
    :root {
        --bg-primary: #ffffff;
        --bg-secondary: #f0f2f6;
        --text-primary: #1a1a2e;
        --text-secondary: #4a4a6a;
        --accent: #0066cc;
        --border: #e0e2e8;
    }
    .stApp { background-color: var(--bg-primary); color: var(--text-primary); }
    section[data-testid="stSidebar"] { background-color: var(--bg-secondary); }
    .stMarkdown, .stText { color: var(--text-primary); }
    header[data-testid="stHeader"] { background-color: var(--bg-primary); }
    .stSelectbox label, .stRadio label, .stCheckbox label,
    .stNumberInput label, .stTextInput label {
        color: var(--text-primary) !important;
    }
    .stMetric label { color: var(--text-secondary) !important; }
    .stMetric [data-testid="stMetricValue"] { color: var(--text-primary) !important; }
    div[data-testid="stExpander"] { border-color: var(--border); }
</style>
"""


def apply_theme() -> None:
    """Inject CSS matching the user's current theme selection."""
    is_light = st.session_state.get("theme_light", False)
    css = LIGHT_CSS if is_light else DARK_CSS
    st.markdown(css, unsafe_allow_html=True)
