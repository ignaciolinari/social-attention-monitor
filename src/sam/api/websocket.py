"""WebSocket connection manager and broadcast helpers for the SAM API."""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket
from loguru import logger
from starlette.websockets import WebSocketState


class ConnectionManager:
    """
    Manages WebSocket connections for real-time updates.

    Supports:
    - Multiple concurrent connections
    - Topic-based subscriptions (alerts, metrics, all)
    - Broadcast to all or filtered subscribers
    - Periodic cleanup of dead connections
    """

    def __init__(self) -> None:
        self.active_connections: dict[str, WebSocket] = {}
        self.subscriptions: dict[str, set[str]] = defaultdict(set)  # topic -> connection_ids
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task[None] | None = None

    async def connect(self, websocket: WebSocket, connection_id: str) -> None:
        """Accept a new WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            self.active_connections[connection_id] = websocket
        logger.info(f"[ws] Client connected: {connection_id}")

    async def disconnect(self, connection_id: str) -> None:
        """Remove a disconnected client."""
        async with self._lock:
            self.active_connections.pop(connection_id, None)
            for topic in list(self.subscriptions.keys()):
                self.subscriptions[topic].discard(connection_id)
        logger.info(f"[ws] Client disconnected: {connection_id}")

    async def subscribe(self, connection_id: str, topic: str) -> None:
        """Subscribe a connection to a topic."""
        async with self._lock:
            self.subscriptions[topic].add(connection_id)
        logger.debug(f"[ws] {connection_id} subscribed to {topic}")

    async def unsubscribe(self, connection_id: str, topic: str) -> None:
        """Unsubscribe a connection from a topic."""
        async with self._lock:
            self.subscriptions[topic].discard(connection_id)

    async def broadcast(self, message: dict[str, Any], topic: str = "all") -> int:
        """
        Broadcast a message to all subscribers of a topic.

        Returns the number of successful sends.
        """
        sent = 0
        to_send: list[tuple[str, WebSocket]] = []
        async with self._lock:
            subscribers = self.subscriptions.get(topic, set()) | self.subscriptions.get(
                "all", set()
            )
            for conn_id in subscribers:
                websocket = self.active_connections.get(conn_id)
                if websocket is not None:
                    to_send.append((conn_id, websocket))

        dead: list[str] = []
        for conn_id, websocket in to_send:
            try:
                await websocket.send_json(message)
                sent += 1
            except Exception as e:
                logger.warning(f"[ws] Failed to send to {conn_id}: {e}")
                dead.append(conn_id)

        if dead:
            async with self._lock:
                for conn_id in dead:
                    self.active_connections.pop(conn_id, None)
                    for t in self.subscriptions:
                        self.subscriptions[t].discard(conn_id)
        return sent

    @property
    def connection_count(self) -> int:
        """Get current number of active connections."""
        return len(self.active_connections)

    async def snapshot_status(self) -> dict[str, Any]:
        """Return a lock-safe snapshot of current connections/subscriptions."""
        async with self._lock:
            return {
                "active_connections": len(self.active_connections),
                "subscriptions": {
                    topic: len(conn_ids) for topic, conn_ids in self.subscriptions.items()
                },
            }

    async def start_cleanup_task(self, interval_seconds: int = 60) -> None:
        """Start periodic cleanup of dead connections."""
        if self._cleanup_task is not None:
            return

        async def _cleanup_loop() -> None:
            while True:
                await asyncio.sleep(interval_seconds)
                await self._cleanup_dead_connections()

        self._cleanup_task = asyncio.create_task(_cleanup_loop())
        logger.info(f"[ws] Started cleanup task (interval={interval_seconds}s)")

    async def stop_cleanup_task(self) -> None:
        """Stop the periodic cleanup task."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None
            logger.info("[ws] Stopped cleanup task")

    async def _cleanup_dead_connections(self) -> None:
        """Remove connections that are no longer alive."""
        dead_connections: list[str] = []

        async with self._lock:
            for conn_id, websocket in list(self.active_connections.items()):
                try:
                    # Check if connection is still open by inspecting state
                    if websocket.client_state != WebSocketState.CONNECTED:
                        dead_connections.append(conn_id)
                except Exception:
                    dead_connections.append(conn_id)

            for conn_id in dead_connections:
                self.active_connections.pop(conn_id, None)
                for topic in self.subscriptions:
                    self.subscriptions[topic].discard(conn_id)

        if dead_connections:
            logger.info(f"[ws] Cleaned up {len(dead_connections)} dead connections")


# Global connection manager singleton
ws_manager = ConnectionManager()


async def broadcast_alert(alert: dict[str, Any]) -> None:
    """Broadcast a new alert to WebSocket subscribers."""
    await ws_manager.broadcast(
        {"type": "alert", "data": alert, "timestamp": datetime.now(UTC).isoformat()},
        topic="alerts",
    )


async def broadcast_metrics_update(title_id: str, metrics: dict[str, Any]) -> None:
    """Broadcast a metrics update to WebSocket subscribers."""
    await ws_manager.broadcast(
        {
            "type": "metrics_update",
            "data": {"title_id": title_id, "metrics": metrics},
            "timestamp": datetime.now(UTC).isoformat(),
        },
        topic="metrics",
    )
