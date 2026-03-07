"""
Alert Manager - Orchestrates anomaly detection and alert lifecycle.

Provides:
- Periodic anomaly detection for all active titles
- System-level self-health checks (no ingest, collector failures, quota)
- Alert persistence and deduplication
- Alert acknowledgment and resolution
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from loguru import logger
from sqlalchemy import and_, select
from sqlalchemy import func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from sam.alerts.detector import (
    AlertType,
    AnomalyDetector,
    DetectedAnomaly,
    MetricsWindow,
    Severity,
)
from sam.storage.models import Alert, MetricsSnapshot, PipelineRun, Title


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
        freshness_hours: int = 48,
    ):
        """
        Initialize the alert manager.

        Args:
            detector: AnomalyDetector instance (creates default if None)
            dedup_window_minutes: Don't create duplicate alerts within this window
        """
        self.detector = detector or AnomalyDetector()
        self.dedup_window_minutes = dedup_window_minutes
        self.freshness_hours = freshness_hours

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
        freshness_cutoff = datetime.now(UTC) - timedelta(hours=self.freshness_hours)
        if current_snapshot.snapshot_time < freshness_cutoff:
            return []
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

    # ------------------------------------------------------------------
    # Self-health checks (system-level)
    # ------------------------------------------------------------------

    async def check_system_health(
        self,
        session: AsyncSession,
        *,
        no_ingest_minutes: int = 120,
        quota_warn_pct: float = 80.0,
    ) -> list[DetectedAnomaly]:
        """Run system-level health checks and return detected anomalies.

        Checks:
        1. **No-ingest** – no new mentions across *any* title within the
           look-back window (``no_ingest_minutes``).
        2. **Collector failures** – the most recent ``collector-cycle`` pipeline
           run ended with ``status = 'failed'``.
        3. **Quota threshold** – YouTube daily quota usage exceeds
           ``quota_warn_pct`` (requires ``sam.quota``).
        4. **Redis degraded** – Redis is unreachable (graceful degradation is
           still active, but caching / toggles are impaired).

        .. note::
           Because the ``Alert`` table has a FK to ``titles``, system-level
           anomalies are **not** persisted in the DB.  Callers can broadcast
           them via WebSocket or log them for observability.
        """
        now = datetime.now(UTC)
        anomalies: list[DetectedAnomaly] = []
        # Use a well-known UUID for system-level alerts (not tied to a title).
        system_title_id = "00000000-0000-0000-0000-000000000000"

        # 1. No-ingest check ------------------------------------------------
        cutoff = now - timedelta(minutes=no_ingest_minutes)
        from sam.storage.models import Mention

        count_stmt = (
            select(sa_func.count()).select_from(Mention).where(Mention.collected_at >= cutoff)
        )
        result = await session.execute(count_stmt)
        recent_count = int(result.scalar_one())
        if recent_count == 0:
            anomalies.append(
                DetectedAnomaly(
                    alert_type=AlertType.NO_INGEST,
                    severity=Severity.WARNING,
                    message=(
                        f"No mentions ingested in the last {no_ingest_minutes} minutes. "
                        "Collectors may be stalled or misconfigured."
                    ),
                    details={"look_back_minutes": no_ingest_minutes},
                    detected_at=now,
                    title_id=system_title_id,
                    title_name="[system]",
                )
            )

        # 2. Collector failure check ----------------------------------------
        last_run_stmt = (
            select(PipelineRun)
            .where(PipelineRun.job_name == "collector-cycle")
            .order_by(PipelineRun.started_at.desc())
            .limit(1)
        )
        last_run_result = await session.execute(last_run_stmt)
        last_run = last_run_result.scalars().first()
        if last_run is not None and last_run.status == "failed":
            anomalies.append(
                DetectedAnomaly(
                    alert_type=AlertType.COLLECTOR_FAILURE,
                    severity=Severity.WARNING,
                    message=(
                        f"Last collector-cycle run failed"
                        f"{': ' + last_run.error if last_run.error else '.'}"
                    ),
                    details={
                        "run_id": str(last_run.id),
                        "started_at": last_run.started_at.isoformat(),
                        "error": last_run.error,
                    },
                    detected_at=now,
                    title_id=system_title_id,
                    title_name="[system]",
                )
            )

        # 3. YouTube quota threshold ----------------------------------------
        try:
            from sam.quota import YOUTUBE_DAILY_BUDGET, aggregate_youtube_quota_from_db

            quota = await aggregate_youtube_quota_from_db(session)
            if quota.budget_used_pct is not None and quota.budget_used_pct >= quota_warn_pct:
                sev = Severity.CRITICAL if quota.budget_used_pct >= 95.0 else Severity.WARNING
                anomalies.append(
                    DetectedAnomaly(
                        alert_type=AlertType.QUOTA_THRESHOLD,
                        severity=sev,
                        message=(
                            f"YouTube API quota at {quota.budget_used_pct:.1f}% "
                            f"({quota.total_units}/{YOUTUBE_DAILY_BUDGET} units)."
                        ),
                        details={
                            "used_pct": quota.budget_used_pct,
                            "total_units": quota.total_units,
                            "daily_budget": YOUTUBE_DAILY_BUDGET,
                        },
                        detected_at=now,
                        title_id=system_title_id,
                        title_name="[system]",
                    )
                )
        except Exception as exc:
            logger.debug(f"[alerts] Quota check skipped: {exc}")

        # 4. Redis degraded -------------------------------------------------
        try:
            from sam.cache import get_redis

            redis = get_redis()
            if redis is None:
                anomalies.append(
                    DetectedAnomaly(
                        alert_type=AlertType.REDIS_DEGRADED,
                        severity=Severity.INFO,
                        message="Redis is unavailable — caching and collector toggles are impaired.",
                        details={},
                        detected_at=now,
                        title_id=system_title_id,
                        title_name="[system]",
                    )
                )
            else:
                pong = await redis.ping()  # type: ignore[misc]
                if not pong:
                    raise ConnectionError("ping returned False")
        except Exception:
            anomalies.append(
                DetectedAnomaly(
                    alert_type=AlertType.REDIS_DEGRADED,
                    severity=Severity.INFO,
                    message="Redis is unavailable — caching and collector toggles are impaired.",
                    details={},
                    detected_at=now,
                    title_id=system_title_id,
                    title_name="[system]",
                )
            )

        if anomalies:
            logger.info(f"[alerts] System health check found {len(anomalies)} issue(s)")
        return anomalies

    def _snapshot_to_window(self, snapshot: MetricsSnapshot) -> MetricsWindow:
        """Convert a MetricsSnapshot to a MetricsWindow."""
        raw = snapshot.raw_metrics or {}
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
            sentiment_divergence=raw.get("sentiment_divergence"),
            author_diversity_score=raw.get("author_diversity_score"),
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
    unacknowledged_only: bool = False,
) -> list[Alert]:
    """Get recent alerts with optional filtering."""
    stmt = select(Alert).order_by(Alert.created_at.desc())

    if title_id:
        stmt = stmt.where(Alert.title_id == title_id)
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    if since:
        stmt = stmt.where(Alert.created_at >= since)
    if unacknowledged_only:
        stmt = stmt.where(Alert.acknowledged_at.is_(None))

    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_alerts(
    session: AsyncSession,
    *,
    title_id: UUID | None = None,
    severity: str | None = None,
    since: datetime | None = None,
    unacknowledged_only: bool = False,
) -> int:
    """Count alerts matching optional filters."""
    stmt = select(sa_func.count()).select_from(Alert)
    if title_id:
        stmt = stmt.where(Alert.title_id == title_id)
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    if since:
        stmt = stmt.where(Alert.created_at >= since)
    if unacknowledged_only:
        stmt = stmt.where(Alert.acknowledged_at.is_(None))

    result = await session.execute(stmt)
    return int(result.scalar_one())


async def get_alert_counts_by_severity(
    session: AsyncSession,
    *,
    since: datetime | None = None,
) -> dict[str, int]:
    """Get alert counts grouped by severity."""
    stmt = select(Alert.severity, sa_func.count().label("cnt")).group_by(Alert.severity)
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
    stmt = select(sa_func.count()).where(Alert.acknowledged_at.is_(None))
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
    stmt = select(sa_func.count()).select_from(Alert).where(Alert.acknowledged_at.is_(None))
    if title_id:
        stmt = stmt.where(Alert.title_id == title_id)
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    if since:
        stmt = stmt.where(Alert.created_at >= since)

    result = await session.execute(stmt)
    return int(result.scalar_one())
