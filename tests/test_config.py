import os

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
