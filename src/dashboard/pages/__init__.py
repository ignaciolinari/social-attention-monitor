"""Dashboard page modules.

Each page function receives a :class:`PageContext` and renders its
Streamlit UI.  The thin ``app.py`` dispatcher calls the appropriate
page based on sidebar selection.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PageContext:
    """Shared state passed from the main dispatcher to every page."""

    hours: int
    """Time-range filter converted to integer hours."""

    window_hours: int
    """Metrics snapshot window (1 or 24)."""

    enabled_platforms: set[str]
    """Platforms whose collectors are currently enabled."""
