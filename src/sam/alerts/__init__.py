"""Alerts package - Anomaly detection and notifications."""

from sam.alerts.detector import (
    AlertType,
    AnomalyDetector,
    DetectedAnomaly,
    MetricsWindow,
    Severity,
)
from sam.alerts.manager import (
    AlertManager,
    acknowledge_alert,
    count_alerts,
    count_unacknowledged_alerts,
    get_alert_counts_by_severity,
    get_recent_alerts,
    get_unacknowledged_count,
)

__all__ = [
    "AlertType",
    "AnomalyDetector",
    "DetectedAnomaly",
    "MetricsWindow",
    "Severity",
    "AlertManager",
    "acknowledge_alert",
    "count_alerts",
    "count_unacknowledged_alerts",
    "get_alert_counts_by_severity",
    "get_recent_alerts",
    "get_unacknowledged_count",
]
