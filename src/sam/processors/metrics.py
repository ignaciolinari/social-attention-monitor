"""
Metrics Calculator

Computes engagement metrics and composite scores for titles.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np


@dataclass
class EngagementMetrics:
    """Computed engagement metrics for a title."""

    # Basic counts
    mention_count: int
    unique_authors: int
    total_engagement: int  # likes + comments + shares

    # Velocity
    mention_velocity: float  # mentions per hour
    velocity_change: float  # acceleration (change in velocity)

    # Sentiment
    avg_sentiment: float
    sentiment_volatility: float
    positive_ratio: float
    negative_ratio: float

    # Composite scores
    attention_index: float
    hype_acceleration: float

    # Metadata
    window_start: datetime
    window_end: datetime
    platform_breakdown: dict[str, int]


class MetricsCalculator:
    """
    Calculates engagement metrics and composite scores.

    Provides methods for computing:
    - Basic engagement metrics (counts, rates)
    - Velocity and acceleration metrics
    - Sentiment aggregations
    - Composite scores (Attention Index, Hype Acceleration)
    """

    def __init__(self) -> None:
        """Initialize the metrics calculator."""
        self._historical_stats: dict[str, dict[str, float]] = {}

    def calculate(
        self,
        mentions: list[dict[str, Any]],
        window_hours: int = 24,
        previous_metrics: "EngagementMetrics | None" = None,
    ) -> EngagementMetrics:
        """
        Calculate engagement metrics for a set of mentions.

        Args:
            mentions: List of mention dictionaries with sentiment scores
            window_hours: Time window for calculations
            previous_metrics: Previous period metrics for velocity calculations

        Returns:
            EngagementMetrics with all computed values
        """
        now = datetime.now(UTC)
        window_start = now - timedelta(hours=window_hours)

        # Filter to window
        in_window = []
        for mention in mentions:
            created_at = mention.get("created_at", now)
            if isinstance(created_at, datetime) and created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            if created_at >= window_start:
                in_window.append(mention)

        # Basic counts
        mention_count = len(in_window)
        unique_authors = len({m.get("author") for m in in_window if m.get("author")})

        # Platform breakdown
        platform_breakdown: dict[str, int] = {}
        for m in in_window:
            platform = m.get("platform", "unknown")
            platform_breakdown[platform] = platform_breakdown.get(platform, 0) + 1

        # Total engagement
        total_engagement = sum(
            m.get("metrics", {}).get("score", 0)
            + m.get("metrics", {}).get("num_comments", 0)
            + m.get("metrics", {}).get("like_count", 0)
            + m.get("metrics", {}).get("comment_count", 0)
            for m in in_window
        )

        # Velocity
        mention_velocity = mention_count / window_hours if window_hours > 0 else 0

        # Velocity change (acceleration)
        velocity_change = 0.0
        if previous_metrics:
            velocity_change = mention_velocity - previous_metrics.mention_velocity

        # Sentiment metrics
        sentiments = [
            m.get("sentiment", {}).get("compound", 0.0) for m in in_window if m.get("sentiment")
        ]

        if sentiments:
            avg_sentiment = float(np.mean(sentiments))
            sentiment_volatility = float(np.std(sentiments))
            positive_ratio = len([s for s in sentiments if s > 0.05]) / len(sentiments)
            negative_ratio = len([s for s in sentiments if s < -0.05]) / len(sentiments)
        else:
            avg_sentiment = 0.0
            sentiment_volatility = 0.0
            positive_ratio = 0.0
            negative_ratio = 0.0

        # Composite scores
        attention_index = self._calculate_attention_index(
            mention_count, mention_velocity, unique_authors, avg_sentiment
        )
        hype_acceleration = self._calculate_hype_acceleration(
            velocity_change, mention_count, previous_metrics
        )

        return EngagementMetrics(
            mention_count=mention_count,
            unique_authors=unique_authors,
            total_engagement=total_engagement,
            mention_velocity=mention_velocity,
            velocity_change=velocity_change,
            avg_sentiment=avg_sentiment,
            sentiment_volatility=sentiment_volatility,
            positive_ratio=positive_ratio,
            negative_ratio=negative_ratio,
            attention_index=attention_index,
            hype_acceleration=hype_acceleration,
            window_start=window_start,
            window_end=now,
            platform_breakdown=platform_breakdown,
        )

    def _calculate_attention_index(
        self,
        mentions: int,
        velocity: float,
        unique_users: int,
        sentiment_momentum: float,
    ) -> float:
        """
        Calculate the Attention Index composite score.

        AI = z(mentions) * 0.25 + z(velocity) * 0.30 + z(unique_users) * 0.25 + z(sentiment) * 0.20

        For now, uses simple normalization. In production, would use historical baselines.
        """
        # Simple normalization (scale to roughly 0-100)
        # In production, replace with z-score against historical data
        normalized_mentions = min(100, mentions / 10)
        normalized_velocity = min(100, velocity * 10)
        normalized_users = min(100, unique_users / 5)
        normalized_sentiment = (sentiment_momentum + 1) * 50  # -1 to 1 -> 0 to 100

        attention_index = (
            normalized_mentions * 0.25
            + normalized_velocity * 0.30
            + normalized_users * 0.25
            + normalized_sentiment * 0.20
        )

        return round(attention_index, 2)

    def _calculate_hype_acceleration(
        self,
        velocity_change: float,
        current_mentions: int,
        previous: "EngagementMetrics | None",
    ) -> float:
        """
        Calculate Hype Acceleration.

        HA = d²/dt²(mentions) - second derivative of mentions over time.
        Indicates whether hype is building or declining.
        """
        if not previous:
            return 0.0

        # Simple acceleration calculation
        # Positive = hype building, Negative = hype declining
        if previous.mention_count > 0:
            growth_rate = (current_mentions - previous.mention_count) / previous.mention_count
        else:
            growth_rate = current_mentions / 10  # Normalize when no previous

        # Weight by velocity change
        acceleration = growth_rate * (1 + velocity_change / 10)

        return round(acceleration * 100, 2)  # Scale for readability

    def calculate_platform_normalized(
        self,
        metrics: dict[str, EngagementMetrics],
    ) -> dict[str, float]:
        """
        Calculate platform-normalized scores for comparison.

        Accounts for different engagement scales across platforms.
        """
        baselines = {
            "reddit": {"mentions": 100, "engagement": 5000},
            "youtube": {"mentions": 20, "engagement": 100000},
        }

        normalized = {}
        for platform, m in metrics.items():
            if platform in baselines:
                baseline = baselines[platform]
                norm_mentions = m.mention_count / baseline["mentions"]
                norm_engagement = m.total_engagement / baseline["engagement"]
                normalized[platform] = round((norm_mentions + norm_engagement) / 2 * 100, 2)
            else:
                normalized[platform] = m.attention_index

        return normalized

    def detect_anomaly(
        self,
        current: EngagementMetrics,
        historical: list[EngagementMetrics],
        threshold_std: float = 3.0,
    ) -> dict[str, bool]:
        """
        Detect anomalies in current metrics compared to historical baseline.

        Args:
            current: Current period metrics
            historical: List of historical metrics for comparison
            threshold_std: Number of standard deviations for anomaly threshold

        Returns:
            Dict of anomaly flags for each metric
        """
        if len(historical) < 3:
            return {}

        anomalies = {}

        # Check mention count
        hist_mentions = [h.mention_count for h in historical]
        mean_mentions = float(np.mean(hist_mentions))
        std_mentions = float(np.std(hist_mentions)) or 1

        if abs(current.mention_count - mean_mentions) > threshold_std * std_mentions:
            anomalies["mention_spike"] = current.mention_count > mean_mentions

        # Check sentiment shift
        hist_sentiment = [h.avg_sentiment for h in historical]
        mean_sentiment = float(np.mean(hist_sentiment))
        std_sentiment = float(np.std(hist_sentiment)) or 0.1

        if abs(current.avg_sentiment - mean_sentiment) > threshold_std * std_sentiment:
            anomalies["sentiment_shift"] = True

        # Check velocity surge
        hist_velocity = [h.mention_velocity for h in historical]
        mean_velocity = float(np.mean(hist_velocity))
        std_velocity = float(np.std(hist_velocity)) or 1

        if current.mention_velocity > mean_velocity + threshold_std * std_velocity:
            anomalies["velocity_surge"] = True

        return anomalies


# Convenience function
_calculator: MetricsCalculator | None = None


def get_calculator() -> MetricsCalculator:
    """Get or create the default metrics calculator."""
    global _calculator
    if _calculator is None:
        _calculator = MetricsCalculator()
    return _calculator
