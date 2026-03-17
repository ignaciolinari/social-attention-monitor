from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks

import sam.api.dependencies as deps
import sam.api.main as api
from sam.api.routes.mentions import (
    get_bluesky_mentions,
    get_platform_mentions,
    get_reddit_mentions,
)


def _fake_mention(platform: str = "reddit") -> api.MentionResponse:
    now = datetime.now(UTC).isoformat()
    return api.MentionResponse(
        platform=platform,
        source_id="abc123",
        source_type="post",
        content="sample",
        author="user",
        url="https://example.com",
        created_at=now,
        metrics={},
        sentiment={"compound": 0.1, "label": "positive"},
    )


@pytest.mark.asyncio
async def test_mentions_refreshes_when_stale(monkeypatch) -> None:
    stale_at = datetime.now(UTC) - timedelta(minutes=90)

    async def fake_get_mentions_from_db(
        *,
        title: str,
        title_id,
        platform: str,
        limit: int,
        offset: int,
    ):
        _ = (title, title_id, platform, limit, offset)
        return deps.DbMentionsResult(
            mentions=[_fake_mention(platform)],
            total_count=1,
            next_offset=None,
            title_id=uuid4(),
            last_collected_at=stale_at,
        )

    monkeypatch.setattr(deps, "get_mentions_from_db", fake_get_mentions_from_db)
    background_tasks = BackgroundTasks()

    response = await get_reddit_mentions(
        background_tasks=background_tasks,
        title="Dune",
        limit=5,
        offset=0,
    )

    assert response.platform == "reddit"
    assert len(background_tasks.tasks) == 1
    assert response.collected_at == stale_at.isoformat()


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
        return deps.DbMentionsResult(
            mentions=[_fake_mention(platform)],
            total_count=1,
            next_offset=None,
            title_id=uuid4(),
            last_collected_at=datetime.now(UTC) - timedelta(minutes=1),
        )

    monkeypatch.setattr(deps, "get_mentions_from_db", fake_get_mentions_from_db)
    background_tasks = BackgroundTasks()

    response = await get_reddit_mentions(
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
        return deps.DbMentionsResult(
            mentions=[_fake_mention(platform)],
            total_count=1,
            next_offset=None,
            title_id=uuid4(),
            last_collected_at=datetime.now(UTC) - timedelta(minutes=90),
        )

    monkeypatch.setattr(deps, "get_mentions_from_db", fake_get_mentions_from_db)
    background_tasks = BackgroundTasks()

    response = await get_bluesky_mentions(
        background_tasks=background_tasks,
        title="Dune",
        limit=5,
        offset=0,
    )

    assert response.platform == "bluesky"
    assert len(background_tasks.tasks) == 1


@pytest.mark.asyncio
async def test_generic_mentions_keeps_requested_limit(monkeypatch) -> None:
    seen: dict[str, int] = {}

    async def fake_get_mentions_from_db(
        *,
        title: str,
        title_id,
        platform: str,
        limit: int,
        offset: int,
    ):
        _ = (title, title_id, offset)
        seen[platform] = limit
        return deps.DbMentionsResult(
            mentions=[_fake_mention(platform)],
            total_count=1,
            next_offset=None,
            title_id=uuid4(),
            last_collected_at=datetime.now(UTC),
        )

    monkeypatch.setattr(deps, "get_mentions_from_db", fake_get_mentions_from_db)
    background_tasks = BackgroundTasks()

    response = await get_platform_mentions(
        platform="youtube",
        background_tasks=background_tasks,
        title="Dune",
        limit=80,
        offset=0,
    )

    assert response.platform == "youtube"
    assert seen["youtube"] == 80
