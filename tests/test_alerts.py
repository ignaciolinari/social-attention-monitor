"""Tests for the anomaly detection system."""

from datetime import UTC, datetime, timedelta

from sam.alerts.detector import (
    AlertType,
    AnomalyDetector,
    DetectedAnomaly,
    MetricsWindow,
    Severity,
)


def _make_window(
    mention_count: int = 10,
    mention_velocity: float = 1.0,
    velocity_change: float = 0.0,
    avg_sentiment: float = 0.0,
    sentiment_volatility: float = 0.1,
    attention_index: float = 50.0,
    hype_acceleration: float = 0.0,
    unique_authors: int = 5,
    hours_ago: int = 0,
) -> MetricsWindow:
    """Helper to create a MetricsWindow."""
    return MetricsWindow(
        mention_count=mention_count,
        mention_velocity=mention_velocity,
        velocity_change=velocity_change,
        avg_sentiment=avg_sentiment,
        sentiment_volatility=sentiment_volatility,
        attention_index=attention_index,
        hype_acceleration=hype_acceleration,
        unique_authors=unique_authors,
        snapshot_time=datetime.now(UTC) - timedelta(hours=hours_ago),
    )


class TestAnomalyDetector:
    """Tests for AnomalyDetector class."""

    def test_no_anomalies_with_stable_metrics(self) -> None:
        """Stable metrics should produce no anomalies."""
        detector = AnomalyDetector(min_history_points=3)

        # Create stable history
        history = [_make_window(mention_count=10, hours_ago=i) for i in range(5, 0, -1)]
        current = _make_window(mention_count=11)  # Slightly higher, but within normal

        anomalies = detector.detect_anomalies(
            current=current,
            history=history,
            title_id="test-id",
            title_name="Test Title",
        )

        # Should have no anomalies (or at most info-level)
        critical_or_warning = [a for a in anomalies if a.severity != Severity.INFO]
        assert len(critical_or_warning) == 0

    def test_detects_mention_spike(self) -> None:
        """Large increase in mentions should trigger spike alert."""
        detector = AnomalyDetector(spike_threshold_std=2.0, min_history_points=3)

        # Varied history with ~10 mentions average (needs variance for std)
        history = [
            _make_window(mention_count=8, hours_ago=5),
            _make_window(mention_count=10, hours_ago=4),
            _make_window(mention_count=12, hours_ago=3),
            _make_window(mention_count=9, hours_ago=2),
            _make_window(mention_count=11, hours_ago=1),
        ]
        # Current has 5x the mentions
        current = _make_window(mention_count=50)

        anomalies = detector.detect_anomalies(
            current=current,
            history=history,
            title_id="test-id",
            title_name="Test Title",
        )

        spike_alerts = [a for a in anomalies if a.alert_type == AlertType.MENTION_SPIKE]
        assert len(spike_alerts) == 1
        assert spike_alerts[0].severity in (Severity.WARNING, Severity.CRITICAL)
        assert "spike" in spike_alerts[0].message.lower()

    def test_detects_spike_with_zero_std(self) -> None:
        """Flat history should still flag a large jump."""
        detector = AnomalyDetector(spike_threshold_std=2.0, min_history_points=3)

        # Flat history -> std = 0
        history = [_make_window(mention_count=10, hours_ago=i) for i in range(5, 0, -1)]
        current = _make_window(mention_count=25)

        anomalies = detector.detect_anomalies(
            current=current,
            history=history,
            title_id="test-id",
            title_name="Test Title",
        )

        spike_alerts = [a for a in anomalies if a.alert_type == AlertType.MENTION_SPIKE]
        assert len(spike_alerts) == 1

    def test_detects_sentiment_shift(self) -> None:
        """Large sentiment change should trigger sentiment shift alert."""
        detector = AnomalyDetector(sentiment_shift_threshold=0.3, min_history_points=3)

        # Positive sentiment history
        history = [_make_window(avg_sentiment=0.5, hours_ago=i) for i in range(5, 0, -1)]
        # Sudden negative shift
        current = _make_window(avg_sentiment=-0.2)

        anomalies = detector.detect_anomalies(
            current=current,
            history=history,
            title_id="test-id",
            title_name="Test Title",
        )

        sentiment_alerts = [a for a in anomalies if a.alert_type == AlertType.SENTIMENT_SHIFT]
        assert len(sentiment_alerts) == 1
        assert "negative" in sentiment_alerts[0].details.get("direction", "")

    def test_detects_velocity_surge(self) -> None:
        """High velocity increase should trigger velocity surge alert."""
        detector = AnomalyDetector(velocity_surge_threshold_std=2.0, min_history_points=3)

        # Varied low velocity history
        history = [
            _make_window(mention_velocity=0.8, hours_ago=5),
            _make_window(mention_velocity=1.2, hours_ago=4),
            _make_window(mention_velocity=1.0, hours_ago=3),
            _make_window(mention_velocity=0.9, hours_ago=2),
            _make_window(mention_velocity=1.1, hours_ago=1),
        ]
        # Sudden high velocity
        current = _make_window(mention_velocity=10.0)

        anomalies = detector.detect_anomalies(
            current=current,
            history=history,
            title_id="test-id",
            title_name="Test Title",
        )

        velocity_alerts = [a for a in anomalies if a.alert_type == AlertType.VELOCITY_SURGE]
        assert len(velocity_alerts) == 1

    def test_detects_viral_breakout(self) -> None:
        """Multiple simultaneous anomalies should trigger viral breakout."""
        detector = AnomalyDetector(
            spike_threshold_std=2.0,
            velocity_surge_threshold_std=2.0,
            min_history_points=3,
        )

        # Varied low activity history
        history = [
            _make_window(mention_count=8, mention_velocity=0.8, hours_ago=5),
            _make_window(mention_count=12, mention_velocity=1.2, hours_ago=4),
            _make_window(mention_count=10, mention_velocity=1.0, hours_ago=3),
            _make_window(mention_count=9, mention_velocity=0.9, hours_ago=2),
            _make_window(mention_count=11, mention_velocity=1.1, hours_ago=1),
        ]
        # Massive spike in both metrics
        current = _make_window(mention_count=100, mention_velocity=20.0)

        anomalies = detector.detect_anomalies(
            current=current,
            history=history,
            title_id="test-id",
            title_name="Test Title",
        )

        viral_alerts = [a for a in anomalies if a.alert_type == AlertType.VIRAL_BREAKOUT]
        assert len(viral_alerts) == 1
        assert viral_alerts[0].severity == Severity.CRITICAL

    def test_insufficient_history_returns_empty(self) -> None:
        """Should return no anomalies when history is insufficient."""
        detector = AnomalyDetector(min_history_points=5)

        # Only 2 history points
        history = [_make_window(hours_ago=i) for i in range(2, 0, -1)]
        current = _make_window(mention_count=100)

        anomalies = detector.detect_anomalies(
            current=current,
            history=history,
            title_id="test-id",
            title_name="Test Title",
        )

        assert len(anomalies) == 0

    def test_anomaly_details_contain_z_score(self) -> None:
        """Detected anomalies should include z-score in details."""
        detector = AnomalyDetector(spike_threshold_std=2.0, min_history_points=3)

        # Varied history for proper std calculation
        history = [
            _make_window(mention_count=8, hours_ago=5),
            _make_window(mention_count=12, hours_ago=4),
            _make_window(mention_count=10, hours_ago=3),
            _make_window(mention_count=9, hours_ago=2),
            _make_window(mention_count=11, hours_ago=1),
        ]
        current = _make_window(mention_count=50)

        anomalies = detector.detect_anomalies(
            current=current,
            history=history,
            title_id="test-id",
            title_name="Test Title",
        )

        spike_alerts = [a for a in anomalies if a.alert_type == AlertType.MENTION_SPIKE]
        assert len(spike_alerts) == 1
        assert "z_score" in spike_alerts[0].details
        assert spike_alerts[0].details["z_score"] > 2.0


class TestDetectedAnomaly:
    """Tests for DetectedAnomaly dataclass."""

    def test_anomaly_has_required_fields(self) -> None:
        """DetectedAnomaly should have all required fields."""
        anomaly = DetectedAnomaly(
            alert_type=AlertType.MENTION_SPIKE,
            severity=Severity.WARNING,
            message="Test message",
            details={"key": "value"},
            detected_at=datetime.now(UTC),
            title_id="test-id",
            title_name="Test Title",
        )

        assert anomaly.alert_type == AlertType.MENTION_SPIKE
        assert anomaly.severity == Severity.WARNING
        assert anomaly.message == "Test message"
        assert anomaly.details == {"key": "value"}
        assert anomaly.title_id == "test-id"
        assert anomaly.title_name == "Test Title"
