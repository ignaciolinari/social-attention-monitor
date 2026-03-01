"""WebSocket endpoint for real-time updates and connection status."""

from __future__ import annotations

import json
import uuid as uuid_mod
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from sam.api.websocket import ws_manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time updates.

    Clients can subscribe to topics:
    - "alerts": Receive new alert notifications
    - "metrics": Receive metrics updates
    - "all": Receive all updates

    Send JSON messages to subscribe/unsubscribe:
    {"action": "subscribe", "topic": "alerts"}
    {"action": "unsubscribe", "topic": "alerts"}
    {"action": "ping"}
    """
    connection_id = str(uuid_mod.uuid4())[:8]

    await ws_manager.connect(websocket, connection_id)

    # Auto-subscribe to "all" by default
    await ws_manager.subscribe(connection_id, "all")

    # Send welcome message
    await websocket.send_json(
        {
            "type": "connected",
            "connection_id": connection_id,
            "subscribed": ["all"],
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )

    try:
        while True:
            try:
                data = await websocket.receive_json()
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            action = data.get("action")

            if action == "ping":
                await websocket.send_json(
                    {"type": "pong", "timestamp": datetime.now(UTC).isoformat()}
                )

            elif action == "subscribe":
                topic = data.get("topic", "all")
                if topic in ("alerts", "metrics", "all"):
                    await ws_manager.subscribe(connection_id, topic)
                    await websocket.send_json(
                        {
                            "type": "subscribed",
                            "topic": topic,
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                    )
                else:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": f"Unknown topic: {topic}. Valid: alerts, metrics, all",
                        }
                    )

            elif action == "unsubscribe":
                topic = data.get("topic")
                if topic:
                    await ws_manager.unsubscribe(connection_id, topic)
                    await websocket.send_json(
                        {
                            "type": "unsubscribed",
                            "topic": topic,
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                    )

            elif action == "status":
                status = await ws_manager.snapshot_status()
                await websocket.send_json(
                    {
                        "type": "status",
                        "connections": status["active_connections"],
                        "subscriptions": status["subscriptions"],
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )

            else:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": (
                            f"Unknown action: {action}. Valid: subscribe, unsubscribe, ping, status"
                        ),
                    }
                )

    except WebSocketDisconnect:
        await ws_manager.disconnect(connection_id)
    except Exception as e:
        logger.warning(f"[ws] Connection {connection_id} error: {e}")
        await ws_manager.disconnect(connection_id)


@router.get("/api/v1/ws/status")
async def websocket_status() -> dict[str, Any]:
    """Get WebSocket connection status."""
    status = await ws_manager.snapshot_status()
    return {
        "active_connections": status["active_connections"],
        "subscriptions": status["subscriptions"],
        "timestamp": datetime.now(UTC).isoformat(),
    }
