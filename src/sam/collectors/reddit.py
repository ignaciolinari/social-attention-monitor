"""
Reddit Collector

Collects posts and comments from Reddit subreddits using PRAW.
"""

import asyncio
import contextlib
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import praw
import prawcore
from loguru import logger

from sam.collectors.base import BaseCollector, CollectedPost, CollectionResult
from sam.config import get_settings


class RedditCollector(BaseCollector):
    """
    Collector for Reddit data using PRAW.

    Monitors specified subreddits for posts mentioning tracked titles.
    """

    def __init__(self, demo_mode: bool | None = None) -> None:
        """
        Initialize Reddit collector.

        Args:
            demo_mode: Override settings demo mode if specified
        """
        settings = get_settings()
        self._settings = settings.reddit
        self._collector_settings = settings.collector

        super().__init__(demo_mode=demo_mode if demo_mode is not None else settings.demo_mode)

    @property
    def platform_name(self) -> str:
        return "reddit"

    @property
    def is_configured(self) -> bool:
        return self._settings.is_configured

    async def close(self) -> None:
        """Close the Reddit client (PRAW is sync, so this is a no-op for cleanup symmetry)."""
        # PRAW doesn't require explicit cleanup, but we provide this for interface consistency
        self._client = None

    def _setup_client(self) -> None:
        """Initialize PRAW client."""
        try:
            self._client = praw.Reddit(
                client_id=self._settings.client_id,
                client_secret=self._settings.client_secret,
                user_agent=self._settings.user_agent,
            )
            # Important: do NOT call `user.me()` here.
            # `user.me()` requires a user-authorized OAuth flow and will fail for typical
            # read-only "installed/script" style apps that only use client credentials.
            # We'll rely on request-time error handling inside collection calls.
            with contextlib.suppress(Exception):
                # Explicitly force read-only mode (safe even if already read-only).
                self._client.read_only = True
            logger.info("[reddit] PRAW client initialized successfully")
        except Exception as e:
            logger.warning(f"[reddit] Failed to initialize PRAW client: {e}")
            self._client = None

    async def collect(
        self,
        query: str | None = None,
        limit: int = 100,
        subreddits: list[str] | None = None,
        time_filter: str = "day",
        **_kwargs: Any,
    ) -> CollectionResult:
        """
        Collect posts from Reddit.

        Args:
            query: Search query (title name to look for)
            limit: Maximum posts per subreddit
            subreddits: List of subreddits to search (uses default if None)
            time_filter: Time filter for hot/top posts (hour, day, week, month, year, all)

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
            return CollectionResult(
                platform=self.platform_name,
                posts=[],
                collected_at=datetime.now(UTC),
                success=False,
                error="Reddit client not initialized",
            )

        target_subreddits = subreddits or self._collector_settings.subreddit_list
        result = await self._collect_with_retries(
            query=query,
            limit=limit,
            target_subreddits=target_subreddits,
            time_filter=time_filter,
        )

        self._log_collection(result)
        return result

    async def _collect_with_retries(
        self,
        *,
        query: str | None,
        limit: int,
        target_subreddits: list[str],
        time_filter: str,
        max_attempts: int = 3,
    ) -> CollectionResult:
        """
        Run sync PRAW collection with explicit retry/backoff on 429s.

        Note: PRAW clients are not thread-safe, so we collect subreddits sequentially
        (still offloading each sync call to a thread).
        """
        attempt = 0
        while True:
            attempt += 1
            try:
                all_posts: list[CollectedPost] = []
                for subreddit in target_subreddits:
                    posts = await asyncio.to_thread(
                        self._collect_subreddit_sync,
                        subreddit,
                        query,
                        limit,
                        time_filter,
                    )
                    all_posts.extend(posts)

                return CollectionResult(
                    platform=self.platform_name,
                    posts=all_posts,
                    collected_at=datetime.now(UTC),
                    success=True,
                    rate_limit_remaining=self._get_rate_limit(),
                )

            except (RedditRateLimitError, prawcore.exceptions.TooManyRequests) as e:
                # Unwrap if it's a PRAW error that got through
                error_obj = e
                if isinstance(e, prawcore.exceptions.TooManyRequests):
                    error_obj = RedditRateLimitError.from_praw(e)

                if attempt >= max_attempts:
                    return CollectionResult(
                        platform=self.platform_name,
                        posts=[],
                        collected_at=datetime.now(UTC),
                        success=False,
                        error=f"rate limited by Reddit API; retry_after={error_obj.retry_after_seconds}s",
                    )

                # Respect Retry-After when available; otherwise exponential backoff with jitter.
                if error_obj.retry_after_seconds is not None:
                    sleep_s = max(1.0, float(error_obj.retry_after_seconds))
                else:
                    sleep_s = min(60.0, (2 ** (attempt - 1)) * 2.0) + random.uniform(0.0, 1.0)

                logger.warning(
                    f"[reddit] Rate limited (attempt {attempt}/{max_attempts}); sleeping {sleep_s:.1f}s"
                )
                await asyncio.sleep(sleep_s)

            except Exception as e:
                # Capture other errors (e.g. auth failure)
                logger.error(f"[reddit] Collection failed: {e}")
                return CollectionResult(
                    platform=self.platform_name,
                    posts=[],
                    collected_at=datetime.now(UTC),
                    success=False,
                    error=str(e),
                )

    def _collect_subreddit_sync(
        self,
        subreddit_name: str,
        query: str | None,
        limit: int,
        time_filter: str,
    ) -> list[CollectedPost]:
        """Collect from a single subreddit (sync)."""
        collected_posts: list[CollectedPost] = []
        try:
            subreddit = self._client.subreddit(subreddit_name)

            if query:
                submissions = subreddit.search(
                    query=query,
                    sort="new",
                    time_filter=time_filter,
                    limit=limit,
                )
            else:
                submissions = subreddit.hot(limit=limit)

            for submission in submissions:
                # Reddit search can return fuzzy-ish results. We keep a lightweight guard to
                # filter obvious false positives, but ensure we don't drop legitimate matches
                # where the query appears in the selftext/body instead of the title.
                if query:
                    q = query.lower()
                    title = str(getattr(submission, "title", "") or "").lower()
                    body = str(getattr(submission, "selftext", "") or "").lower()
                    if q not in title and q not in body:
                        continue

                post = self._parse_submission(submission, subreddit_name)
                collected_posts.append(post)

            logger.debug(f"[reddit] Collected {len(collected_posts)} posts from r/{subreddit_name}")
            return collected_posts

        except prawcore.exceptions.TooManyRequests as e:
            # Re-raise to be caught by the main retry loop
            raise RedditRateLimitError.from_praw(e) from e
        except Exception as e:
            # Log individual subreddit failures but don't fail the whole batch
            logger.warning(f"[reddit] Error collecting from r/{subreddit_name}: {e}")
            return []

    def _parse_submission(self, submission: Any, subreddit: str) -> CollectedPost:
        """Parse a PRAW submission into a CollectedPost."""
        return CollectedPost(
            platform=self.platform_name,
            source_id=submission.id,
            source_type="post",
            content=f"{submission.title}\n\n{submission.selftext}"
            if submission.selftext
            else submission.title,
            author=str(submission.author) if submission.author else None,
            url=f"https://reddit.com{submission.permalink}",
            created_at=datetime.fromtimestamp(submission.created_utc, tz=UTC),
            metrics={
                "score": submission.score,
                "upvote_ratio": submission.upvote_ratio,
                "num_comments": submission.num_comments,
                "subreddit": subreddit,
                "is_self": submission.is_self,
                "link_flair_text": submission.link_flair_text,
            },
            raw_data={
                "id": submission.id,
                "name": submission.name,
            },
        )

    def _get_rate_limit(self) -> int | None:
        """Get remaining rate limit from Reddit API."""
        try:
            if self._client and hasattr(self._client, "auth"):
                return getattr(self._client.auth, "limits", {}).get("remaining")
        except Exception:
            pass
        return None

    def _generate_demo_data(self, _query: str | None, limit: int) -> list[CollectedPost]:
        """Generate demo Reddit posts for testing."""
        demo_titles = [
            "The Last of Us Season 2",
            "Dune: Part Two",
            "Severance Season 2",
            "The White Lotus Season 3",
            "Wednesday Season 2",
        ]

        demo_subreddits = ["movies", "television", "netflix", "streaming"]

        posts = []
        base_time = datetime.now(UTC)

        for i in range(min(limit, 20)):
            title = random.choice(demo_titles)
            subreddit = random.choice(demo_subreddits)

            sentiment_type = random.choice(["positive", "negative", "neutral"])
            if sentiment_type == "positive":
                content = f"Just watched {title} and it was absolutely incredible! The writing, acting, everything was perfect."
            elif sentiment_type == "negative":
                content = f"I really tried to like {title} but it was such a disappointment. Not worth the hype at all."
            else:
                content = f"Finished {title} last night. It was okay, had some good moments but nothing special."

            posts.append(
                CollectedPost(
                    platform=self.platform_name,
                    source_id=f"demo_{i}_{random.randint(1000, 9999)}",
                    source_type="post",
                    content=content,
                    author=f"demo_user_{random.randint(1, 100)}",
                    url=f"https://reddit.com/r/{subreddit}/comments/demo{i}",
                    created_at=base_time - timedelta(hours=random.randint(1, 48)),
                    metrics={
                        "score": random.randint(10, 5000),
                        "upvote_ratio": round(random.uniform(0.6, 0.98), 2),
                        "num_comments": random.randint(5, 500),
                        "subreddit": subreddit,
                        "is_self": True,
                    },
                )
            )

        return posts


class RedditRateLimitError(RuntimeError):
    def __init__(self, message: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds

    @classmethod
    def from_praw(cls, exc: prawcore.exceptions.TooManyRequests) -> "RedditRateLimitError":
        retry_after: int | None = None
        # prawcore exposes response similar to requests.Response
        with contextlib.suppress(Exception):
            ra = getattr(getattr(exc, "response", None), "headers", {}).get("retry-after")
            if ra is not None:
                retry_after = int(float(str(ra)))
        msg = "rate limited by Reddit API (HTTP 429)"
        if retry_after is not None:
            msg = f"{msg}; retry_after={retry_after}s"
        return cls(msg, retry_after_seconds=retry_after)
