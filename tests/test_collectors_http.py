from __future__ import annotations

from unittest.mock import patch

import pytest
import respx

from sam.collectors.tmdb import TMDBCollector
from sam.collectors.youtube import YouTubeCollector


@respx.mock
@pytest.mark.asyncio
async def test_tmdb_get_trending(monkeypatch) -> None:
    monkeypatch.setenv("SAM_DEMO_MODE", "false")
    monkeypatch.setenv("TMDB_API_KEY", "test-key")

    route = respx.get("https://api.themoviedb.org/3/trending/all/week").respond(
        200,
        json={
            "results": [
                {
                    "id": 1,
                    "title": "Dune: Part Two",
                    "media_type": "movie",
                    "overview": "test",
                    "popularity": 100.0,
                    "vote_average": 8.0,
                },
                {
                    "id": 2,
                    "name": "The Last of Us",
                    "media_type": "tv",
                    "overview": "test",
                    "popularity": 90.0,
                    "vote_average": 8.5,
                },
            ]
        },
    )

    collector = TMDBCollector()
    titles = await collector.get_trending(media_type="all", time_window="week", limit=2)
    await collector.close()

    assert route.called
    assert len(titles) == 2
    assert titles[0].title == "Dune: Part Two"
    assert titles[1].title == "The Last of Us"


@respx.mock
@pytest.mark.asyncio
async def test_youtube_collect(monkeypatch) -> None:
    monkeypatch.setenv("SAM_DEMO_MODE", "false")
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    monkeypatch.setenv("REDIS_URL", "")

    respx.get("https://www.googleapis.com/youtube/v3/search").respond(
        200,
        json={
            "items": [
                {"id": {"videoId": "abc123"}},
            ]
        },
    )

    respx.get("https://www.googleapis.com/youtube/v3/videos").respond(
        200,
        json={
            "items": [
                {
                    "id": "abc123",
                    "snippet": {
                        "title": "Dune Trailer",
                        "description": "desc",
                        "publishedAt": "2026-01-30T00:00:00Z",
                        "channelId": "chan",
                        "channelTitle": "Channel",
                    },
                    "statistics": {
                        "viewCount": "10",
                        "likeCount": "2",
                        "commentCount": "1",
                    },
                }
            ]
        },
    )

    collector = YouTubeCollector()
    result = await collector.collect(query="Dune", limit=1)
    await collector.close()

    assert result.success is True
    assert len(result.posts) == 1
    assert result.posts[0].source_id == "abc123"


@respx.mock
@pytest.mark.asyncio
async def test_youtube_quota_not_recorded_on_http_error(monkeypatch) -> None:
    """A2: Quota recording must only happen after a successful response.

    When the YouTube API returns a 403 quotaExceeded error, no quota units
    should be debited to the tracker.
    """
    monkeypatch.setenv("SAM_DEMO_MODE", "false")
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    monkeypatch.setenv("REDIS_URL", "")

    respx.get("https://www.googleapis.com/youtube/v3/search").respond(
        403,
        json={
            "error": {
                "errors": [{"reason": "quotaExceeded", "domain": "usageLimits"}],
                "code": 403,
                "message": "The request cannot be completed because you have exceeded your quota.",
            }
        },
    )

    with patch("sam.quota.get_quota_tracker") as mock_get_qt:
        mock_tracker = mock_get_qt.return_value

        collector = YouTubeCollector()
        result = await collector.collect(query="Dune", limit=1)
        await collector.close()

    assert result.success is False
    # quota.record() should NOT have been called since the API returned 403.
    mock_tracker.record.assert_not_called()


@respx.mock
@pytest.mark.asyncio
async def test_youtube_quota_recorded_on_success(monkeypatch) -> None:
    """A2 positive path: quota IS recorded after successful responses."""
    monkeypatch.setenv("SAM_DEMO_MODE", "false")
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    monkeypatch.setenv("REDIS_URL", "")

    respx.get("https://www.googleapis.com/youtube/v3/search").respond(
        200,
        json={"items": [{"id": {"videoId": "v1"}}]},
    )
    respx.get("https://www.googleapis.com/youtube/v3/videos").respond(
        200,
        json={
            "items": [
                {
                    "id": "v1",
                    "snippet": {
                        "title": "Trailer",
                        "description": "",
                        "publishedAt": "2026-01-30T00:00:00Z",
                        "channelId": "c",
                        "channelTitle": "C",
                    },
                    "statistics": {"viewCount": "1", "likeCount": "0", "commentCount": "0"},
                }
            ]
        },
    )

    with patch("sam.quota.get_quota_tracker") as mock_get_qt:
        mock_tracker = mock_get_qt.return_value

        collector = YouTubeCollector()
        result = await collector.collect(query="Dune", limit=1)
        await collector.close()

    assert result.success is True
    # Two successful API calls: search.list + videos.list.
    assert mock_tracker.record.call_count == 2
    endpoints_recorded = [c.args[1] for c in mock_tracker.record.call_args_list]
    assert "search.list" in endpoints_recorded
    assert "videos.list" in endpoints_recorded
