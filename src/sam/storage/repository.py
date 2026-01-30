"""Database repository helpers.

Small, focused helpers to persist Titles and Mentions.
Keeps the scheduler runner simple and testable.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sam.collectors.base import CollectedPost
from sam.collectors.tmdb import TMDBTitle
from sam.storage.models import Mention, Title


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

    title_result = await session.execute(select(Title).where(Title.id == title_id))
    return title_result.scalar_one()


async def insert_mentions(
    session: AsyncSession,
    *,
    title_id: uuid.UUID,
    platform: str,
    posts: list[CollectedPost],
    sentiment_by_source_id: dict[str, dict[str, Any]] | None = None,
) -> int:
    """Insert Mention rows (ignore duplicates). Returns inserted row count (best-effort)."""
    sentiment_by_source_id = sentiment_by_source_id or {}

    rows: list[dict[str, Any]] = []
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
                "collected_at": datetime.now(UTC),
                "metrics": post.metrics or None,
                "sentiment": sentiment,
            }
        )

    if not rows:
        return 0

    stmt = (
        insert(Mention).values(rows).on_conflict_do_nothing(constraint="uq_platform_source_title")
    )

    result = await session.execute(stmt)
    # rowcount is driver-dependent for executemany; treat as best-effort.
    rowcount = getattr(result, "rowcount", 0) or 0
    return int(rowcount)


async def get_title_by_name(session: AsyncSession, title: str) -> Title | None:
    """Find a title by case-insensitive match."""
    pattern = f"%{title.strip()}%"
    stmt = (
        select(Title)
        .where(Title.title.ilike(pattern))
        .order_by(Title.popularity.desc().nullslast())
    )
    result = await session.execute(stmt)
    return result.scalars().first()


async def get_mentions_for_title(
    session: AsyncSession,
    *,
    title_id: uuid.UUID,
    platform: str,
    limit: int,
) -> list[Mention]:
    """Get latest mentions for a title and platform."""
    stmt = (
        select(Mention)
        .where(Mention.title_id == title_id, Mention.platform == platform)
        .order_by(Mention.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
