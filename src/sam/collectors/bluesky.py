"""
Bluesky Collector

Collects posts from Bluesky using the AT Protocol SDK.
"""

import asyncio
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from sam.collectors.base import BaseCollector, CollectedPost, CollectionResult
from sam.config import get_settings


def _should_retry(exc: BaseException) -> bool:
    """Retry on rate limits and transient server errors."""
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    if status in {429, 500, 502, 503, 504}:
        return True
    return "429" in str(exc)


class BlueskyCollector(BaseCollector):
    """
    Collector for Bluesky data using the atproto SDK.

    Searches for posts mentioning tracked titles.
    """

    def __init__(self, demo_mode: bool | None = None) -> None:
        """
        Initialize Bluesky collector.

        Args:
            demo_mode: Override settings demo mode if specified
        """
        settings = get_settings()
        self._settings = settings.bluesky

        super().__init__(demo_mode=demo_mode if demo_mode is not None else settings.demo_mode)

    @property
    def platform_name(self) -> str:
        return "bluesky"

    @property
    def is_configured(self) -> bool:
        return self._settings.is_configured

    async def close(self) -> None:
        """Close the Bluesky client."""
        self._client = None

    def _setup_client(self) -> None:
        """Initialize atproto client and login.

        The atproto Client is synchronous and performs a blocking HTTP login.
        We defer this to the first collection call (via `_ensure_client`) to
        avoid blocking the async event loop during ``__init__``.
        """
        # Intentional no-op: real init happens lazily in _ensure_client.
        pass

    def _ensure_client(self) -> None:
        """Lazily initialize the atproto client on first use (sync context)."""
        if self._client is not None:
            return
        try:
            from atproto import Client

            self._client = Client()
            self._client.login(
                self._settings.identifier,
                self._settings.app_password,
            )
            logger.info("[bluesky] Client initialized and logged in successfully")
        except Exception as e:
            logger.warning(f"[bluesky] Failed to initialize client: {e}")
            self._client = None

    async def collect(
        self,
        query: str | None = None,
        limit: int = 100,
        **_kwargs: Any,
    ) -> CollectionResult:
        """
        Collect posts from Bluesky.

        Args:
            query: Search query (title name to look for)
            limit: Maximum posts to collect

        Returns:
            CollectionResult with collected posts
        """
        if self.demo_mode:
            posts = self._generate_demo_data(query, limit)
            result = CollectionResult(
                platform=self.platform_name,
                posts=posts,
                collected_at=datetime.now(UTC),
                success=True,
            )
            self._log_collection(result)
            return result

        if not self._client:
            # Attempt lazy initialization before giving up.
            if self.is_configured:
                await asyncio.to_thread(self._ensure_client)
            if not self._client:
                return CollectionResult(
                    platform=self.platform_name,
                    posts=[],
                    collected_at=datetime.now(UTC),
                    success=False,
                    error="Bluesky client not initialized",
                )

        if not query:
            return CollectionResult(
                platform=self.platform_name,
                posts=[],
                collected_at=datetime.now(UTC),
                success=True,
            )

        try:
            return await self._collect_with_retries(query, limit)
        except Exception as e:
            logger.error(f"[bluesky] Collection failed after retries: {e}")
            return CollectionResult(
                platform=self.platform_name,
                posts=[],
                collected_at=datetime.now(UTC),
                success=False,
                error=str(e),
            )

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        retry=retry_if_exception(_should_retry),
    )
    async def _collect_with_retries(self, query: str, limit: int) -> CollectionResult:
        """
        Run sync atproto collection with retry/backoff on errors.

        Uses tenacity for 429, 500, 502, 503, 504 with exponential backoff.
        The atproto Client is synchronous, so we offload to a thread.
        """
        try:
            posts = await asyncio.to_thread(self._search_posts, query, limit)
            result = CollectionResult(
                platform=self.platform_name,
                posts=posts,
                collected_at=datetime.now(UTC),
                success=True,
            )
            self._log_collection(result)
            return result
        except Exception as e:
            if _should_retry(e):
                logger.warning(f"[bluesky] Transient error (will retry): {e}")
                raise
            logger.error(f"[bluesky] Collection failed: {e}")
            return CollectionResult(
                platform=self.platform_name,
                posts=[],
                collected_at=datetime.now(UTC),
                success=False,
                error=str(e),
            )

    def _search_posts(self, query: str, limit: int) -> list[CollectedPost]:
        """Search for posts matching query.

        Raises on error so that ``_collect_with_retries`` can decide whether
        to retry (e.g. on rate-limit) or fail immediately.
        """
        collected_posts: list[CollectedPost] = []
        remaining = max(0, int(limit))
        cursor: str | None = None

        while remaining > 0:
            page_limit = min(remaining, 100)
            params: dict[str, Any] = {"q": query, "limit": page_limit}
            if cursor:
                params["cursor"] = cursor

            response = self._client.app.bsky.feed.search_posts(params=params)
            posts = getattr(response, "posts", None) or []

            for post_view in posts:
                post = self._parse_post(post_view)
                if post:
                    collected_posts.append(post)

            cursor = getattr(response, "cursor", None)
            remaining = limit - len(collected_posts)

            if not cursor or not posts:
                break

        logger.debug(f"[bluesky] Collected {len(collected_posts)} posts for query '{query}'")
        return collected_posts[:limit]

    def _parse_post(self, post_view: Any) -> CollectedPost | None:
        """Parse a Bluesky PostView into a CollectedPost."""
        try:
            # PostView has attributes directly (not nested under .post)
            record = post_view.record

            # Extract author info
            author = post_view.author
            author_handle = getattr(author, "handle", None)

            # Extract metrics
            like_count = getattr(post_view, "like_count", 0) or 0
            repost_count = getattr(post_view, "repost_count", 0) or 0
            reply_count = getattr(post_view, "reply_count", 0) or 0

            # Parse created_at timestamp from record
            created_at_raw = getattr(record, "created_at", None)
            if isinstance(created_at_raw, datetime):
                created_at = (
                    created_at_raw if created_at_raw.tzinfo else created_at_raw.replace(tzinfo=UTC)
                )
            elif isinstance(created_at_raw, str):
                # Handle ISO format with Z suffix
                if created_at_raw.endswith("Z"):
                    created_at_raw = created_at_raw[:-1] + "+00:00"
                created_at = datetime.fromisoformat(created_at_raw)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
            else:
                created_at = datetime.now(UTC)

            # Build post URL
            post_uri = post_view.uri
            # URI format: at://did:plc:xxx/app.bsky.feed.post/xxx
            # Convert to web URL: https://bsky.app/profile/handle/post/xxx
            rkey = post_uri.split("/")[-1] if post_uri else ""
            url = f"https://bsky.app/profile/{author_handle}/post/{rkey}" if author_handle else None

            return CollectedPost(
                platform=self.platform_name,
                source_id=str(post_uri or post_view.cid),
                source_type="post",
                content=getattr(record, "text", "") or "",
                author=author_handle,
                url=url,
                created_at=created_at,
                metrics={
                    "likes": like_count,
                    "reposts": repost_count,
                    "replies": reply_count,
                },
                raw_data={
                    "uri": post_uri,
                    "cid": post_view.cid,
                    "author_did": getattr(author, "did", None),
                },
            )

        except Exception as e:
            logger.warning(f"[bluesky] Error parsing post: {e}")
            return None

    def _generate_demo_data(self, _query: str | None, limit: int) -> list[CollectedPost]:
        """Generate demo Bluesky posts for testing."""
        demo_titles = [
            "The Last of Us Season 2",
            "Dune: Part Two",
            "Severance Season 2",
            "The White Lotus Season 3",
            "Wednesday Season 2",
        ]

        posts = []
        base_time = datetime.now(UTC)

        for i in range(min(limit, 20)):
            title = random.choice(demo_titles)

            sentiment_type = random.choice(["positive", "negative", "neutral"])
            if sentiment_type == "positive":
                content = f"Just finished {title} and I'm absolutely blown away! 🎬✨ The storytelling is next level."
            elif sentiment_type == "negative":
                content = f"Had high hopes for {title} but it really didn't live up to expectations. Disappointing."
            else:
                content = f"Watching {title} right now. Pretty interesting so far, curious to see where it goes."

            posts.append(
                CollectedPost(
                    platform=self.platform_name,
                    source_id=f"demo_bsky_{i}_{random.randint(1000, 9999)}",
                    source_type="post",
                    content=content,
                    author=f"demo_user_{random.randint(1, 100)}.bsky.social",
                    url=f"https://bsky.app/profile/demouser/post/demo{i}",
                    created_at=base_time - timedelta(hours=random.randint(1, 48)),
                    metrics={
                        "likes": random.randint(1, 500),
                        "reposts": random.randint(0, 50),
                        "replies": random.randint(0, 30),
                    },
                )
            )

        return posts
