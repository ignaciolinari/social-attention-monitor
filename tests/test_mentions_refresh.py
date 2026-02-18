from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks

import sam.api.main as api


def _fake_mention(platform: str = "reddit") -> api.MentionResponse:
    now = datetime.now(UTC).isoformat()
    return api.MentionResponse(
        platform=platform,
        source_id="abc123",
        content="sample",
        author="user",
        url="https://example.com",
        created_at=now,
        metrics={},
        sentiment={"compound": 0.1, "label": "positive"},
    )


@pytest.mark.asyncio
async def test_mentions_refreshes_when_stale(monkeypatch) -> None:
    async def fake_get_mentions_from_db(
        *,
        title: str,
        title_id,
        platform: str,
        limit: int,
        offset: int,
    ):
        _ = (title, title_id, platform, limit, offset)
        return api.DbMentionsResult(
            mentions=[_fake_mention(platform)],
            total_count=1,
            next_offset=None,
            title_id=uuid4(),
            last_collected_at=datetime.now(UTC) - timedelta(minutes=90),
        )

    monkeypatch.setattr(api, "_get_mentions_from_db", fake_get_mentions_from_db)
    background_tasks = BackgroundTasks()

    response = await api.get_reddit_mentions(
        background_tasks=background_tasks,
        title="Dune",
        limit=5,
        offset=0,
    )

    assert response.platform == "reddit"
    assert len(background_tasks.tasks) == 1


@pytest.mark.asyncio
async def test_mentions_skip_refresh_when_fresh(monkeypatch) -> None:
    async def fake_get_mentions_from_db(
        *,
        title: str,
        title_id,
        platform: str,
        limit: int,
        offset: int,
    ):
        _ = (title, title_id, platform, limit, offset)
        return api.DbMentionsResult(
            mentions=[_fake_mention(platform)],
            total_count=1,
            next_offset=None,
            title_id=uuid4(),
            last_collected_at=datetime.now(UTC) - timedelta(minutes=1),
        )

    monkeypatch.setattr(api, "_get_mentions_from_db", fake_get_mentions_from_db)
    background_tasks = BackgroundTasks()

    response = await api.get_reddit_mentions(
        background_tasks=background_tasks,
        title="Dune",
        limit=5,
        offset=0,
    )

    assert response.platform == "reddit"
    assert len(background_tasks.tasks) == 0


@pytest.mark.asyncio
async def test_bluesky_mentions_refreshes_when_stale(monkeypatch) -> None:
    async def fake_get_mentions_from_db(
        *,
        title: str,
        title_id,
        platform: str,
        limit: int,
        offset: int,
    ):
        _ = (title, title_id, platform, limit, offset)
        return api.DbMentionsResult(
            mentions=[_fake_mention(platform)],
            total_count=1,
            next_offset=None,
            title_id=uuid4(),
            last_collected_at=datetime.now(UTC) - timedelta(minutes=90),
        )

    monkeypatch.setattr(api, "_get_mentions_from_db", fake_get_mentions_from_db)
    background_tasks = BackgroundTasks()

    response = await api.get_bluesky_mentions(
        background_tasks=background_tasks,
        title="Dune",
        limit=5,
        offset=0,
    )

    assert response.platform == "bluesky"
    assert len(background_tasks.tasks) == 1
