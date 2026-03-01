"""Logging configuration helpers for SAM."""

import sys
from typing import Any

from loguru import logger

from sam.config import get_settings


def _format_extra(record: dict[str, Any]) -> str:
    """Build a suffix string from loguru extra context fields."""
    extra = record.get("extra", {})
    if not extra:
        return ""
    parts = " ".join(f"{k}={v}" for k, v in extra.items() if v is not None)
    return f" [{parts}]" if parts else ""


def setup_logging() -> None:
    """Configure loguru based on settings."""
    settings = get_settings()

    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        serialize=settings.log_json,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
            "{extra[_ctx]}"
        ),
    )

    # Patch all records so {extra[_ctx]} always exists (even when empty).
    logger.configure(patcher=lambda record: record["extra"].update(_ctx=_format_extra(record)))  # type: ignore[arg-type]
