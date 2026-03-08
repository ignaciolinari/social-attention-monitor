"""Shared helpers for title-aware collection flows."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sam.collectors.base import CollectedPost
from sam.collectors.tmdb import TMDBTitle
from sam.processors.matching import build_search_query, match_title_text
from sam.processors.spam_detector import detect_duplicate_content, filter_spam


def tmdb_title_from_db_row(row: Any) -> TMDBTitle:
    """Rebuild a minimal ``TMDBTitle`` from a persisted DB row."""
    return TMDBTitle(
        tmdb_id=int(row.tmdb_id),
        title=str(row.title),
        original_title=str(row.original_title or row.title),
        media_type=str(row.media_type),
        release_date=row.release_date,
        overview=str(row.overview or ""),
        poster_path=row.poster_path,
        backdrop_path=(row.extra_data or {}).get("backdrop_path")
        if isinstance(row.extra_data, dict)
        else None,
        popularity=float(row.popularity or 0.0),
        vote_average=float(row.vote_average or 0.0),
        vote_count=int((row.extra_data or {}).get("vote_count", 0) or 0)
        if isinstance(row.extra_data, dict)
        else 0,
        genres=list(row.genres or []),
        original_language=str((row.extra_data or {}).get("original_language", "en"))
        if isinstance(row.extra_data, dict)
        else "en",
        revenue=row.revenue,
        budget=row.budget,
        raw_data=dict(row.extra_data or {}),
    )


def fallback_tmdb_title(title: str) -> TMDBTitle:
    """Build a minimal title context when the DB has no canonical row yet."""
    normalized = title.strip()
    return TMDBTitle(
        tmdb_id=0,
        title=normalized,
        original_title=normalized,
        media_type="unknown",
        release_date=None,
        overview="",
        poster_path=None,
        backdrop_path=None,
        popularity=0.0,
        vote_average=0.0,
        vote_count=0,
        genres=[],
        original_language="en",
        revenue=None,
        budget=None,
        raw_data={},
    )


def build_platform_query(title: TMDBTitle, *, platform: str) -> str:
    """Build the platform-specific collection query for a title."""
    normalized_platform = platform.strip().lower()
    if normalized_platform in {"youtube", "bluesky"}:
        return build_search_query(
            title.title,
            original_title=title.original_title,
            media_type=title.media_type,
            release_date=title.release_date,
        )
    return title.title


def filter_title_matches(
    posts: list[CollectedPost], *, title: TMDBTitle
) -> tuple[list[CollectedPost], int]:
    """Drop posts that do not confidently match the expected title."""
    kept: list[CollectedPost] = []
    filtered = 0
    for post in posts:
        if (
            match_title_text(
                post.content,
                title=title.title,
                original_title=title.original_title,
            )
            is None
        ):
            filtered += 1
            continue
        kept.append(post)
    return kept, filtered


def dedupe_posts_by_source(posts: list[CollectedPost]) -> list[CollectedPost]:
    """Keep the first item for each source identity."""
    seen_ids: set[str] = set()
    deduped: list[CollectedPost] = []
    for post in posts:
        source_id = str(post.source_id)
        if source_id in seen_ids:
            continue
        seen_ids.add(source_id)
        deduped.append(post)
    return deduped


def prune_duplicate_and_spam(
    posts: list[CollectedPost], *, enable_spam_filter: bool
) -> tuple[list[CollectedPost], int, int]:
    """Apply cross-author duplicate detection and spam filtering."""
    duplicate_count = 0
    spam_count = 0
    filtered_posts = posts

    dup_indices = detect_duplicate_content(filtered_posts)
    if dup_indices:
        duplicate_count = len(dup_indices)
        filtered_posts = [p for i, p in enumerate(filtered_posts) if i not in dup_indices]

    if enable_spam_filter and filtered_posts:
        filtered_posts, spam_count = filter_spam(filtered_posts)

    return filtered_posts, duplicate_count, spam_count


def title_collected_at(value: Any) -> datetime:
    """Normalize collector timestamps to tz-aware UTC datetimes."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    return datetime.now(UTC)
