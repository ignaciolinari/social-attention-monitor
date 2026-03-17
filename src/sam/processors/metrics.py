"""
Metrics Calculator

Computes engagement metrics and composite scores for titles.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypedDict

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

    # New alpha metrics (with defaults for backward compat)
    engagement_weighted_sentiment: float = 0.0
    sentiment_divergence: dict[str, float] | None = None
    sentiment_momentum: float = 0.0
    audience_fatigue_index: float = 0.0
    viral_coefficient: float = 0.0
    author_diversity_score: float = 0.0
    repeat_author_ratio: float = 0.0

    # Creator vs audience sentiment
    creator_sentiment: float | None = None
    audience_sentiment: float | None = None


class MetricsPayload(TypedDict, total=False):
    """Platform engagement counters for a single mention.

    All fields are optional because available metrics vary by platform:
    Reddit supplies ``score`` and ``num_comments``; YouTube supplies
    ``like_count``, ``comment_count``, and ``view_count``; Bluesky supplies
    ``likes``, ``reposts``, and ``replies``.
    """

    score: int
    num_comments: int
    like_count: int
    comment_count: int
    view_count: int
    likes: int
    reposts: int
    replies: int


class SentimentPayload(TypedDict, total=False):
    """Minimal sentiment data read by :class:`MetricsCalculator`.

    The full sentiment JSONB column can contain richer fields (label, model,
    extra, sarcasm_confidence, etc.) but the calculator only reads ``compound``.
    """

    compound: float


class MentionData(TypedDict, total=False):
    """Input record type for :class:`MetricsCalculator`.

    Mirrors the columns projected from the ``Mention`` ORM model. All fields
    are optional (``total=False``) because different callers construct these
    dicts from different query projections.
    """

    created_at: datetime
    author: str
    platform: str
    source_type: str
    metrics: MetricsPayload
    sentiment: SentimentPayload


class MetricsCalculator:
    """
    Calculates engagement metrics and composite scores.

    Provides methods for computing:
    - Basic engagement metrics (counts, rates)
    - Velocity and acceleration metrics
    - Sentiment aggregations
    - Composite scores (Attention Index, Hype Acceleration)
    - Alpha metrics (engagement-weighted sentiment, divergence, momentum, etc.)
    """

    def __init__(self) -> None:
        """Initialize the metrics calculator."""

    def calculate(
        self,
        mentions: list["MentionData"],
        window_hours: int = 24,
        previous_metrics: "EngagementMetrics | None" = None,
        window_end: datetime | None = None,
    ) -> EngagementMetrics:
        """
        Calculate engagement metrics for a set of mentions.

        Args:
            mentions: List of mention dictionaries with sentiment scores
            window_hours: Time window for calculations
            previous_metrics: Previous period metrics for velocity calculations
            window_end: Upper boundary of the analysis window. Defaults to
                ``datetime.now(UTC)`` when not provided. Pass an explicit value
                for reproducible back-fills or tests.

        Returns:
            EngagementMetrics with all computed values
        """
        if window_hours <= 0:
            raise ValueError("window_hours must be positive")

        window_end = window_end or datetime.now(UTC)
        window_start = window_end - timedelta(hours=window_hours)

        # Filter to window
        in_window = []
        for mention in mentions:
            created_at = mention.get("created_at", window_end)
            if not isinstance(created_at, datetime):
                created_at = window_end
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            if window_start <= created_at < window_end:
                in_window.append(mention)

        # Basic counts
        mention_count = len(in_window)
        unique_authors = len({m.get("author") for m in in_window if m.get("author")})

        # Platform breakdown
        platform_breakdown: dict[str, int] = {}
        for m in in_window:
            platform = m.get("platform", "unknown")
            platform_breakdown[platform] = platform_breakdown.get(platform, 0) + 1

        # Total engagement — platform-aware summation.
        # Reddit:  score + num_comments
        # YouTube: view_count + like_count + comment_count
        total_engagement = 0
        for m in in_window:
            total_engagement += self._get_engagement(m)

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

        # ── New alpha metrics ──────────────────────────────────────
        engagement_weighted_sentiment = self._calc_engagement_weighted_sentiment(in_window)
        sentiment_divergence = self._calc_sentiment_divergence(in_window)
        sentiment_momentum = self._calc_sentiment_momentum(avg_sentiment, previous_metrics)
        audience_fatigue_index = self._calc_audience_fatigue(
            in_window, mention_count, avg_sentiment, previous_metrics
        )
        viral_coefficient = self._calc_viral_coefficient(in_window)
        author_diversity_score, repeat_author_ratio = self._calc_author_diversity(in_window)
        creator_sentiment, audience_sentiment = self._calc_creator_audience_sentiment(in_window)

        # Composite scores (ratio-normalised against previous window when available)
        attention_index = self._calculate_attention_index(
            mention_count, mention_velocity, unique_authors, avg_sentiment, previous_metrics
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
            engagement_weighted_sentiment=engagement_weighted_sentiment,
            sentiment_divergence=sentiment_divergence,
            sentiment_momentum=sentiment_momentum,
            audience_fatigue_index=audience_fatigue_index,
            viral_coefficient=viral_coefficient,
            author_diversity_score=author_diversity_score,
            repeat_author_ratio=repeat_author_ratio,
            creator_sentiment=creator_sentiment,
            audience_sentiment=audience_sentiment,
            window_start=window_start,
            window_end=window_end,
            platform_breakdown=platform_breakdown,
        )

    # ── Engagement helpers ─────────────────────────────────────────

    @staticmethod
    def _get_engagement(mention: "MentionData") -> int:
        metrics = mention.get("metrics", {})
        platform = mention.get("platform", "unknown")
        if platform == "reddit":
            return metrics.get("score", 0) + metrics.get("num_comments", 0)
        elif platform == "youtube":
            return (
                metrics.get("view_count", 0)
                + metrics.get("like_count", 0)
                + metrics.get("comment_count", 0)
            )
        elif platform == "bluesky":
            return metrics.get("likes", 0) + metrics.get("reposts", 0) + metrics.get("replies", 0)
        else:
            return (
                metrics.get("score", 0)
                + metrics.get("num_comments", 0)
                + metrics.get("like_count", 0)
                + metrics.get("comment_count", 0)
                + metrics.get("view_count", 0)
            )

    # ── New alpha metric methods ───────────────────────────────────

    def _calc_engagement_weighted_sentiment(self, mentions: list["MentionData"]) -> float:
        """Compute sentiment weighted by engagement (high-engagement opinions matter more)."""
        total_weight = 0.0
        weighted_sum = 0.0

        for m in mentions:
            sentiment = m.get("sentiment", {})
            compound = sentiment.get("compound")
            if compound is None:
                continue
            engagement = max(1, self._get_engagement(m))
            weighted_sum += compound * engagement
            total_weight += engagement

        if total_weight == 0:
            return 0.0
        return round(weighted_sum / total_weight, 6)

    def _calc_sentiment_divergence(self, mentions: list["MentionData"]) -> dict[str, float]:
        """Compute pairwise absolute sentiment differences between platforms."""
        platform_sentiments: dict[str, list[float]] = {}
        for m in mentions:
            platform = m.get("platform", "unknown")
            compound = m.get("sentiment", {}).get("compound")
            if compound is not None:
                platform_sentiments.setdefault(platform, []).append(compound)

        platform_avgs = {
            p: float(np.mean(scores)) for p, scores in platform_sentiments.items() if scores
        }

        divergence: dict[str, float] = {}
        platforms = sorted(platform_avgs.keys())
        for i, p1 in enumerate(platforms):
            for p2 in platforms[i + 1 :]:
                key = f"{p1}_vs_{p2}"
                divergence[key] = round(abs(platform_avgs[p1] - platform_avgs[p2]), 4)

        # Also store per-platform averages for reference
        for p, avg in platform_avgs.items():
            divergence[f"{p}_avg"] = round(avg, 4)

        return divergence

    def _calc_sentiment_momentum(
        self, current_sentiment: float, previous: "EngagementMetrics | None"
    ) -> float:
        """First derivative of sentiment: rate of change between snapshots."""
        if not previous:
            return 0.0
        return round(current_sentiment - previous.avg_sentiment, 4)

    def _calc_audience_fatigue(
        self,
        mentions: list["MentionData"],
        mention_count: int,
        avg_sentiment: float,
        previous: "EngagementMetrics | None",
    ) -> float:
        """Composite fatigue index: declining engagement-per-mention + declining sentiment.

        Range: 0 (no fatigue) to 1 (severe fatigue).
        """
        if not previous or previous.mention_count == 0:
            return 0.0

        fatigue = 0.0

        # 1. Engagement per mention declining
        current_epm = 0.0
        if mention_count > 0:
            total_eng = sum(max(0, self._get_engagement(m)) for m in mentions)
            current_epm = total_eng / mention_count
        prev_epm = (
            previous.total_engagement / previous.mention_count if previous.mention_count > 0 else 0
        )
        if prev_epm > 0 and current_epm < prev_epm:
            fatigue += min(0.4, (prev_epm - current_epm) / prev_epm)

        # 2. Sentiment declining
        if avg_sentiment < previous.avg_sentiment:
            decline = previous.avg_sentiment - avg_sentiment
            fatigue += min(0.3, decline)

        # 3. Volume sustained or growing (but quality declining)
        if mention_count >= previous.mention_count and fatigue > 0:
            fatigue += 0.1

        return round(min(1.0, fatigue), 4)

    def _calc_viral_coefficient(self, mentions: list["MentionData"]) -> float:
        """Ratio of reposts/shares to total posts. Higher = more organic amplification."""
        total_shares = 0
        total_posts = len(mentions)
        if total_posts == 0:
            return 0.0

        for m in mentions:
            metrics = m.get("metrics", {})
            total_shares += metrics.get("reposts", 0)

        return round(total_shares / total_posts, 4)

    def _calc_author_diversity(self, mentions: list["MentionData"]) -> tuple[float, float]:
        """Compute author diversity using HHI (Herfindahl-Hirschman Index).

        Returns:
            (hhi_score, repeat_author_ratio).
            HHI: 0 = perfectly diverse, 1 = single author.
            repeat_author_ratio: fraction of authors appearing 2+ times.
        """
        authors = [m.get("author") for m in mentions if m.get("author")]
        if not authors:
            return (0.0, 0.0)

        counter = Counter(authors)
        total = len(authors)

        # HHI: sum of squared market shares
        hhi = sum((count / total) ** 2 for count in counter.values())

        # Repeat ratio
        repeat_count = sum(1 for count in counter.values() if count > 1)
        repeat_ratio = repeat_count / len(counter) if counter else 0.0

        return (round(hhi, 4), round(repeat_ratio, 4))

    def _calc_creator_audience_sentiment(
        self, mentions: list["MentionData"]
    ) -> tuple[float | None, float | None]:
        """Split sentiment by source_type: creator (video/post) vs audience (comment)."""
        creator_scores: list[float] = []
        audience_scores: list[float] = []

        for m in mentions:
            compound = m.get("sentiment", {}).get("compound")
            if compound is None:
                continue
            source_type = m.get("source_type", "post")
            if source_type == "comment":
                audience_scores.append(compound)
            else:
                creator_scores.append(compound)

        creator_avg = round(float(np.mean(creator_scores)), 4) if creator_scores else None
        audience_avg = round(float(np.mean(audience_scores)), 4) if audience_scores else None
        return creator_avg, audience_avg

    # ── Composite scores ────────────────────────────────────────────

    def _calculate_attention_index(
        self,
        mentions: int,
        velocity: float,
        unique_users: int,
        avg_sentiment: float,
        previous: "EngagementMetrics | None" = None,
    ) -> float:
        """
        Calculate the Attention Index composite score.

        AI = z(mentions) * 0.25 + z(velocity) * 0.30 + z(unique_users) * 0.25 + z(sentiment) * 0.20

        Uses z-score normalization when previous metrics are available,
        falling back to simple normalization otherwise.
        """
        if previous and previous.mention_count > 0:
            # Z-score-ish normalization against previous snapshot
            prev_mentions = previous.mention_count or 1
            prev_velocity = previous.mention_velocity or 0.1
            prev_users = previous.unique_authors or 1

            normalized_mentions = min(100, (mentions / prev_mentions) * 50)
            normalized_velocity = min(100, (velocity / max(prev_velocity, 0.01)) * 50)
            normalized_users = min(100, (unique_users / prev_users) * 50)
        else:
            # Simple fallback normalization (scale to roughly 0-100)
            normalized_mentions = min(100, mentions / 10)
            normalized_velocity = min(100, velocity * 10)
            normalized_users = min(100, unique_users / 5)

        normalized_sentiment = (avg_sentiment + 1) * 50  # -1 to 1 -> 0 to 100

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


# Convenience function
_calculator: MetricsCalculator | None = None


def get_calculator() -> MetricsCalculator:
    """Get or create the default metrics calculator."""
    global _calculator
    if _calculator is None:
        _calculator = MetricsCalculator()
    return _calculator
