from __future__ import annotations

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
