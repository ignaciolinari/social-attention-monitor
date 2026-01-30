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
        try:
            # PRAW is synchronous; run collection in a thread to preserve async API.
            result = await asyncio.to_thread(
                self._collect_sync,
                query,
                limit,
                target_subreddits,
                time_filter,
            )
        except Exception as e:
            result = CollectionResult(
                platform=self.platform_name,
                posts=[],
                collected_at=datetime.now(UTC),
                success=False,
                error=str(e),
            )

        self._log_collection(result)
        return result

    def _collect_sync(
        self,
        query: str | None,
        limit: int,
        target_subreddits: list[str],
        time_filter: str,
    ) -> CollectionResult:
        collected_posts: list[CollectedPost] = []
        for subreddit_name in target_subreddits:
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
                    if query and query.lower() not in submission.title.lower():
                        continue

                    post = self._parse_submission(submission, subreddit_name)
                    collected_posts.append(post)

                logger.debug(
                    f"[reddit] Collected from r/{subreddit_name}: "
                    f"{len([p for p in collected_posts if p.metrics.get('subreddit') == subreddit_name])} posts"
                )
            except prawcore.exceptions.TooManyRequests as e:
                logger.warning(f"[reddit] Rate limited by Reddit API: {e}")
                break
            except Exception as e:
                logger.warning(f"[reddit] Error collecting from r/{subreddit_name}: {e}")

        return CollectionResult(
            platform=self.platform_name,
            posts=collected_posts,
            collected_at=datetime.now(UTC),
            success=True,
            rate_limit_remaining=self._get_rate_limit(),
        )

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
