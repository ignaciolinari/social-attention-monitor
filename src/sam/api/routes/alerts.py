"""Alert listing, counts, acknowledgement, detection and system-health."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from sam.api import dependencies as deps
from sam.api.schemas import (
    AlertAckResponse,
    AlertCountsResponse,
    AlertResponse,
    AlertsListResponse,
)
from sam.api.websocket import broadcast_alert
from sam.cache import publish_alert_event

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


@router.get("", response_model=AlertsListResponse)
async def list_alerts(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    title_id: UUID | None = Query(None, description="Filter by title"),
    severity: str | None = Query(None, description="Filter by severity: info, warning, critical"),
    hours: int = Query(24, ge=1, le=24 * 30, description="Look back period in hours"),
) -> AlertsListResponse:
    """Get recent alerts with optional filtering."""
    since = datetime.now(UTC) - timedelta(hours=hours)

    async with deps.get_session() as session:
        total_matching = await deps.count_alerts(
            session,
            title_id=title_id,
            severity=severity,
            since=since,
        )
        unack_total = await deps.get_unacknowledged_count(session)
        unack_filtered = await deps.count_unacknowledged_alerts(
            session,
            title_id=title_id,
            severity=severity,
            since=since,
        )
        alerts = await deps.get_recent_alerts(
            session,
            limit=limit,
            offset=offset,
            title_id=title_id,
            severity=severity,
            since=since,
        )

    has_more = (offset + len(alerts)) < total_matching

    return AlertsListResponse(
        alerts=[
            AlertResponse(
                id=str(a.id),
                title_id=str(a.title_id),
                alert_type=a.alert_type,
                severity=a.severity,
                message=a.message,
                details=a.details,
                created_at=a.created_at.isoformat(),
                acknowledged_at=(a.acknowledged_at.isoformat() if a.acknowledged_at else None),
            )
            for a in alerts
        ],
        total_count=total_matching,
        next_offset=offset + limit if has_more else None,
        unacknowledged_count_total=unack_total,
        unacknowledged_count=unack_filtered,
    )


@router.get("/counts", response_model=AlertCountsResponse)
async def alert_counts(
    hours: int = Query(24, ge=1, le=24 * 30, description="Look back period in hours"),
) -> AlertCountsResponse:
    """Get alert counts by severity."""
    since = datetime.now(UTC) - timedelta(hours=hours)

    async with deps.get_session() as session:
        counts = await deps.get_alert_counts_by_severity(session, since=since)
        unack_count = await deps.get_unacknowledged_count(session)
        unack_in_window = await deps.count_unacknowledged_alerts(session, since=since)

    return AlertCountsResponse(
        counts=counts,
        total=sum(counts.values()),
        unacknowledged=unack_count,
        unacknowledged_in_window=unack_in_window,
    )


@router.post("/{alert_id}/acknowledge", response_model=AlertAckResponse)
async def ack_alert(alert_id: UUID) -> AlertAckResponse:
    """Acknowledge an alert."""
    async with deps.get_session() as session:
        success = await deps.acknowledge_alert(session, alert_id=alert_id)
        if not success:
            raise HTTPException(status_code=404, detail="Alert not found")

    return AlertAckResponse(acknowledged=True, alert_id=str(alert_id))


@router.post("/run-detection")
async def run_alert_detection(
    window_hours: int = Query(1, ge=1, le=24),
    history_points: int = Query(24, ge=5, le=168),
) -> dict[str, Any]:
    """Manually trigger anomaly detection cycle.

    This is primarily for testing/debugging. In production,
    detection runs automatically via the scheduler.
    """
    manager = deps.AlertManager()

    async with deps.get_session() as session:
        detected, created_alerts = await manager.run_detection_cycle(
            session, window_hours=window_hours, history_points=history_points
        )

    # Broadcast after commit (session context exited)
    for alert in created_alerts:
        published = await publish_alert_event(alert)
        if not published:
            await broadcast_alert(alert)

    return {
        "anomalies_detected": detected,
        "alerts_created": len(created_alerts),
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/system-health")
async def system_health_check() -> dict[str, Any]:
    """Run system-level health checks.

    Returns detected issues without persisting them (system alerts have no
    title FK).  Use this for dashboards or external monitoring integrations.
    """
    manager = deps.AlertManager()

    async with deps.get_session() as session:
        anomalies = await manager.check_system_health(session)

    issues = [
        {
            "alert_type": a.alert_type.value,
            "severity": a.severity.value,
            "message": a.message,
            "details": a.details,
        }
        for a in anomalies
    ]

    return {
        "healthy": len(issues) == 0,
        "issues": issues,
        "timestamp": datetime.now(UTC).isoformat(),
    }
