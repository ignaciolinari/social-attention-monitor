"""HTTP helpers for communicating with the SAM API."""

from __future__ import annotations

import os
from typing import Any

import httpx
import streamlit as st

from sam.config import get_settings


def api_base_url() -> str:
    """Return the SAM API base URL from env vars or defaults."""
    override = os.getenv("SAM_API_BASE_URL")
    if override:
        return override.rstrip("/")

    host = os.getenv("API_HOST", "127.0.0.1")
    port = os.getenv("API_PORT", "8000")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    return f"http://{host}:{port}"


def _auth_headers() -> dict[str, str]:
    """Return API key header if SAM_API_KEY is set."""
    key = os.getenv("SAM_API_KEY", "")
    return {"X-API-Key": key} if key else {}


@st.cache_data(ttl=30, show_spinner="Connecting to SAM API…")
def get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Cached GET request returning parsed JSON dict (TTL 30 s)."""
    url = api_base_url() + path
    timeout_s = get_settings().dashboard_http_timeout_seconds
    r = httpx.get(url, params=params, headers=_auth_headers(), timeout=timeout_s)
    r.raise_for_status()
    data: Any = r.json()
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object from API")
    return data


def get_json_nocache(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Like :func:`get_json` but never cached (for mutable state like toggles)."""
    url = api_base_url() + path
    timeout_s = get_settings().dashboard_http_timeout_seconds
    r = httpx.get(url, params=params, headers=_auth_headers(), timeout=timeout_s)
    r.raise_for_status()
    data: Any = r.json()
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object from API")
    return data


def put_json(
    path: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a PUT request to the API."""
    url = api_base_url() + path
    timeout_s = get_settings().dashboard_http_timeout_seconds
    r = httpx.put(
        url,
        params=params,
        json=json_body,
        headers=_auth_headers(),
        timeout=timeout_s,
    )
    r.raise_for_status()
    data: Any = r.json()
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object from API")
    return data


def post_json(
    path: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a POST request to the API."""
    url = api_base_url() + path
    timeout_s = get_settings().dashboard_http_timeout_seconds
    r = httpx.post(
        url,
        params=params,
        json=json_body,
        headers=_auth_headers(),
        timeout=timeout_s,
    )
    r.raise_for_status()
    data: Any = r.json()
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object from API")
    return data


def delete_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send a DELETE request to the API."""
    url = api_base_url() + path
    timeout_s = get_settings().dashboard_http_timeout_seconds
    r = httpx.delete(url, params=params, headers=_auth_headers(), timeout=timeout_s)
    r.raise_for_status()
    data: Any = r.json()
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object from API")
    return data
