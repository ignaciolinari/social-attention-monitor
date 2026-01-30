"""Logging configuration helpers for SAM."""

import sys

from loguru import logger

from sam.config import get_settings


def setup_logging() -> None:
    """Configure loguru based on settings."""
    settings = get_settings()

    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
