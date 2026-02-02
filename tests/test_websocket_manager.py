"""Tests for WebSocket connection manager."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


class TestConnectionManager:
    """Tests for the ConnectionManager class."""

    @pytest.fixture
    def manager(self):
        """Create a fresh ConnectionManager for each test."""
        # Import here to avoid import-time side effects
        from sam.api.main import ConnectionManager

        return ConnectionManager()

    @pytest.mark.asyncio
    async def test_connect_adds_connection(self, manager) -> None:
        """Test that connecting adds a connection to the manager."""
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()

        await manager.connect(mock_ws, "test-conn-1")

        assert manager.connection_count == 1
        assert "test-conn-1" in manager.active_connections

    @pytest.mark.asyncio
    async def test_disconnect_removes_connection(self, manager) -> None:
        """Test that disconnecting removes a connection."""
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()

        await manager.connect(mock_ws, "test-conn-1")
        assert manager.connection_count == 1

        await manager.disconnect("test-conn-1")
        assert manager.connection_count == 0

    @pytest.mark.asyncio
    async def test_subscribe_adds_to_topic(self, manager) -> None:
        """Test that subscribing adds connection to topic."""
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()

        await manager.connect(mock_ws, "test-conn-1")
        await manager.subscribe("test-conn-1", "alerts")

        assert "test-conn-1" in manager.subscriptions["alerts"]

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_from_topic(self, manager) -> None:
        """Test that unsubscribing removes connection from topic."""
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()

        await manager.connect(mock_ws, "test-conn-1")
        await manager.subscribe("test-conn-1", "alerts")
        await manager.unsubscribe("test-conn-1", "alerts")

        assert "test-conn-1" not in manager.subscriptions["alerts"]

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_subscribers(self, manager) -> None:
        """Test that broadcast sends to topic subscribers."""
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_json = AsyncMock()

        await manager.connect(mock_ws, "test-conn-1")
        await manager.subscribe("test-conn-1", "alerts")

        sent = await manager.broadcast({"type": "test"}, topic="alerts")

        assert sent == 1
        mock_ws.send_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_all_topic_receives_all(self, manager) -> None:
        """Test that 'all' topic receives broadcasts from any topic."""
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_json = AsyncMock()

        await manager.connect(mock_ws, "test-conn-1")
        await manager.subscribe("test-conn-1", "all")

        sent = await manager.broadcast({"type": "alert"}, topic="alerts")

        assert sent == 1
        mock_ws.send_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_cleans_subscriptions(self, manager) -> None:
        """Test that disconnect removes connection from all subscriptions."""
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()

        await manager.connect(mock_ws, "test-conn-1")
        await manager.subscribe("test-conn-1", "alerts")
        await manager.subscribe("test-conn-1", "metrics")

        await manager.disconnect("test-conn-1")

        assert "test-conn-1" not in manager.subscriptions.get("alerts", set())
        assert "test-conn-1" not in manager.subscriptions.get("metrics", set())

    @pytest.mark.asyncio
    async def test_cleanup_task_can_start_and_stop(self, manager) -> None:
        """Test that cleanup task lifecycle works."""
        await manager.start_cleanup_task(interval_seconds=1)
        assert manager._cleanup_task is not None

        await manager.stop_cleanup_task()
        assert manager._cleanup_task is None

    @pytest.mark.asyncio
    async def test_broadcast_removes_dead_connections(self, manager) -> None:
        """Test that broadcast cleans up dead connections on failure."""
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_json = AsyncMock(side_effect=Exception("Connection closed"))

        await manager.connect(mock_ws, "dead-conn")
        await manager.subscribe("dead-conn", "alerts")

        sent = await manager.broadcast({"type": "test"}, topic="alerts")

        assert sent == 0
        assert "dead-conn" not in manager.active_connections
