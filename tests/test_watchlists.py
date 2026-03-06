"""Tests for watchlists CRUD endpoints."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import sam.api.dependencies as deps
import sam.api.routes.watchlists as watchlists_route
from sam.config import get_settings
from sam.storage.models import Watchlist


class TestWatchlistsEndpoints:
    def test_list_watchlists_returns_structure(self, client):
        """List watchlists should return proper structure."""
        response = client.get("/api/v1/watchlists")
        assert response.status_code == 200
        data = response.json()
        assert "watchlists" in data
        assert "total_count" in data
        assert isinstance(data["watchlists"], list)

    def test_create_watchlist_returns_201(self, client):
        """Create watchlist should return 201 with created watchlist."""
        response = client.post(
            "/api/v1/watchlists",
            json={"name": "My Watchlist", "tmdb_ids": [1396, 438631]},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "My Watchlist"
        assert data["tmdb_ids"] == [1396, 438631]
        assert "id" in data
        assert "created_at" in data

    def test_create_watchlist_requires_name(self, client):
        """Create watchlist should require a name."""
        response = client.post(
            "/api/v1/watchlists",
            json={"tmdb_ids": []},
        )
        assert response.status_code == 422

    def test_create_watchlist_empty_name_rejected(self, client):
        """Create watchlist should reject empty name."""
        response = client.post(
            "/api/v1/watchlists",
            json={"name": "", "tmdb_ids": []},
        )
        assert response.status_code == 422

    def test_create_watchlist_rejects_non_positive_tmdb_ids(self, client):
        """Create watchlist should reject non-positive TMDB IDs."""
        response = client.post(
            "/api/v1/watchlists",
            json={"name": "Bad IDs", "tmdb_ids": [0, -3, 1396]},
        )
        assert response.status_code == 422

    def test_create_watchlist_rejects_unknown_tmdb_ids(self, client, monkeypatch):
        """Create watchlist should reject IDs that TMDB cannot resolve."""

        async def fake_validate(_ids):
            return [], [999999999]

        monkeypatch.setattr(watchlists_route, "_validate_tmdb_ids_exist", fake_validate)

        response = client.post(
            "/api/v1/watchlists",
            json={"name": "Unknown", "tmdb_ids": [999999999]},
        )
        assert response.status_code == 422
        detail = response.json().get("detail", {})
        assert detail.get("error") == "unknown_tmdb_ids"
        assert detail.get("tmdb_ids") == [999999999]

    def test_create_watchlist_requires_api_key_when_configured(self, client, monkeypatch):
        """Create watchlist should require API key when one is configured."""
        monkeypatch.setenv("SAM_API_KEY", "secret")
        get_settings.cache_clear()

        response = client.post(
            "/api/v1/watchlists",
            json={"name": "My Watchlist", "tmdb_ids": [1396]},
        )

        assert response.status_code == 401

    def test_create_watchlist_with_api_key_succeeds(self, client, monkeypatch):
        """Create watchlist should succeed when a valid API key is provided."""
        monkeypatch.setenv("SAM_API_KEY", "secret")
        get_settings.cache_clear()

        response = client.post(
            "/api/v1/watchlists",
            json={"name": "My Watchlist", "tmdb_ids": [1396]},
            headers={"X-API-Key": "secret"},
        )

        assert response.status_code == 201

    def test_delete_watchlist_not_found(self, client):
        """Delete non-existent watchlist should return 404."""
        fake_id = "00000000-0000-0000-0000-000000000001"
        response = client.delete(f"/api/v1/watchlists/{fake_id}")
        assert response.status_code == 404

    def test_update_watchlist_not_found(self, client):
        """Update non-existent watchlist should return 404."""
        fake_id = "00000000-0000-0000-0000-000000000001"
        response = client.put(
            f"/api/v1/watchlists/{fake_id}",
            json={"name": "Updated", "tmdb_ids": []},
        )
        assert response.status_code == 404

    def test_update_watchlist_success(self, client, monkeypatch):
        """Update watchlist should persist new name and ids."""
        watchlist_id = UUID("00000000-0000-0000-0000-000000000001")
        watchlist = Watchlist(
            id=watchlist_id,
            name="Original",
            tmdb_ids=[1],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        class Session:
            async def get(self, _model, pk):
                return watchlist if pk == watchlist_id else None

            async def commit(self):
                pass

            async def refresh(self, item):
                item.updated_at = datetime.now(UTC)

        @asynccontextmanager
        async def fake_session():
            yield Session()

        monkeypatch.setattr(deps, "get_session", fake_session)

        response = client.put(
            f"/api/v1/watchlists/{watchlist_id}",
            json={"name": "Updated", "tmdb_ids": [1396, 438631]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated"
        assert data["tmdb_ids"] == [1396, 438631]

    def test_update_watchlist_deduplicates_tmdb_ids(self, client, monkeypatch):
        """Update watchlist should keep unique IDs while preserving order."""
        watchlist_id = UUID("00000000-0000-0000-0000-000000000001")
        watchlist = Watchlist(
            id=watchlist_id,
            name="Original",
            tmdb_ids=[1],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        class Session:
            async def get(self, _model, pk):
                return watchlist if pk == watchlist_id else None

            async def commit(self):
                pass

            async def refresh(self, item):
                item.updated_at = datetime.now(UTC)

        @asynccontextmanager
        async def fake_session():
            yield Session()

        monkeypatch.setattr(deps, "get_session", fake_session)

        response = client.put(
            f"/api/v1/watchlists/{watchlist_id}",
            json={"name": "Updated", "tmdb_ids": [1396, 438631, 1396, 438631, 95396]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["tmdb_ids"] == [1396, 438631, 95396]

    def test_update_watchlist_rejects_unknown_tmdb_ids(self, client, monkeypatch):
        """Update watchlist should reject IDs that TMDB cannot resolve."""
        watchlist_id = UUID("00000000-0000-0000-0000-000000000001")
        watchlist = Watchlist(
            id=watchlist_id,
            name="Original",
            tmdb_ids=[1],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        class Session:
            async def get(self, _model, pk):
                return watchlist if pk == watchlist_id else None

            async def commit(self):
                pass

            async def refresh(self, item):
                item.updated_at = datetime.now(UTC)

        @asynccontextmanager
        async def fake_session():
            yield Session()

        async def fake_validate(_ids):
            return [], [424242424]

        monkeypatch.setattr(deps, "get_session", fake_session)
        monkeypatch.setattr(watchlists_route, "_validate_tmdb_ids_exist", fake_validate)

        response = client.put(
            f"/api/v1/watchlists/{watchlist_id}",
            json={"name": "Updated", "tmdb_ids": [424242424]},
        )

        assert response.status_code == 422
        detail = response.json().get("detail", {})
        assert detail.get("error") == "unknown_tmdb_ids"
        assert detail.get("tmdb_ids") == [424242424]

    def test_delete_watchlist_success(self, client, monkeypatch):
        """Delete watchlist should return deleted=true for existing item."""
        watchlist_id = UUID("00000000-0000-0000-0000-000000000001")
        watchlist = Watchlist(
            id=watchlist_id,
            name="Delete Me",
            tmdb_ids=[1],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        deleted: list[Watchlist] = []

        class Session:
            async def get(self, _model, pk):
                return watchlist if pk == watchlist_id else None

            async def delete(self, item):
                deleted.append(item)

            async def commit(self):
                pass

        @asynccontextmanager
        async def fake_session():
            yield Session()

        monkeypatch.setattr(deps, "get_session", fake_session)

        response = client.delete(f"/api/v1/watchlists/{watchlist_id}")

        assert response.status_code == 200
        assert response.json() == {"deleted": True, "id": str(watchlist_id)}
        assert deleted == [watchlist]
