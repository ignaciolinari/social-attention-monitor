"""
Database Models

SQLAlchemy models for the Social Attention Monitor.
Designed for PostgreSQL with TimescaleDB extension for time-series optimization.
"""

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""

    type_annotation_map = {
        dict[str, Any]: JSONB,
    }


class MediaType(str, Enum):
    """Type of media content."""

    MOVIE = "movie"
    TV = "tv"


class Platform(str, Enum):
    """Social media platform."""

    REDDIT = "reddit"
    YOUTUBE = "youtube"


class Title(Base):
    """
    Media title (movie or TV show).

    Represents a trackable piece of content with metadata from TMDB.
    """

    __tablename__ = "titles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tmdb_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    original_title: Mapped[str | None] = mapped_column(String(500))
    media_type: Mapped[str] = mapped_column(String(10), nullable=False)  # movie, tv
    release_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    overview: Mapped[str | None] = mapped_column(Text)
    poster_path: Mapped[str | None] = mapped_column(String(255))
    popularity: Mapped[float | None] = mapped_column(Float)
    vote_average: Mapped[float | None] = mapped_column(Float)
    genres: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extra_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Tracking
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    is_active: Mapped[bool] = mapped_column(default=True)

    # Relationships
    mentions: Mapped[list["Mention"]] = relationship(back_populates="title")

    def __repr__(self) -> str:
        return f"<Title(id={self.id}, title='{self.title}', type={self.media_type})>"


class Mention(Base):
    """
    A social media mention of a title.

    Captures posts, comments, and videos that reference a tracked title.
    This is the core time-series data - designed for TimescaleDB hypertable.
    """

    __tablename__ = "mentions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("titles.id"), index=True
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)  # reddit, youtube
    source_id: Mapped[str] = mapped_column(
        String(100), nullable=False
    )  # Reddit post ID, YouTube video ID
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)  # post, comment, video

    # Content
    content: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(100))
    url: Mapped[str | None] = mapped_column(String(500))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    # Platform-specific metrics (stored as JSONB for flexibility)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Reddit: {score, upvote_ratio, num_comments, subreddit}
    # YouTube: {view_count, like_count, comment_count, channel_id}

    # Sentiment analysis results
    sentiment: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # {compound, positive, negative, neutral, model_version}

    # Relationships
    title: Mapped["Title"] = relationship(back_populates="mentions")

    __table_args__ = (
        UniqueConstraint("platform", "source_id", "title_id", name="uq_platform_source_title"),
        Index("ix_mentions_title_platform", "title_id", "platform"),
        Index("ix_mentions_created_at_title", "created_at", "title_id"),
    )

    def __repr__(self) -> str:
        return f"<Mention(id={self.id}, platform={self.platform}, source_id={self.source_id})>"


class MetricsSnapshot(Base):
    """
    Aggregated metrics snapshot for a title at a point in time.

    Pre-computed metrics for fast dashboard queries.
    Updated periodically by the processing pipeline.
    """

    __tablename__ = "metrics_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("titles.id"), index=True
    )
    snapshot_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    window_hours: Mapped[int] = mapped_column(Integer, default=1)  # Aggregation window size

    # Mention counts
    mention_count: Mapped[int] = mapped_column(Integer, default=0)
    unique_authors: Mapped[int] = mapped_column(Integer, default=0)

    # Platform breakdown
    reddit_mentions: Mapped[int] = mapped_column(Integer, default=0)
    youtube_mentions: Mapped[int] = mapped_column(Integer, default=0)

    # Velocity metrics
    mention_velocity: Mapped[float | None] = mapped_column(Float)  # mentions per hour
    velocity_change: Mapped[float | None] = mapped_column(Float)  # acceleration

    # Sentiment metrics
    avg_sentiment: Mapped[float | None] = mapped_column(Float)
    sentiment_volatility: Mapped[float | None] = mapped_column(Float)
    positive_ratio: Mapped[float | None] = mapped_column(Float)

    # Composite scores
    attention_index: Mapped[float | None] = mapped_column(Float)
    hype_acceleration: Mapped[float | None] = mapped_column(Float)

    # Full metrics blob for flexibility
    raw_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint("title_id", "snapshot_time", "window_hours", name="uq_title_snapshot"),
        Index("ix_snapshots_time_title", "snapshot_time", "title_id"),
    )

    def __repr__(self) -> str:
        return f"<MetricsSnapshot(title_id={self.title_id}, time={self.snapshot_time})>"


class Alert(Base):
    """
    Alert generated by anomaly detection.

    Captures significant events like spikes in attention or sentiment shifts.
    """

    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("titles.id"), index=True
    )
    alert_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # spike, sentiment_shift, velocity_surge
    severity: Mapped[str] = mapped_column(String(20), default="info")  # info, warning, critical

    # Alert details
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<Alert(id={self.id}, type={self.alert_type}, title_id={self.title_id})>"
