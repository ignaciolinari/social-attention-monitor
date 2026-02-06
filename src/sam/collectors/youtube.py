"""
YouTube Collector

Collects video data from YouTube using the Data API v3.
"""

import contextlib
import random
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
from loguru import logger
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from sam.collectors.base import BaseCollector, CollectedPost, CollectionResult
from sam.config import get_settings
from sam.quota import YOUTUBE_SEARCH_COST, YOUTUBE_VIDEOS_COST, get_quota_tracker


def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return isinstance(exc, httpx.TimeoutException)


def _truncate_description(text: str, max_chars: int = 300) -> str:
    """Truncate a YouTube description at a sentence or word boundary.

    Prefers cutting at the last sentence-ending punctuation (.!?) within
    *max_chars*.  Falls back to the last space so we never split mid-word.
    """
    if len(text) <= max_chars:
        return text

    # Look for the last sentence boundary within the limit.
    candidate = text[:max_chars]
    for sep in (".\n", ". ", "! ", "? ", ".\t"):
        idx = candidate.rfind(sep)
        if idx > 0:
            return candidate[: idx + 1].rstrip()

    # No sentence boundary — fall back to last whitespace.
    space_idx = candidate.rfind(" ")
    if space_idx > 0:
        return candidate[:space_idx].rstrip()

    # Degenerate case: one giant token.
    return candidate


def _youtube_quota_reason(payload: dict[str, Any] | None) -> str | None:
    """Return a YouTube quota/rate-limit reason string if present."""
    if not payload:
        return None
    err = payload.get("error") or {}
    for item in err.get("errors", []) or []:
        reason = item.get("reason")
        if reason in {"quotaExceeded", "dailyLimitExceeded", "userRateLimitExceeded"}:
            return str(reason)
    return None


class YouTubeCollector(BaseCollector):
    """
    Collector for YouTube data using the Data API v3.

    Searches for trailers, reviews, and discussions about tracked titles.
    """

    YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

    def __init__(self, demo_mode: bool | None = None) -> None:
        """
        Initialize YouTube collector.

        Args:
            demo_mode: Override settings demo mode if specified
        """
        settings = get_settings()
        self._settings = settings.youtube

        super().__init__(demo_mode=demo_mode if demo_mode is not None else settings.demo_mode)

    @property
    def platform_name(self) -> str:
        return "youtube"

    @property
    def is_configured(self) -> bool:
        return self._settings.is_configured

    def _setup_client(self) -> None:
        """Initialize HTTP client for YouTube API."""
        self._client = httpx.AsyncClient(
            base_url=self.YOUTUBE_API_BASE,
            params={"key": self._settings.api_key},
            timeout=30.0,
        )
        logger.info("[youtube] HTTP client initialized")

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception(_should_retry),
    )
    async def _get_json(
        self,
        path: str,
        params: dict[str, Any],
        *,
        quota_endpoint: str | None = None,
        quota_units: int = 0,
    ) -> dict[str, Any]:
        if not self._client:
            raise RuntimeError("YouTube client not initialized")

        response = await self._client.get(path, params=params)
        # Record quota on response receipt (even if it's an error like 403 quotaExceeded).
        # If we never got a response (timeouts, network errors), we don't record.
        if quota_endpoint and quota_units:
            quota = get_quota_tracker()
            quota.record("youtube", quota_endpoint, quota_units)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    async def collect(
        self,
        query: str | None = None,
        limit: int = 50,
        video_type: str | None = None,
        exclude_source_ids: set[str] | None = None,
        **_kwargs: Any,
    ) -> CollectionResult:
        """
        Collect videos from YouTube.

        Args:
            query: Search query (title name + keywords)
            limit: Maximum videos to collect
            video_type: Filter by type (trailer, review, reaction, etc.)

        Returns:
            CollectionResult with collected posts
        """
        if self.demo_mode:
            demo_posts = self._generate_demo_data(query, limit)
            result = CollectionResult(
                platform=self.platform_name,
                posts=demo_posts,
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
                error="YouTube client not initialized",
            )

        try:
            # Build search query
            search_query = query or ""
            if video_type:
                search_query = f"{search_query} {video_type}"

            posts: list[CollectedPost] = []
            next_page: str | None = None
            published_after = (
                (datetime.now(UTC) - timedelta(days=self._settings.published_after_days))
                .isoformat()
                .replace("+00:00", "Z")
            )

            while len(posts) < limit:
                search_params = {
                    "part": "snippet",
                    "q": search_query,
                    "type": "video",
                    "maxResults": min(50, limit - len(posts)),
                    "order": self._settings.search_order,
                    "publishedAfter": published_after,
                }
                if next_page:
                    search_params["pageToken"] = next_page

                search_data = await self._get_json(
                    "/search",
                    params=search_params,
                    quota_endpoint="search.list",
                    quota_units=YOUTUBE_SEARCH_COST,
                )

                video_ids = [item["id"]["videoId"] for item in search_data.get("items", [])]
                if not video_ids:
                    break

                if exclude_source_ids:
                    video_ids = [vid for vid in video_ids if vid not in exclude_source_ids]
                    # If this page only returned already-seen videos, try the next page.
                    if not video_ids:
                        next_page = search_data.get("nextPageToken")
                        if not next_page:
                            break
                        continue

                stats_data = await self._get_json(
                    "/videos",
                    params={
                        "part": "statistics,snippet",
                        "id": ",".join(video_ids),
                    },
                    quota_endpoint="videos.list",
                    quota_units=YOUTUBE_VIDEOS_COST,
                )

                posts.extend(self._parse_video(video) for video in stats_data.get("items", []))
                # Defensive cap: APIs can occasionally return more items than requested
                # (or we may overshoot within a single batch). Ensure we never exceed `limit`.
                if len(posts) >= limit:
                    posts = posts[:limit]
                    break

                next_page = search_data.get("nextPageToken")
                if not next_page:
                    break

            result = CollectionResult(
                platform=self.platform_name,
                posts=posts,
                collected_at=datetime.now(UTC),
                success=True,
            )

        except httpx.HTTPStatusError as e:
            retry_after = e.response.headers.get("Retry-After")
            payload: dict[str, Any] | None = None
            with contextlib.suppress(Exception):
                payload = cast(dict[str, Any], e.response.json())

            quota_reason = _youtube_quota_reason(payload)
            if quota_reason is not None:
                msg = f"quota/rate limit exceeded ({quota_reason})"
                if retry_after:
                    msg = f"{msg}; retry_after={retry_after}s"
                result = CollectionResult(
                    platform=self.platform_name,
                    posts=[],
                    collected_at=datetime.now(UTC),
                    success=False,
                    error=msg,
                )
            elif e.response.status_code == 429:
                msg = "rate limited (HTTP 429)"
                if retry_after:
                    msg = f"{msg}; retry_after={retry_after}s"
                result = CollectionResult(
                    platform=self.platform_name,
                    posts=[],
                    collected_at=datetime.now(UTC),
                    success=False,
                    error=msg,
                )
            else:
                result = CollectionResult(
                    platform=self.platform_name,
                    posts=[],
                    collected_at=datetime.now(UTC),
                    success=False,
                    error=f"HTTP error: {e.response.status_code}",
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

    def _parse_video(self, video: dict[str, Any]) -> CollectedPost:
        """Parse a YouTube video response into a CollectedPost."""
        snippet = video.get("snippet", {})
        statistics = video.get("statistics", {})

        published_at = snippet.get("publishedAt")
        if published_at:
            created_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        else:
            created_at = datetime.now(UTC)

        # YouTube descriptions are often SEO spam, timestamps, and boilerplate
        # channel info.  Truncate to ~300 chars at the nearest sentence or word
        # boundary so sentiment analysis focuses on meaningful introductory text.
        description = _truncate_description(snippet.get("description") or "", max_chars=300)
        title_text = snippet.get("title", "")

        return CollectedPost(
            platform=self.platform_name,
            source_id=video["id"],
            source_type="video",
            content=f"{title_text}\n\n{description}",
            author=snippet.get("channelTitle"),
            url=f"https://youtube.com/watch?v={video['id']}",
            created_at=created_at,
            metrics={
                "view_count": int(statistics.get("viewCount", 0)),
                "like_count": int(statistics.get("likeCount", 0)),
                "comment_count": int(statistics.get("commentCount", 0)),
                "channel_id": snippet.get("channelId"),
                "channel_title": snippet.get("channelTitle"),
            },
            raw_data=video,
        )

    def _generate_demo_data(self, query: str | None, limit: int) -> list[CollectedPost]:
        """Generate demo YouTube videos for testing."""
        demo_titles = [
            "The Last of Us Season 2",
            "Dune: Part Two",
            "Severance Season 2",
            "The White Lotus Season 3",
        ]

        video_types = [
            ("Official Trailer", "channel_official"),
            ("Review - Is It Worth Watching?", "review_channel"),
            ("Ending Explained", "analysis_channel"),
            ("Reaction", "reaction_channel"),
            ("Scene Breakdown", "breakdown_channel"),
        ]

        posts = []
        base_time = datetime.now(UTC)

        for i in range(min(limit, 15)):
            title = query or random.choice(demo_titles)
            video_type, channel = random.choice(video_types)

            view_count = random.randint(10000, 5000000)

            posts.append(
                CollectedPost(
                    platform=self.platform_name,
                    source_id=f"demo_yt_{i}_{random.randint(1000, 9999)}",
                    source_type="video",
                    content=f"{title} | {video_type}\n\nIn this video we discuss {title} and share our thoughts...",
                    author=f"Demo_{channel}_{random.randint(1, 10)}",
                    url=f"https://youtube.com/watch?v=demo{i}",
                    created_at=base_time - timedelta(hours=random.randint(1, 168)),
                    metrics={
                        "view_count": view_count,
                        "like_count": int(view_count * random.uniform(0.02, 0.08)),
                        "comment_count": int(view_count * random.uniform(0.001, 0.01)),
                        "channel_id": f"UC{random.randint(10000, 99999)}",
                        "channel_title": f"Demo_{channel}_{random.randint(1, 10)}",
                    },
                )
            )

        return posts

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
