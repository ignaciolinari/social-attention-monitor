import os

import pytest

from sam.config import Settings


def test_demo_mode_alias_sam_demo_mode() -> None:
    old = os.environ.get("SAM_DEMO_MODE")
    try:
        os.environ["SAM_DEMO_MODE"] = "false"
        s = Settings()
        assert s.demo_mode is False
    finally:
        if old is None:
            os.environ.pop("SAM_DEMO_MODE", None)
        else:
            os.environ["SAM_DEMO_MODE"] = old


def test_cors_allow_origins_list() -> None:
    old = os.environ.get("CORS_ALLOW_ORIGINS")
    try:
        os.environ["CORS_ALLOW_ORIGINS"] = "https://a.com, https://b.com"
        s = Settings()
        assert s.cors_allow_origins_list == ["https://a.com", "https://b.com"]
    finally:
        if old is None:
            os.environ.pop("CORS_ALLOW_ORIGINS", None)
        else:
            os.environ["CORS_ALLOW_ORIGINS"] = old


def test_log_json_alias() -> None:
    old = os.environ.get("SAM_LOG_JSON")
    try:
        os.environ["SAM_LOG_JSON"] = "true"
        s = Settings()
        assert s.log_json is True
    finally:
        if old is None:
            os.environ.pop("SAM_LOG_JSON", None)
        else:
            os.environ["SAM_LOG_JSON"] = old


def test_dashboard_http_timeout_alias() -> None:
    old = os.environ.get("SAM_DASHBOARD_HTTP_TIMEOUT")
    try:
        os.environ["SAM_DASHBOARD_HTTP_TIMEOUT"] = "12.5"
        s = Settings()
        assert s.dashboard_http_timeout_seconds == 12.5
    finally:
        if old is None:
            os.environ.pop("SAM_DASHBOARD_HTTP_TIMEOUT", None)
        else:
            os.environ["SAM_DASHBOARD_HTTP_TIMEOUT"] = old


def test_youtube_comments_per_video_valid() -> None:
    old = os.environ.get("SAM_YOUTUBE_COMMENTS_PER_VIDEO")
    try:
        os.environ["SAM_YOUTUBE_COMMENTS_PER_VIDEO"] = "50"
        s = Settings()
        assert s.youtube_comments_per_video == 50
    finally:
        if old is None:
            os.environ.pop("SAM_YOUTUBE_COMMENTS_PER_VIDEO", None)
        else:
            os.environ["SAM_YOUTUBE_COMMENTS_PER_VIDEO"] = old


def test_youtube_comments_per_video_invalid_low() -> None:
    old = os.environ.get("SAM_YOUTUBE_COMMENTS_PER_VIDEO")
    try:
        os.environ["SAM_YOUTUBE_COMMENTS_PER_VIDEO"] = "0"
        with pytest.raises(ValueError):
            Settings()
    finally:
        if old is None:
            os.environ.pop("SAM_YOUTUBE_COMMENTS_PER_VIDEO", None)
        else:
            os.environ["SAM_YOUTUBE_COMMENTS_PER_VIDEO"] = old


def test_youtube_comments_per_video_invalid_high() -> None:
    old = os.environ.get("SAM_YOUTUBE_COMMENTS_PER_VIDEO")
    try:
        os.environ["SAM_YOUTUBE_COMMENTS_PER_VIDEO"] = "101"
        with pytest.raises(ValueError):
            Settings()
    finally:
        if old is None:
            os.environ.pop("SAM_YOUTUBE_COMMENTS_PER_VIDEO", None)
        else:
            os.environ["SAM_YOUTUBE_COMMENTS_PER_VIDEO"] = old
