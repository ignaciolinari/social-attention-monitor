from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import sam.api.dependencies as deps
import sam.api.main as api
from sam.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Shared dummy DB / Redis stubs
# ---------------------------------------------------------------------------


class DummySession:
    """Minimal async session stub for route-level tests."""

    def __init__(self) -> None:
        self._items: dict = {}

    async def execute(self, *_a, **_kw):
        return MagicMock(scalar_one=lambda: 0, scalars=lambda: MagicMock(all=lambda: []))

    def add(self, item):
        self._items[id(item)] = item

    async def commit(self):
        pass

    async def refresh(self, item):
        if not hasattr(item, "id") or item.id is None:
            item.id = uuid4()
        if not hasattr(item, "created_at") or item.created_at is None:
            item.created_at = datetime.now(UTC)
        if not hasattr(item, "updated_at") or item.updated_at is None:
            item.updated_at = datetime.now(UTC)

    async def get(self, _model, _pk):
        return None

    async def delete(self, item):
        pass


class DummyRedis:
    """Minimal async Redis stub."""

    async def ping(self):
        return True

    async def get(self, _k):
        return None

    async def set(self, _k, _v, ex=None):
        pass


@asynccontextmanager
async def fake_session():
    yield DummySession()


@pytest.fixture
def client(monkeypatch):
    """Patched TestClient with dummy DB/Redis for route tests."""
    monkeypatch.setattr(deps, "get_session", fake_session)
    monkeypatch.setattr(deps, "get_redis", lambda: DummyRedis())
    monkeypatch.setattr("sam.cache.get_redis", lambda: DummyRedis())
    with TestClient(api.app) as c:
        yield c
