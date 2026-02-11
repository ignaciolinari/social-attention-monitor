from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

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


def test_mentions_refreshes_when_stale(monkeypatch) -> None:
    called: list[dict[str, object]] = []

    async def fake_get_mentions_from_db(*, title: str, platform: str, limit: int, offset: int):
        _ = (title, platform, limit, offset)
        return api.DbMentionsResult(
            mentions=[_fake_mention(platform)],
            total_count=1,
            next_offset=None,
            title_id=uuid4(),
            last_collected_at=datetime.now(UTC) - timedelta(minutes=90),
        )

    async def fake_refresh_mentions_background(**kwargs):
        called.append(kwargs)

    monkeypatch.setattr(api, "_get_mentions_from_db", fake_get_mentions_from_db)
    monkeypatch.setattr(api, "_refresh_mentions_background", fake_refresh_mentions_background)

    with TestClient(api.app) as client:
        response = client.get(
            "/api/v1/mentions/reddit",
            params={"title": "Dune", "limit": 5, "offset": 0},
        )

    assert response.status_code == 200
    assert called


def test_mentions_skip_refresh_when_fresh(monkeypatch) -> None:
    called: list[dict[str, object]] = []

    async def fake_get_mentions_from_db(*, title: str, platform: str, limit: int, offset: int):
        _ = (title, platform, limit, offset)
        return api.DbMentionsResult(
            mentions=[_fake_mention(platform)],
            total_count=1,
            next_offset=None,
            title_id=uuid4(),
            last_collected_at=datetime.now(UTC) - timedelta(minutes=1),
        )

    async def fake_refresh_mentions_background(**kwargs):
        called.append(kwargs)

    monkeypatch.setattr(api, "_get_mentions_from_db", fake_get_mentions_from_db)
    monkeypatch.setattr(api, "_refresh_mentions_background", fake_refresh_mentions_background)

    with TestClient(api.app) as client:
        response = client.get(
            "/api/v1/mentions/reddit",
            params={"title": "Dune", "limit": 5, "offset": 0},
        )

    assert response.status_code == 200
    assert not called


def test_bluesky_mentions_refreshes_when_stale(monkeypatch) -> None:
    called: list[dict[str, object]] = []

    async def fake_get_mentions_from_db(*, title: str, platform: str, limit: int, offset: int):
        _ = (title, platform, limit, offset)
        return api.DbMentionsResult(
            mentions=[_fake_mention(platform)],
            total_count=1,
            next_offset=None,
            title_id=uuid4(),
            last_collected_at=datetime.now(UTC) - timedelta(minutes=90),
        )

    async def fake_refresh_mentions_background(**kwargs):
        called.append(kwargs)

    monkeypatch.setattr(api, "_get_mentions_from_db", fake_get_mentions_from_db)
    monkeypatch.setattr(api, "_refresh_mentions_background", fake_refresh_mentions_background)

    with TestClient(api.app) as client:
        response = client.get(
            "/api/v1/mentions/bluesky",
            params={"title": "Dune", "limit": 5, "offset": 0},
        )

    assert response.status_code == 200
    assert called
