"""Raw data persistence to local filesystem.

This is intentionally simple and opt-in. The goal is reproducibility:
- keep a copy of what we collected from each platform
- enable offline reprocessing/debugging

Data is written as JSONL (one post per line) with a small header object.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from sam.collectors.base import CollectedPost, CollectionResult

_SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _slugify(value: str, *, max_len: int = 80) -> str:
    v = value.strip().replace(" ", "-")
    v = _SLUG_RE.sub("-", v)
    v = re.sub(r"-+", "-", v).strip("-")
    return v[:max_len] if len(v) > max_len else v


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (datetime,)):
        # Ensure tz-aware ISO, default to UTC if missing.
        if obj.tzinfo is None:
            obj = obj.replace(tzinfo=UTC)
        return obj.isoformat()
    if isinstance(obj, (uuid.UUID,)):
        return str(obj)
    # dataclasses.is_dataclass() is True for both dataclass *types* and *instances*.
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    return str(obj)


def _post_to_dict(post: CollectedPost) -> dict[str, Any]:
    data = asdict(post)
    # Normalize datetimes
    created = post.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    data["created_at"] = created.isoformat()
    return data


def _write_jsonl(path: Path, *, header: dict[str, Any], posts: list[CollectedPost]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        f.write(
            json.dumps({"type": "collection", **header}, ensure_ascii=False, default=_json_default)
        )
        f.write("\n")
        for post in posts:
            f.write(
                json.dumps(
                    {"type": "post", **_post_to_dict(post)},
                    ensure_ascii=False,
                    default=_json_default,
                )
            )
            f.write("\n")


async def persist_collection_result(
    result: CollectionResult,
    *,
    raw_data_dir: str,
    title: str,
    title_id: uuid.UUID | None = None,
    query: str | None = None,
    run_id: uuid.UUID | None = None,
    extra: dict[str, Any] | None = None,
) -> Path | None:
    """Persist a collection result to the filesystem.

    Returns the written file path, or None if there was nothing to write.
    """
    if not result.posts:
        return None

    collected_at = result.collected_at
    if collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=UTC)

    day = collected_at.date()
    ts = collected_at.strftime("%Y%m%dT%H%M%SZ")
    title_slug = _slugify(title) or "unknown-title"

    base = Path(raw_data_dir)
    # Keep things relatively stable and human-browsable.
    path = base / result.platform / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
    filename = f"{ts}_{title_slug}.jsonl"
    full_path = path / filename

    header: dict[str, Any] = {
        "platform": result.platform,
        "success": result.success,
        "error": result.error,
        "rate_limit_remaining": result.rate_limit_remaining,
        "collected_at": collected_at,
        "query": query,
        "title": title,
        "title_id": title_id,
        "run_id": run_id,
        "count": len(result.posts),
        "extra": extra or {},
        "cwd": os.getcwd(),
    }

    try:
        await asyncio.to_thread(_write_jsonl, full_path, header=header, posts=result.posts)
        logger.debug(f"[raw_storage] wrote {len(result.posts)} items to {full_path}")
        return full_path
    except Exception as e:
        logger.warning(f"[raw_storage] failed to write raw data: {e}")
        return None
