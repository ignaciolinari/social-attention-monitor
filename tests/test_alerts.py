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
    sentiment_divergence: dict[str, float] | None = None,
    author_diversity_score: float | None = None,
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
        sentiment_divergence=sentiment_divergence,
        author_diversity_score=author_diversity_score,
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


class TestSentimentDivergence:
    """Tests for _detect_sentiment_divergence."""

    def test_no_divergence_when_field_is_none(self) -> None:
        detector = AnomalyDetector(min_history_points=3)
        current = _make_window(sentiment_divergence=None)
        result = detector._detect_sentiment_divergence(current, "t1", "Title", datetime.now(UTC))
        assert result is None

    def test_no_divergence_below_threshold(self) -> None:
        detector = AnomalyDetector(min_history_points=3)
        current = _make_window(sentiment_divergence={"reddit_vs_youtube": 0.2})
        result = detector._detect_sentiment_divergence(current, "t1", "Title", datetime.now(UTC))
        assert result is None

    def test_info_divergence_at_04(self) -> None:
        detector = AnomalyDetector(min_history_points=3)
        current = _make_window(sentiment_divergence={"reddit_vs_youtube": 0.45})
        result = detector._detect_sentiment_divergence(current, "t1", "Title", datetime.now(UTC))
        assert result is not None
        assert result.alert_type == AlertType.SENTIMENT_DIVERGENCE
        assert result.severity == Severity.INFO

    def test_warning_divergence_at_06(self) -> None:
        detector = AnomalyDetector(min_history_points=3)
        current = _make_window(sentiment_divergence={"reddit_vs_youtube": 0.65})
        result = detector._detect_sentiment_divergence(current, "t1", "Title", datetime.now(UTC))
        assert result is not None
        assert result.severity == Severity.WARNING

    def test_picks_max_pair(self) -> None:
        detector = AnomalyDetector(min_history_points=3)
        div = {"reddit_vs_youtube": 0.3, "reddit_vs_bluesky": 0.5}
        current = _make_window(sentiment_divergence=div)
        result = detector._detect_sentiment_divergence(current, "t1", "Title", datetime.now(UTC))
        assert result is not None
        assert result.details["pair"] == "reddit_vs_bluesky"

    def test_ignores_keys_without_vs(self) -> None:
        detector = AnomalyDetector(min_history_points=3)
        # Keys without _vs_ should be ignored
        current = _make_window(sentiment_divergence={"overall": 0.8})
        result = detector._detect_sentiment_divergence(current, "t1", "Title", datetime.now(UTC))
        assert result is None


class TestDiversityDrop:
    """Tests for _detect_diversity_drop."""

    def test_no_alert_when_score_is_none(self) -> None:
        detector = AnomalyDetector(min_history_points=3)
        current = _make_window(author_diversity_score=None)
        result = detector._detect_diversity_drop(current, [], "t1", "Title", datetime.now(UTC))
        assert result is None

    def test_no_alert_with_insufficient_history(self) -> None:
        detector = AnomalyDetector(min_history_points=3)
        current = _make_window(author_diversity_score=0.5)
        history = [_make_window(author_diversity_score=0.1, hours_ago=i) for i in range(2)]
        result = detector._detect_diversity_drop(current, history, "t1", "Title", datetime.now(UTC))
        assert result is None

    def test_alert_on_significant_hhi_increase(self) -> None:
        detector = AnomalyDetector(min_history_points=3)
        # Historical HHI is low (~0.1 mean), current is much higher (0.25 > 0.1*1.5 and > 0.15)
        history = [_make_window(author_diversity_score=0.1, hours_ago=i) for i in range(5)]
        current = _make_window(author_diversity_score=0.25)
        result = detector._detect_diversity_drop(current, history, "t1", "Title", datetime.now(UTC))
        assert result is not None
        assert result.alert_type == AlertType.DIVERSITY_DROP
        assert result.severity == Severity.INFO

    def test_no_alert_when_within_normal_range(self) -> None:
        detector = AnomalyDetector(min_history_points=3)
        history = [_make_window(author_diversity_score=0.1, hours_ago=i) for i in range(5)]
        current = _make_window(author_diversity_score=0.12)  # Slightly above, not 1.5x
        result = detector._detect_diversity_drop(current, history, "t1", "Title", datetime.now(UTC))
        assert result is None

    def test_no_alert_when_below_015_threshold(self) -> None:
        detector = AnomalyDetector(min_history_points=3)
        # Even if 1.5x mean, if absolute < 0.15, no alert
        history = [_make_window(author_diversity_score=0.05, hours_ago=i) for i in range(5)]
        current = _make_window(author_diversity_score=0.12)  # 2.4x but < 0.15
        result = detector._detect_diversity_drop(current, history, "t1", "Title", datetime.now(UTC))
        assert result is None

    def test_history_none_scores_filtered(self) -> None:
        detector = AnomalyDetector(min_history_points=3)
        history = [
            _make_window(author_diversity_score=None, hours_ago=5),
            _make_window(author_diversity_score=0.1, hours_ago=4),
            _make_window(author_diversity_score=0.1, hours_ago=3),
            _make_window(author_diversity_score=0.1, hours_ago=2),
        ]
        current = _make_window(author_diversity_score=0.25)
        result = detector._detect_diversity_drop(current, history, "t1", "Title", datetime.now(UTC))
        assert result is not None
