"""Tests for YouTube comment collection."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from sam.collectors.youtube import YouTubeCollector


class TestYouTubeCommentCollection:
    """Test YouTube comment collection."""

    @pytest.mark.asyncio
    async def test_collect_comments_demo_mode(self) -> None:
        """In demo mode, collect_comments should return demo comments."""
        collector = YouTubeCollector(demo_mode=True)
        comments = await collector.collect_comments("dQw4w9WgXcQ", limit=5)

        assert isinstance(comments, list)
        assert len(comments) > 0
        for c in comments:
            assert c.source_type == "comment"
            assert c.content  # Should have non-empty content

    @pytest.mark.asyncio
    async def test_collect_comments_no_client(self) -> None:
        """When client is not configured, return empty list."""
        collector = YouTubeCollector(demo_mode=False)
        collector._client = None
        comments = await collector.collect_comments("video123", limit=10)
        assert comments == []

    @pytest.mark.asyncio
    async def test_collect_comments_parses_response(self) -> None:
        """collect_comments should parse API response into CollectedPost objects."""
        collector = YouTubeCollector(demo_mode=False)
        collector._client = AsyncMock()  # Non-None so it doesn't short-circuit

        mock_response = {
            "items": [
                {
                    "snippet": {
                        "topLevelComment": {
                            "id": "comment1",
                            "snippet": {
                                "textDisplay": "Great video!",
                                "authorDisplayName": "User1",
                                "likeCount": 5,
                                "publishedAt": "2026-01-15T10:00:00Z",
                            },
                        },
                        "totalReplyCount": 2,
                    },
                },
            ],
        }

        collector._get_json = AsyncMock(return_value=mock_response)

        comments = await collector.collect_comments("video123", limit=10)
        assert len(comments) == 1
        assert comments[0].source_type == "comment"
        assert comments[0].content == "Great video!"
        assert comments[0].author == "User1"

    @pytest.mark.asyncio
    async def test_collect_comments_respects_limit(self) -> None:
        """Should not return more than limit comments."""
        collector = YouTubeCollector(demo_mode=True)
        comments = await collector.collect_comments("video123", limit=3)
        assert len(comments) <= 3

    @pytest.mark.asyncio
    async def test_parse_comment_method(self) -> None:
        """_parse_comment should extract fields from API item."""
        collector = YouTubeCollector(demo_mode=True)
        item = {
            "snippet": {
                "topLevelComment": {
                    "id": "cmt_abc",
                    "snippet": {
                        "textDisplay": "Amazing content!",
                        "authorDisplayName": "TestUser",
                        "likeCount": 10,
                        "publishedAt": "2026-02-01T12:30:00Z",
                    },
                },
                "totalReplyCount": 3,
            },
        }
        result = collector._parse_comment(item, "parent_video_id")
        assert result is not None
        assert result.source_type == "comment"
        assert result.content == "Amazing content!"
        assert result.author == "TestUser"
        assert result.metrics["like_count"] == 10
        assert result.metrics["reply_count"] == 3
