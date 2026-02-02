"""
Alert Manager - Orchestrates anomaly detection and alert lifecycle.

Provides:
- Periodic anomaly detection for all active titles
- Alert persistence and deduplication
- Alert acknowledgment and resolution
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from loguru import logger
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sam.alerts.detector import (
    AnomalyDetector,
    DetectedAnomaly,
    MetricsWindow,
)
from sam.storage.models import Alert, MetricsSnapshot, Title


class AlertManager:
    """
    Manages the alert lifecycle.

    - Runs anomaly detection across titles
    - Persists new alerts (with deduplication)
    - Provides query interfaces for alerts
    """

    def __init__(
        self,
        detector: AnomalyDetector | None = None,
        dedup_window_minutes: int = 60,
    ):
        """
        Initialize the alert manager.

        Args:
            detector: AnomalyDetector instance (creates default if None)
            dedup_window_minutes: Don't create duplicate alerts within this window
        """
        self.detector = detector or AnomalyDetector()
        self.dedup_window_minutes = dedup_window_minutes

    async def check_title_for_anomalies(
        self,
        session: AsyncSession,
        title: Title,
        window_hours: int = 1,
        history_points: int = 24,
    ) -> list[DetectedAnomaly]:
        """
        Check a single title for anomalies.

        Args:
            session: Database session
            title: Title to check
            window_hours: Metrics window size
            history_points: Number of historical snapshots to use

        Returns:
            List of detected anomalies
        """
        # Get historical snapshots
        stmt = (
            select(MetricsSnapshot)
            .where(
                MetricsSnapshot.title_id == title.id,
                MetricsSnapshot.window_hours == window_hours,
            )
            .order_by(MetricsSnapshot.snapshot_time.desc())
            .limit(history_points + 1)  # +1 for current
        )
        result = await session.execute(stmt)
        snapshots = list(result.scalars().all())

        if len(snapshots) < 2:
            return []

        # Most recent is "current", rest is history
        current_snapshot = snapshots[0]
        history_snapshots = list(reversed(snapshots[1:]))

        current = self._snapshot_to_window(current_snapshot)
        history = [self._snapshot_to_window(s) for s in history_snapshots]

        anomalies = self.detector.detect_anomalies(
            current=current,
            history=history,
            title_id=str(title.id),
            title_name=title.title,
        )

        return anomalies

    async def check_all_titles(
        self,
        session: AsyncSession,
        window_hours: int = 1,
        history_points: int = 24,
    ) -> list[DetectedAnomaly]:
        """
        Check all active titles for anomalies.

        Returns:
            Combined list of all detected anomalies
        """
        stmt = select(Title).where(Title.is_active.is_(True))
        result = await session.execute(stmt)
        titles = list(result.scalars().all())

        all_anomalies: list[DetectedAnomaly] = []

        for title in titles:
            try:
                anomalies = await self.check_title_for_anomalies(
                    session, title, window_hours, history_points
                )
                all_anomalies.extend(anomalies)
            except Exception as e:
                logger.warning(f"[alerts] Error checking {title.title}: {e}")

        logger.info(f"[alerts] Checked {len(titles)} titles, found {len(all_anomalies)} anomalies")
        return all_anomalies

    async def persist_anomalies(
        self,
        session: AsyncSession,
        anomalies: list[DetectedAnomaly],
    ) -> list[dict[str, Any]]:
        """
        Persist detected anomalies as alerts (with deduplication).

        Returns:
            List of newly created alerts (serialized)
        """
        if not anomalies:
            return []

        created_alerts: list[Alert] = []
        dedup_cutoff = datetime.now(UTC) - timedelta(minutes=self.dedup_window_minutes)

        for anomaly in anomalies:
            # Check for recent duplicate
            existing_stmt = select(Alert).where(
                and_(
                    Alert.title_id == UUID(anomaly.title_id),
                    Alert.alert_type == anomaly.alert_type.value,
                    Alert.created_at >= dedup_cutoff,
                )
            )
            existing = await session.execute(existing_stmt)
            if existing.scalars().first():
                logger.debug(
                    f"[alerts] Skipping duplicate {anomaly.alert_type.value} for {anomaly.title_name}"
                )
                continue

            # Create new alert
            alert = Alert(
                title_id=UUID(anomaly.title_id),
                alert_type=anomaly.alert_type.value,
                severity=anomaly.severity.value,
                message=anomaly.message,
                details=anomaly.details,
                created_at=anomaly.detected_at,
            )
            session.add(alert)
            created_alerts.append(alert)
            logger.info(f"[alerts] Created {anomaly.severity.value} alert: {anomaly.message}")

        if created_alerts:
            await session.flush()

        return [_serialize_alert(alert) for alert in created_alerts]

    async def run_detection_cycle(
        self,
        session: AsyncSession,
        window_hours: int = 1,
        history_points: int = 24,
    ) -> tuple[int, list[dict[str, Any]]]:
        """
        Run a full detection cycle: check all titles and persist alerts.

        Returns:
            Tuple of (anomalies_detected, created_alerts)
        """
        anomalies = await self.check_all_titles(session, window_hours, history_points)
        created_alerts = await self.persist_anomalies(session, anomalies)
        return len(anomalies), created_alerts

    def _snapshot_to_window(self, snapshot: MetricsSnapshot) -> MetricsWindow:
        """Convert a MetricsSnapshot to a MetricsWindow."""
        return MetricsWindow(
            mention_count=snapshot.mention_count if snapshot.mention_count is not None else 0,
            mention_velocity=snapshot.mention_velocity,
            velocity_change=snapshot.velocity_change,
            avg_sentiment=snapshot.avg_sentiment,
            sentiment_volatility=snapshot.sentiment_volatility,
            attention_index=snapshot.attention_index,
            hype_acceleration=snapshot.hype_acceleration,
            unique_authors=snapshot.unique_authors if snapshot.unique_authors is not None else 0,
            snapshot_time=snapshot.snapshot_time,
        )


def _serialize_alert(alert: Alert) -> dict[str, Any]:
    """Serialize an Alert ORM object into a JSON-friendly payload."""
    return {
        "id": str(alert.id),
        "title_id": str(alert.title_id),
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "message": alert.message,
        "details": alert.details,
        "created_at": alert.created_at.isoformat(),
        "acknowledged_at": alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
    }


# Repository functions for alerts


async def get_recent_alerts(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    title_id: UUID | None = None,
    severity: str | None = None,
    since: datetime | None = None,
) -> list[Alert]:
    """Get recent alerts with optional filtering."""
    stmt = select(Alert).order_by(Alert.created_at.desc())

    if title_id:
        stmt = stmt.where(Alert.title_id == title_id)
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    if since:
        stmt = stmt.where(Alert.created_at >= since)

    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_alerts(
    session: AsyncSession,
    *,
    title_id: UUID | None = None,
    severity: str | None = None,
    since: datetime | None = None,
) -> int:
    """Count alerts matching optional filters."""
    from sqlalchemy import func

    stmt = select(func.count()).select_from(Alert)
    if title_id:
        stmt = stmt.where(Alert.title_id == title_id)
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    if since:
        stmt = stmt.where(Alert.created_at >= since)

    result = await session.execute(stmt)
    return int(result.scalar_one())


async def get_alert_counts_by_severity(
    session: AsyncSession,
    *,
    since: datetime | None = None,
) -> dict[str, int]:
    """Get alert counts grouped by severity."""
    from sqlalchemy import func

    stmt = select(Alert.severity, func.count().label("cnt")).group_by(Alert.severity)
    if since:
        stmt = stmt.where(Alert.created_at >= since)

    result = await session.execute(stmt)
    return {row.severity: row.cnt for row in result.all()}


async def acknowledge_alert(
    session: AsyncSession,
    *,
    alert_id: UUID,
) -> bool:
    """Acknowledge an alert."""
    result = await session.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalars().first()
    if not alert:
        return False

    alert.acknowledged_at = datetime.now(UTC)
    return True


async def get_unacknowledged_count(session: AsyncSession) -> int:
    """Get count of unacknowledged alerts."""
    from sqlalchemy import func

    stmt = select(func.count()).where(Alert.acknowledged_at.is_(None))
    result = await session.execute(stmt)
    return result.scalar_one()


async def count_unacknowledged_alerts(
    session: AsyncSession,
    *,
    title_id: UUID | None = None,
    severity: str | None = None,
    since: datetime | None = None,
) -> int:
    """Count unacknowledged alerts matching optional filters."""
    from sqlalchemy import func

    stmt = select(func.count()).select_from(Alert).where(Alert.acknowledged_at.is_(None))
    if title_id:
        stmt = stmt.where(Alert.title_id == title_id)
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    if since:
        stmt = stmt.where(Alert.created_at >= since)

    result = await session.execute(stmt)
    return int(result.scalar_one())
