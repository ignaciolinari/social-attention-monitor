"""
TMDB Collector

Fetches movie and TV show metadata from The Movie Database (TMDB).
Used as the authoritative source for title information.
"""

import contextlib
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
from loguru import logger
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from sam.config import get_settings


def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return isinstance(exc, httpx.TimeoutException)


@dataclass
class TMDBTitle:
    """Standardized title metadata from TMDB."""

    tmdb_id: int
    title: str
    original_title: str
    media_type: str  # movie, tv
    release_date: datetime | None
    overview: str
    poster_path: str | None
    backdrop_path: str | None
    popularity: float
    vote_average: float
    vote_count: int
    genres: list[dict[str, Any]]
    original_language: str
    raw_data: dict[str, Any]


class TMDBCollector:
    """
    Collector for TMDB metadata.

    Provides authoritative information about movies and TV shows
    for matching social media mentions.
    """

    TMDB_API_BASE = "https://api.themoviedb.org/3"
    IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

    def __init__(self, demo_mode: bool | None = None) -> None:
        """
        Initialize TMDB collector.

        Args:
            demo_mode: Override settings demo mode if specified
        """
        settings = get_settings()
        self._settings = settings.tmdb
        self.demo_mode = demo_mode if demo_mode is not None else settings.demo_mode
        self._client: httpx.AsyncClient | None = None

        if not self.demo_mode and self.is_configured:
            self._setup_client()

    @property
    def is_configured(self) -> bool:
        """Check if TMDB API is configured."""
        return self._settings.is_configured

    def _setup_client(self) -> None:
        """Initialize HTTP client for TMDB API."""
        headers = {"Content-Type": "application/json"}
        params: dict[str, Any] = {}

        if self._settings.access_token:
            headers["Authorization"] = f"Bearer {self._settings.access_token}"
        elif self._settings.api_key:
            params["api_key"] = self._settings.api_key

        self._client = httpx.AsyncClient(
            base_url=self.TMDB_API_BASE,
            headers=headers,
            params=params,
            timeout=30.0,
        )
        logger.info("[tmdb] HTTP client initialized")

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception(_should_retry),
    )
    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._client:
            raise RuntimeError("TMDB client not initialized")

        response = await self._client.get(path, params=params)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    async def get_trending(
        self,
        media_type: str = "all",
        time_window: str = "week",
        limit: int = 20,
    ) -> list[TMDBTitle]:
        """
        Get trending movies and TV shows.

        Args:
            media_type: all, movie, or tv
            time_window: day or week
            limit: Maximum number of results

        Returns:
            List of trending titles
        """
        if self.demo_mode:
            return self._generate_demo_titles(limit)

        if not self._client:
            logger.warning("[tmdb] Client not initialized")
            return []

        try:
            data = await self._get_json(
                f"/trending/{media_type}/{time_window}",
            )

            titles = []
            for item in data.get("results", [])[: limit * 2]:
                if item.get("media_type") == "person":
                    continue
                titles.append(self._parse_title(item))
                if len(titles) >= limit:
                    break

            logger.info(f"[tmdb] Fetched {len(titles)} trending titles")
            return titles

        except Exception as e:
            # tenacity wraps HTTPStatusError retries; this final exception is the one that
            # bubbled after retries. Keep logs actionable.
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                retry_after = e.response.headers.get("Retry-After")
                extra = f" retry_after={retry_after}s" if retry_after else ""
                logger.error(f"[tmdb] Rate limited by TMDB API (HTTP 429).{extra}")
            else:
                logger.error(f"[tmdb] Error fetching trending: {e}")
            return []

    async def search(
        self,
        query: str,
        media_type: str = "multi",
        year: int | None = None,
        limit: int = 10,
    ) -> list[TMDBTitle]:
        """
        Search for movies and TV shows.

        Args:
            query: Search query
            media_type: multi, movie, or tv
            year: Filter by release year
            limit: Maximum number of results

        Returns:
            List of matching titles
        """
        if self.demo_mode:
            return self._generate_demo_titles(min(limit, 5))

        if not self._client:
            logger.warning("[tmdb] Client not initialized")
            return []

        try:
            endpoint = f"/search/{media_type}"
            params: dict[str, Any] = {"query": query}

            if year:
                params["year"] = year

            data = await self._get_json(endpoint, params=params)

            titles = []
            for item in data.get("results", [])[: limit * 2]:
                if item.get("media_type") == "person":
                    continue
                titles.append(self._parse_title(item))
                if len(titles) >= limit:
                    break

            logger.info(f"[tmdb] Search '{query}' returned {len(titles)} results")
            return titles

        except Exception as e:
            logger.error(f"[tmdb] Error searching: {e}")
            return []

    async def get_details(
        self,
        tmdb_id: int,
        media_type: str,
    ) -> TMDBTitle | None:
        """
        Get detailed information about a specific title.

        Args:
            tmdb_id: TMDB ID
            media_type: movie or tv

        Returns:
            Title details or None if not found
        """
        if self.demo_mode:
            titles = self._generate_demo_titles(1)
            return titles[0] if titles else None

        if not self._client:
            logger.warning("[tmdb] Client not initialized")
            return None

        try:
            data = await self._get_json(
                f"/{media_type}/{tmdb_id}",
            )

            return self._parse_title(data, media_type=media_type)

        except Exception as e:
            logger.error(f"[tmdb] Error fetching details for {media_type}/{tmdb_id}: {e}")
            return None

    async def get_upcoming(
        self,
        media_type: str = "movie",
        limit: int = 20,
    ) -> list[TMDBTitle]:
        """
        Get upcoming releases.

        Args:
            media_type: movie or tv
            limit: Maximum number of results

        Returns:
            List of upcoming titles
        """
        if self.demo_mode:
            return self._generate_demo_titles(limit)

        if not self._client:
            logger.warning("[tmdb] Client not initialized")
            return []

        try:
            endpoint = "/movie/upcoming" if media_type == "movie" else "/tv/on_the_air"

            data = await self._get_json(endpoint)

            titles = [
                self._parse_title(item, media_type=media_type)
                for item in data.get("results", [])[:limit]
            ]

            logger.info(f"[tmdb] Fetched {len(titles)} upcoming {media_type}s")
            return titles

        except Exception as e:
            logger.error(f"[tmdb] Error fetching upcoming: {e}")
            return []

    def _parse_title(
        self,
        data: dict[str, Any],
        media_type: str | None = None,
    ) -> TMDBTitle:
        """Parse TMDB response into TMDBTitle."""
        # Determine media type
        mtype = media_type or data.get("media_type", "movie")

        # Get title (movies use 'title', TV uses 'name')
        title = data.get("title") or data.get("name", "Unknown")
        original_title = data.get("original_title") or data.get("original_name", title)

        # Get release date
        date_str = data.get("release_date") or data.get("first_air_date")
        release_date = None
        if date_str:
            with contextlib.suppress(ValueError):
                release_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)

        return TMDBTitle(
            tmdb_id=data.get("id", 0),
            title=title,
            original_title=original_title,
            media_type=mtype,
            release_date=release_date,
            overview=data.get("overview", ""),
            poster_path=data.get("poster_path"),
            backdrop_path=data.get("backdrop_path"),
            popularity=data.get("popularity", 0.0),
            vote_average=data.get("vote_average", 0.0),
            vote_count=data.get("vote_count", 0),
            genres=data.get("genres", [])
            or [{"id": g, "name": str(g)} for g in data.get("genre_ids", [])],
            original_language=data.get("original_language", "en"),
            raw_data=data,
        )

    def _generate_demo_titles(self, limit: int) -> list[TMDBTitle]:
        """Generate demo TMDB titles for testing."""
        demo_data = [
            {
                "id": 1396,
                "title": "The Last of Us",
                "media_type": "tv",
                "overview": "Twenty years after modern civilization has been destroyed, Joel, a hardened survivor, is hired to smuggle Ellie out of an oppressive quarantine zone.",
                "popularity": 1200.5,
                "vote_average": 8.8,
            },
            {
                "id": 438631,
                "title": "Dune: Part Two",
                "media_type": "movie",
                "overview": "Follow the mythic journey of Paul Atreides as he unites with Chani and the Fremen.",
                "popularity": 980.2,
                "vote_average": 8.5,
            },
            {
                "id": 95396,
                "title": "Severance",
                "media_type": "tv",
                "overview": "Mark leads a team of office workers whose memories have been surgically divided between their work and personal lives.",
                "popularity": 750.8,
                "vote_average": 8.7,
            },
            {
                "id": 111803,
                "title": "The White Lotus",
                "media_type": "tv",
                "overview": "The exploits of various guests and employees of a tropical resort.",
                "popularity": 650.3,
                "vote_average": 8.0,
            },
            {
                "id": 119051,
                "title": "Wednesday",
                "media_type": "tv",
                "overview": "Wednesday Addams is sent to Nevermore Academy, a strange boarding school.",
                "popularity": 890.1,
                "vote_average": 8.2,
            },
        ]

        titles = []
        for i, data in enumerate(demo_data[:limit]):
            release_date = datetime.now(UTC) - timedelta(days=random.randint(1, 180))

            titles.append(
                TMDBTitle(
                    tmdb_id=cast(int, data["id"]),
                    title=cast(str, data["title"]),
                    original_title=cast(str, data["title"]),
                    media_type=cast(str, data["media_type"]),
                    release_date=release_date,
                    overview=cast(str, data["overview"]),
                    poster_path=f"/demo_poster_{i}.jpg",
                    backdrop_path=f"/demo_backdrop_{i}.jpg",
                    popularity=cast(float, data["popularity"]),
                    vote_average=cast(float, data["vote_average"]),
                    vote_count=random.randint(1000, 10000),
                    genres=[
                        {"id": 18, "name": "Drama"},
                        {"id": 10765, "name": "Sci-Fi & Fantasy"},
                    ],
                    original_language="en",
                    raw_data=data,
                )
            )

        return titles

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
