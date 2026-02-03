"""
Anomaly Detection for Social Attention Metrics.

Detects unusual patterns in engagement metrics:
- Mention spikes (sudden volume increase)
- Sentiment shifts (rapid sentiment change)
- Velocity surges (acceleration anomalies)
- Viral breakouts (compound anomaly)
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import numpy as np
from loguru import logger


class AlertType(str, Enum):
    """Types of anomalies that can be detected."""

    MENTION_SPIKE = "mention_spike"
    SENTIMENT_SHIFT = "sentiment_shift"
    VELOCITY_SURGE = "velocity_surge"
    VIRAL_BREAKOUT = "viral_breakout"
    ATTENTION_SPIKE = "attention_spike"


class Severity(str, Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class DetectedAnomaly:
    """Represents a detected anomaly."""

    alert_type: AlertType
    severity: Severity
    message: str
    details: dict[str, Any]
    detected_at: datetime
    title_id: str
    title_name: str


@dataclass
class MetricsWindow:
    """A window of metrics for anomaly detection."""

    mention_count: int
    mention_velocity: float | None
    velocity_change: float | None
    avg_sentiment: float | None
    sentiment_volatility: float | None
    attention_index: float | None
    hype_acceleration: float | None
    unique_authors: int
    snapshot_time: datetime


class AnomalyDetector:
    """
    Statistical anomaly detector for engagement metrics.

    Uses z-score based detection with configurable thresholds.
    Maintains a rolling window of historical data for baseline computation.
    """

    def __init__(
        self,
        spike_threshold_std: float = 2.5,
        sentiment_shift_threshold: float = 0.3,
        velocity_surge_threshold_std: float = 2.0,
        min_history_points: int = 5,
    ):
        """
        Initialize the anomaly detector.

        Args:
            spike_threshold_std: Standard deviations for mention spike detection
            sentiment_shift_threshold: Absolute change for sentiment shift
            velocity_surge_threshold_std: Standard deviations for velocity surge
            min_history_points: Minimum historical points needed for detection
        """
        self.spike_threshold_std = spike_threshold_std
        self.sentiment_shift_threshold = sentiment_shift_threshold
        self.velocity_surge_threshold_std = velocity_surge_threshold_std
        self.min_history_points = min_history_points

    def detect_anomalies(
        self,
        current: MetricsWindow,
        history: list[MetricsWindow],
        title_id: str,
        title_name: str,
    ) -> list[DetectedAnomaly]:
        """
        Detect anomalies by comparing current metrics against historical baseline.

        Args:
            current: Current metrics window
            history: Historical metrics windows (oldest first)
            title_id: UUID of the title
            title_name: Name of the title for messages

        Returns:
            List of detected anomalies
        """
        anomalies: list[DetectedAnomaly] = []
        now = datetime.now(UTC)

        if len(history) < self.min_history_points:
            logger.debug(
                f"[anomaly] Insufficient history for {title_name}: "
                f"{len(history)}/{self.min_history_points} points"
            )
            return anomalies

        # Check for mention spike
        spike = self._detect_mention_spike(current, history, title_id, title_name, now)
        if spike:
            anomalies.append(spike)

        # Check for sentiment shift
        sentiment = self._detect_sentiment_shift(current, history, title_id, title_name, now)
        if sentiment:
            anomalies.append(sentiment)

        # Check for velocity surge
        velocity = self._detect_velocity_surge(current, history, title_id, title_name, now)
        if velocity:
            anomalies.append(velocity)

        # Check for attention spike
        attention = self._detect_attention_spike(current, history, title_id, title_name, now)
        if attention:
            anomalies.append(attention)

        # Check for viral breakout (compound anomaly)
        if len(anomalies) >= 2:
            viral = self._detect_viral_breakout(anomalies, current, title_id, title_name, now)
            if viral:
                anomalies.append(viral)

        return anomalies

    def _detect_mention_spike(
        self,
        current: MetricsWindow,
        history: list[MetricsWindow],
        title_id: str,
        title_name: str,
        now: datetime,
    ) -> DetectedAnomaly | None:
        """Detect sudden increase in mention volume."""
        counts = [h.mention_count for h in history]
        mean = float(np.mean(counts))
        std = float(np.std(counts))

        if std == 0:
            min_increase = max(5.0, mean * 0.5)
            threshold = mean + min_increase
            if current.mention_count < threshold:
                return None

            severity = (
                Severity.CRITICAL
                if current.mention_count >= mean + (min_increase * 2)
                else Severity.WARNING
            )
            return DetectedAnomaly(
                alert_type=AlertType.MENTION_SPIKE,
                severity=severity,
                message=f"🔥 {title_name} is experiencing a mention spike! "
                f"{current.mention_count} mentions (baseline ~{mean:.1f})",
                details={
                    "current_mentions": current.mention_count,
                    "historical_mean": round(mean, 1),
                    "historical_std": round(std, 2),
                    "z_score": None,
                    "increase_pct": round((current.mention_count - mean) / mean * 100, 1)
                    if mean > 0
                    else 0,
                },
                detected_at=now,
                title_id=title_id,
                title_name=title_name,
            )

        z_score = (current.mention_count - mean) / std

        if z_score >= self.spike_threshold_std:
            severity = (
                Severity.CRITICAL if z_score >= self.spike_threshold_std * 1.5 else Severity.WARNING
            )
            return DetectedAnomaly(
                alert_type=AlertType.MENTION_SPIKE,
                severity=severity,
                message=f"🔥 {title_name} is experiencing a mention spike! "
                f"{current.mention_count} mentions (z={z_score:.1f}σ above normal)",
                details={
                    "current_mentions": current.mention_count,
                    "historical_mean": round(mean, 1),
                    "historical_std": round(std, 2),
                    "z_score": round(z_score, 2),
                    "increase_pct": round((current.mention_count - mean) / mean * 100, 1)
                    if mean > 0
                    else 0,
                },
                detected_at=now,
                title_id=title_id,
                title_name=title_name,
            )
        return None

    def _detect_sentiment_shift(
        self,
        current: MetricsWindow,
        history: list[MetricsWindow],
        title_id: str,
        title_name: str,
        now: datetime,
    ) -> DetectedAnomaly | None:
        """Detect rapid change in sentiment."""
        if not history or current.avg_sentiment is None:
            return None

        # Compare to recent average
        recent_sentiments = [h.avg_sentiment for h in history[-5:] if h.avg_sentiment is not None]
        if len(recent_sentiments) < min(self.min_history_points, 5):
            return None
        recent_mean = float(np.mean(recent_sentiments))
        shift = current.avg_sentiment - recent_mean

        if abs(shift) >= self.sentiment_shift_threshold:
            direction = "positive" if shift > 0 else "negative"
            emoji = "📈" if shift > 0 else "📉"
            severity = Severity.WARNING if abs(shift) >= 0.5 else Severity.INFO

            return DetectedAnomaly(
                alert_type=AlertType.SENTIMENT_SHIFT,
                severity=severity,
                message=f"{emoji} {title_name} sentiment shifted {direction}! "
                f"({recent_mean:.2f} → {current.avg_sentiment:.2f})",
                details={
                    "current_sentiment": round(current.avg_sentiment, 3),
                    "previous_mean": round(recent_mean, 3),
                    "shift": round(shift, 3),
                    "direction": direction,
                    "volatility": round(current.sentiment_volatility, 3)
                    if current.sentiment_volatility is not None
                    else None,
                },
                detected_at=now,
                title_id=title_id,
                title_name=title_name,
            )
        return None

    def _detect_velocity_surge(
        self,
        current: MetricsWindow,
        history: list[MetricsWindow],
        title_id: str,
        title_name: str,
        now: datetime,
    ) -> DetectedAnomaly | None:
        """Detect unusual acceleration in mention velocity."""
        if current.mention_velocity is None:
            return None

        velocities = [h.mention_velocity for h in history if h.mention_velocity is not None]
        if len(velocities) < self.min_history_points:
            return None
        mean = float(np.mean(velocities))
        std = float(np.std(velocities))

        if std == 0:
            return None

        z_score = (current.mention_velocity - mean) / std

        if z_score >= self.velocity_surge_threshold_std:
            severity = (
                Severity.CRITICAL
                if z_score >= self.velocity_surge_threshold_std * 1.5
                else Severity.WARNING
            )
            return DetectedAnomaly(
                alert_type=AlertType.VELOCITY_SURGE,
                severity=severity,
                message=f"⚡ {title_name} velocity surge detected! "
                f"{current.mention_velocity:.1f}/hr (z={z_score:.1f}σ)",
                details={
                    "current_velocity": round(current.mention_velocity, 2),
                    "historical_mean": round(mean, 2),
                    "historical_std": round(std, 3),
                    "z_score": round(z_score, 2),
                    "acceleration": round(current.velocity_change, 2)
                    if current.velocity_change is not None
                    else None,
                },
                detected_at=now,
                title_id=title_id,
                title_name=title_name,
            )
        return None

    def _detect_attention_spike(
        self,
        current: MetricsWindow,
        history: list[MetricsWindow],
        title_id: str,
        title_name: str,
        now: datetime,
    ) -> DetectedAnomaly | None:
        """Detect spike in attention index."""
        if current.attention_index is None:
            return None

        indices = [h.attention_index for h in history if h.attention_index is not None]
        if len(indices) < self.min_history_points:
            return None

        mean = float(np.mean(indices))
        std = float(np.std(indices))

        if std == 0:
            return None

        z_score = (current.attention_index - mean) / std

        if z_score >= self.spike_threshold_std:
            severity = Severity.WARNING
            return DetectedAnomaly(
                alert_type=AlertType.ATTENTION_SPIKE,
                severity=severity,
                message=f"🎯 {title_name} attention index spiked! "
                f"AI={current.attention_index:.1f} (z={z_score:.1f}σ)",
                details={
                    "current_attention_index": round(current.attention_index, 2),
                    "historical_mean": round(mean, 2),
                    "historical_std": round(std, 3),
                    "z_score": round(z_score, 2),
                },
                detected_at=now,
                title_id=title_id,
                title_name=title_name,
            )
        return None

    def _detect_viral_breakout(
        self,
        existing_anomalies: list[DetectedAnomaly],
        current: MetricsWindow,
        title_id: str,
        title_name: str,
        now: datetime,
    ) -> DetectedAnomaly | None:
        """Detect viral breakout (multiple simultaneous anomalies)."""
        types = {a.alert_type for a in existing_anomalies}

        # Viral = spike + velocity surge, or 3+ anomalies
        is_viral = (AlertType.MENTION_SPIKE in types and AlertType.VELOCITY_SURGE in types) or len(
            existing_anomalies
        ) >= 3

        if is_viral:
            return DetectedAnomaly(
                alert_type=AlertType.VIRAL_BREAKOUT,
                severity=Severity.CRITICAL,
                message=f"🚀 {title_name} is going VIRAL! "
                f"Multiple anomalies detected simultaneously.",
                details={
                    "triggered_alerts": [a.alert_type.value for a in existing_anomalies],
                    "mention_count": current.mention_count,
                    "velocity": round(current.mention_velocity, 2)
                    if current.mention_velocity is not None
                    else None,
                    "attention_index": round(current.attention_index, 2)
                    if current.attention_index is not None
                    else None,
                },
                detected_at=now,
                title_id=title_id,
                title_name=title_name,
            )
        return None
