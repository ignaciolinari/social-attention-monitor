"""Database repository helpers.

Small, focused helpers to persist Titles and Mentions.
Keeps the scheduler runner simple and testable.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sam.collectors.base import CollectedPost
from sam.collectors.tmdb import TMDBTitle
from sam.storage.models import Lease, Mention, MetricsSnapshot, PipelineRun, Title


async def upsert_title(session: AsyncSession, tmdb_title: TMDBTitle) -> Title:
    """Insert/update a Title row and return the persisted object."""
    values: dict[str, Any] = {
        "tmdb_id": tmdb_title.tmdb_id,
        "title": tmdb_title.title,
        "original_title": tmdb_title.original_title,
        "media_type": tmdb_title.media_type,
        "release_date": tmdb_title.release_date,
        "overview": tmdb_title.overview,
        "poster_path": tmdb_title.poster_path,
        "popularity": tmdb_title.popularity,
        "vote_average": tmdb_title.vote_average,
        "genres": tmdb_title.genres or None,
        "extra_data": tmdb_title.raw_data,
        "is_active": True,
    }

    stmt = (
        insert(Title)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[Title.tmdb_id],
            set_={k: v for k, v in values.items() if k not in {"tmdb_id"}},
        )
        .returning(Title.id)
    )

    result = await session.execute(stmt)
    title_id = result.scalar_one()

    # Use the ORM identity map (and a SELECT if needed) to return a fully-tracked ORM object.
    return await session.get(Title, title_id)  # type: ignore[return-value]


async def insert_mentions(
    session: AsyncSession,
    *,
    title_id: uuid.UUID,
    platform: str,
    posts: list[CollectedPost],
    sentiment_by_source_id: dict[str, dict[str, Any]] | None = None,
    collected_at: datetime | None = None,
) -> int:
    """Insert Mention rows (ignore duplicates). Returns inserted row count (best-effort)."""
    sentiment_by_source_id = sentiment_by_source_id or {}

    rows: list[dict[str, Any]] = []
    collected_at_value = collected_at or datetime.now(UTC)
    for post in posts:
        sentiment = sentiment_by_source_id.get(post.source_id)
        rows.append(
            {
                "title_id": title_id,
                "platform": platform,
                "source_id": post.source_id,
                "source_type": post.source_type,
                "content": post.content,
                "author": post.author,
                "url": post.url,
                "created_at": post.created_at,
                "collected_at": collected_at_value,
                "metrics": post.metrics or None,
                "sentiment": sentiment,
            }
        )

    if not rows:
        return 0

    stmt = (
        insert(Mention)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_platform_source_title")
        .returning(Mention.id)
    )

    result = await session.execute(stmt)
    inserted_ids = result.scalars().all()
    return len(inserted_ids)


def escape_like(value: str) -> str:
    """Escape LIKE/ILIKE special characters (%, _, \\) in a search term."""
    return re.sub(r"([%_\\])", r"\\\1", value.strip())


async def get_title_by_name(session: AsyncSession, title: str) -> Title | None:
    """Find a title by case-insensitive match."""
    pattern = f"%{escape_like(title)}%"
    stmt = (
        select(Title)
        .where(Title.title.ilike(pattern, escape="\\"))
        .order_by(Title.popularity.desc().nullslast())
    )
    result = await session.execute(stmt)
    return result.scalars().first()


async def get_title_by_id(session: AsyncSession, title_id: uuid.UUID) -> Title | None:
    """Find a title by exact UUID."""
    return await session.get(Title, title_id)


async def list_active_titles(
    session: AsyncSession,
    *,
    limit: int = 500,
    offset: int = 0,
) -> list[Title]:
    """List active titles (for backfills / maintenance jobs)."""
    stmt = (
        select(Title)
        .where(Title.is_active.is_(True))
        .order_by(Title.popularity.desc().nullslast())
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_titles(
    session: AsyncSession,
    *,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Title]:
    """List titles from the DB (optionally filtered by substring match)."""
    stmt = select(Title).where(Title.is_active.is_(True))
    if query:
        pattern = f"%{escape_like(query)}%"
        stmt = stmt.where(Title.title.ilike(pattern, escape="\\"))
    stmt = stmt.order_by(Title.popularity.desc().nullslast()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_mentions_for_title(
    session: AsyncSession,
    *,
    title_id: uuid.UUID,
    platform: str,
    limit: int,
    offset: int = 0,
) -> list[Mention]:
    """Get latest mentions for a title and platform."""
    stmt = (
        select(Mention)
        .where(Mention.title_id == title_id, Mention.platform == platform)
        .order_by(Mention.collected_at.desc(), Mention.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_mentions_count(
    session: AsyncSession,
    *,
    title_id: uuid.UUID,
    platform: str,
) -> int:
    """Get total mention count for a title and platform."""
    stmt = select(func.count()).where(Mention.title_id == title_id, Mention.platform == platform)
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def get_latest_mention_collected_at(
    session: AsyncSession,
    *,
    title_id: uuid.UUID,
    platform: str,
) -> datetime | None:
    """Get latest collected_at timestamp for a title and platform."""
    stmt = select(func.max(Mention.collected_at)).where(
        Mention.title_id == title_id, Mention.platform == platform
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def get_mentions_in_window(
    session: AsyncSession,
    *,
    title_id: uuid.UUID,
    window_start: datetime,
    window_end: datetime,
    limit: int = 10_000,
) -> list[Mention]:
    """Get mentions for a title within a time window.

    Uses ``collected_at`` (when the pipeline ingested the mention) rather than
    ``created_at`` (when the content was originally published).  This matters
    because YouTube videos are often published days/weeks before we discover
    them, so ``created_at`` would place them outside the snapshot window.

    A ``limit`` cap (default 10 000) prevents unbounded memory usage on
    viral titles. When capped, this returns the **most recent** mentions
    in the window (closest to ``window_end``), which better represents
    current attention than returning the oldest rows.

    Callers that need exact totals should use SQL aggregates.
    """
    stmt = (
        select(Mention)
        .where(
            Mention.title_id == title_id,
            Mention.collected_at >= window_start,
            Mention.collected_at < window_end,
        )
        .order_by(Mention.collected_at.desc(), Mention.id.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    mentions = list(result.scalars().all())
    mentions.reverse()
    return mentions


async def get_latest_metrics_snapshot(
    session: AsyncSession,
    *,
    title_id: uuid.UUID,
    window_hours: int,
    before: datetime | None = None,
) -> MetricsSnapshot | None:
    """Get the latest metrics snapshot for a title and window size."""
    stmt = select(MetricsSnapshot).where(
        MetricsSnapshot.title_id == title_id,
        MetricsSnapshot.window_hours == window_hours,
    )
    if before is not None:
        stmt = stmt.where(MetricsSnapshot.snapshot_time < before)
    stmt = stmt.order_by(MetricsSnapshot.snapshot_time.desc()).limit(1)
    result = await session.execute(stmt)
    return result.scalars().first()


async def get_metrics_timeseries(
    session: AsyncSession,
    *,
    title_id: uuid.UUID,
    window_hours: int,
    since: datetime,
    until: datetime | None = None,
    limit: int = 2000,
) -> list[MetricsSnapshot]:
    """Get metrics snapshots for a title since a timestamp."""
    stmt = select(MetricsSnapshot).where(
        MetricsSnapshot.title_id == title_id,
        MetricsSnapshot.window_hours == window_hours,
        MetricsSnapshot.snapshot_time >= since,
    )
    if until is not None:
        stmt = stmt.where(MetricsSnapshot.snapshot_time <= until)
    stmt = stmt.order_by(MetricsSnapshot.snapshot_time.asc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_trending_by_attention_index(
    session: AsyncSession,
    *,
    window_hours: int,
    limit: int = 10,
) -> list[tuple[Title, MetricsSnapshot]]:
    """
    Return titles ordered by latest Attention Index for the given window size.
    """
    latest = (
        select(
            MetricsSnapshot.title_id.label("title_id"),
            func.max(MetricsSnapshot.snapshot_time).label("max_time"),
        )
        .where(MetricsSnapshot.window_hours == window_hours)
        .group_by(MetricsSnapshot.title_id)
        .subquery()
    )

    stmt = (
        select(Title, MetricsSnapshot)
        .join(latest, Title.id == latest.c.title_id)
        .join(
            MetricsSnapshot,
            and_(
                MetricsSnapshot.title_id == latest.c.title_id,
                MetricsSnapshot.snapshot_time == latest.c.max_time,
                MetricsSnapshot.window_hours == window_hours,
            ),
        )
        .order_by(
            MetricsSnapshot.attention_index.desc().nullslast(), Title.popularity.desc().nullslast()
        )
        .limit(limit)
    )

    result = await session.execute(stmt)
    rows = result.all()
    return [(row[0], row[1]) for row in rows]


async def upsert_metrics_snapshot(
    session: AsyncSession,
    *,
    title_id: uuid.UUID,
    snapshot_time: datetime,
    window_hours: int,
    metrics: dict[str, Any],
) -> None:
    """
    Upsert a metrics snapshot row.

    `metrics` is expected to contain normalized/aggregated values (counts, ratios, scores),
    plus an optional `raw_metrics` dict for flexibility.
    """
    values: dict[str, Any] = {
        "title_id": title_id,
        "snapshot_time": snapshot_time,
        "window_hours": window_hours,
        "mention_count": int(metrics.get("mention_count", 0)),
        "unique_authors": int(metrics.get("unique_authors", 0)),
        "reddit_mentions": int(metrics.get("reddit_mentions", 0)),
        "youtube_mentions": int(metrics.get("youtube_mentions", 0)),
        "bluesky_mentions": int(metrics.get("bluesky_mentions", 0)),
        "total_engagement": int(metrics.get("total_engagement", 0)),
        "mention_velocity": metrics.get("mention_velocity"),
        "velocity_change": metrics.get("velocity_change"),
        "avg_sentiment": metrics.get("avg_sentiment"),
        "sentiment_volatility": metrics.get("sentiment_volatility"),
        "positive_ratio": metrics.get("positive_ratio"),
        "negative_ratio": metrics.get("negative_ratio"),
        "attention_index": metrics.get("attention_index"),
        "hype_acceleration": metrics.get("hype_acceleration"),
        "raw_metrics": metrics.get("raw_metrics"),
    }

    stmt = (
        insert(MetricsSnapshot)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_title_snapshot",
            set_={
                k: v
                for k, v in values.items()
                if k not in {"title_id", "snapshot_time", "window_hours"}
            },
        )
    )
    await session.execute(stmt)


async def acquire_lease(
    session: AsyncSession,
    *,
    name: str,
    owner_id: uuid.UUID,
    ttl_seconds: int,
) -> bool:
    """
    Acquire or renew a lease.

    Returns True if acquired/renewed, False if another owner currently holds it.
    """
    # Use database-side timestamps consistently to avoid clock skew between
    # the application server and the database server.
    db_now = func.now()
    db_expires = func.now() + timedelta(seconds=ttl_seconds)

    # Avoid waiting indefinitely on locks or slow queries.
    try:
        await session.execute(text("SET LOCAL lock_timeout = '3s'"))
        await session.execute(text("SET LOCAL statement_timeout = '15s'"))
    except Exception as exc:  # pragma: no cover - defensive; won't fail the lease
        logger.debug(f"[repository] Unable to set lease timeouts: {exc}")

    # Only steal the lease if it's expired, otherwise keep current owner.
    stmt = (
        insert(Lease)
        .values(
            name=name,
            owner_id=owner_id,
            acquired_at=db_now,
            expires_at=db_expires,
            updated_at=db_now,
        )
        .on_conflict_do_update(
            index_elements=[Lease.name],
            set_={
                "owner_id": owner_id,
                "acquired_at": db_now,
                "expires_at": db_expires,
                "updated_at": db_now,
            },
            where=or_(Lease.expires_at < func.now(), Lease.owner_id == owner_id),
        )
    )

    try:
        result = await session.execute(stmt.execution_options(timeout=20))
    except Exception as exc:
        logger.warning(f"[repository] acquire_lease timed out/failed: {exc}")
        return False

    # For PostgreSQL, rowcount should be 1 if inserted/updated, 0 otherwise.
    rowcount = getattr(result, "rowcount", 0) or 0
    return int(rowcount) > 0


async def release_lease(
    session: AsyncSession,
    *,
    name: str,
    owner_id: uuid.UUID,
) -> bool:
    """Release a lease if owned by owner_id."""
    stmt = text("DELETE FROM leases WHERE name = :name AND owner_id = :owner_id")
    result = await session.execute(stmt, {"name": name, "owner_id": owner_id})
    rowcount = getattr(result, "rowcount", 0) or 0
    return int(rowcount) > 0


async def start_pipeline_run(
    session: AsyncSession,
    *,
    job_name: str,
    owner_id: uuid.UUID,
    started_at: datetime | None = None,
    stats: dict[str, Any] | None = None,
) -> PipelineRun:
    """Create and return a PipelineRun row."""
    started_at = started_at or datetime.now(UTC)
    run = PipelineRun(
        job_name=job_name,
        owner_id=owner_id,
        status="running",
        started_at=started_at,
        finished_at=None,
        error=None,
        stats=stats,
    )
    session.add(run)
    await session.flush()
    return run


async def finish_pipeline_run(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    status: str,
    finished_at: datetime | None = None,
    error: str | None = None,
    stats: dict[str, Any] | None = None,
) -> None:
    """Mark a run as finished."""
    finished_at = finished_at or datetime.now(UTC)
    result = await session.execute(select(PipelineRun).where(PipelineRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        logger.warning(f"[repository] finish_pipeline_run: run_id={run_id} not found, ignoring")
        return

    run.status = status
    run.finished_at = finished_at
    run.error = error
    if stats is not None:
        run.stats = {**(run.stats or {}), **stats}


async def get_pipeline_health_stats(
    session: AsyncSession,
) -> dict[str, Any]:
    """
    Get pipeline health statistics:
    - newest mention age (per platform)
    - per-platform counts (last 24h)
    - latest pipeline run status
    - processing lag

    Uses pg_class.reltuples for an approximate total mention count to avoid
    a full sequential scan on the (potentially large) mentions table.
    """
    now = datetime.now(UTC)
    cutoff_24h = now - timedelta(hours=24)

    # Get newest mention per platform
    newest_mention_stmt = select(
        Mention.platform, func.max(Mention.collected_at).label("latest")
    ).group_by(Mention.platform)
    result = await session.execute(newest_mention_stmt)
    newest_by_platform: dict[str, datetime | None] = {
        row.platform: row.latest for row in result.all()
    }

    # Get mention counts per platform (last 24h)
    counts_stmt = (
        select(Mention.platform, func.count().label("cnt"))
        .where(Mention.collected_at >= cutoff_24h)
        .group_by(Mention.platform)
    )
    result = await session.execute(counts_stmt)
    counts_by_platform: dict[str, int] = {row.platform: row.cnt for row in result.all()}

    # Approximate total mention count using pg_class.reltuples (O(1)) instead
    # of SELECT COUNT(*) which is a full sequential scan on PostgreSQL.
    total_stmt = text(
        "SELECT COALESCE(reltuples, 0)::bigint FROM pg_class WHERE relname = 'mentions'"
    )
    result = await session.execute(total_stmt)
    total_row = result.scalar_one_or_none()
    total_mentions = max(0, int(total_row)) if total_row is not None else 0

    # Latest pipeline runs (by job_name)
    latest_runs_stmt = (
        select(
            PipelineRun.job_name,
            PipelineRun.status,
            PipelineRun.started_at,
            PipelineRun.finished_at,
            PipelineRun.error,
            PipelineRun.stats,
        )
        .distinct(PipelineRun.job_name)
        .order_by(PipelineRun.job_name, PipelineRun.started_at.desc())
    )
    result = await session.execute(latest_runs_stmt)
    latest_runs: list[dict[str, Any]] = [
        {
            "job_name": row.job_name,
            "status": row.status,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "error": row.error,
            "stats": row.stats or {},
        }
        for row in result.all()
    ]

    # Active titles count
    active_titles_stmt = select(func.count()).where(Title.is_active.is_(True))
    result = await session.execute(active_titles_stmt)
    active_titles = int(result.scalar_one())

    # Compute newest mention age per platform
    newest_age_seconds: dict[str, float | None] = {}
    for platform, ts in newest_by_platform.items():
        if ts:
            newest_age_seconds[platform] = (now - ts).total_seconds()
        else:
            newest_age_seconds[platform] = None

    return {
        "timestamp": now.isoformat(),
        "active_titles": active_titles,
        "total_mentions": total_mentions,
        "mentions_last_24h": counts_by_platform,
        "newest_mention_age_seconds": newest_age_seconds,
        "newest_mention_at": {
            p: ts.isoformat() if ts else None for p, ts in newest_by_platform.items()
        },
        "latest_pipeline_runs": latest_runs,
    }
