"""
Base Collector

Abstract base class for all data collectors.
Provides common functionality for rate limiting, error handling, and data formatting.
"""

import abc
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential


@dataclass
class CollectedPost:
    """Standardized representation of a collected social media post."""

    platform: str
    source_id: str
    source_type: str  # post, comment, video
    content: str
    author: str | None
    url: str | None
    created_at: datetime
    metrics: dict[str, Any] = field(default_factory=dict)
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectionResult:
    """Result of a collection operation."""

    platform: str
    posts: list[CollectedPost]
    collected_at: datetime
    success: bool
    error: str | None = None
    rate_limit_remaining: int | None = None


@dataclass
class CommentCollectionResult:
    """Result of a comment collection operation.

    Unlike :class:`CollectionResult`, this carries an explicit ``had_error``
    flag so callers can distinguish "zero comments available" from "the
    request failed" — even when the collector swallows exceptions internally.
    """

    comments: list[CollectedPost]
    had_error: bool = False


class BaseCollector(abc.ABC):
    """
    Abstract base class for social media data collectors.

    All collectors must implement:
    - is_configured: Check if API credentials are available
    - collect: Fetch data from the platform
    - _setup_client: Initialize the API client
    """

    def __init__(self, demo_mode: bool = False) -> None:
        """
        Initialize the collector.

        Args:
            demo_mode: If True, return mock data instead of making API calls
        """
        self.demo_mode = demo_mode
        self._client: Any = None

        if not demo_mode and self.is_configured:
            self._setup_client()

    @property
    @abc.abstractmethod
    def platform_name(self) -> str:
        """Return the platform name (e.g., 'reddit', 'youtube')."""
        ...

    @property
    @abc.abstractmethod
    def is_configured(self) -> bool:
        """Check if API credentials are configured."""
        ...

    @abc.abstractmethod
    def _setup_client(self) -> None:
        """Initialize the API client."""
        ...

    @abc.abstractmethod
    async def collect(
        self,
        query: str | None = None,
        limit: int = 100,
        **kwargs: Any,
    ) -> CollectionResult:
        """
        Collect data from the platform.

        Args:
            query: Search query or title to look for
            limit: Maximum number of items to collect
            **kwargs: Platform-specific parameters

        Returns:
            CollectionResult with collected posts
        """
        ...

    @abc.abstractmethod
    def _generate_demo_data(self, query: str | None, limit: int) -> list[CollectedPost]:
        """Generate demo data for testing without API access."""
        ...

    async def close(self) -> None:
        """Close any underlying resources (default: no-op)."""
        return None

    def _log_collection(self, result: CollectionResult) -> None:
        """Log collection results."""
        if result.success:
            logger.info(
                f"[{self.platform_name}] Collected {len(result.posts)} posts"
                + (
                    f" (rate limit: {result.rate_limit_remaining})"
                    if result.rate_limit_remaining
                    else ""
                )
            )
        else:
            logger.error(f"[{self.platform_name}] Collection failed: {result.error}")

    @staticmethod
    def retry_on_rate_limit(func: Callable[..., Any]) -> Callable[..., Any]:
        """Decorator for retrying on rate limit errors."""
        return retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=4, max=60),
            reraise=True,
        )(func)
