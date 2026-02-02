"""Tests for raw data storage module."""

from __future__ import annotations

import json
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sam.collectors.base import CollectedPost, CollectionResult
from sam.pipeline.raw_storage import _slugify, _write_jsonl, persist_collection_result


def test_slugify_basic() -> None:
    assert _slugify("Hello World") == "Hello-World"
    assert _slugify("  Dune: Part Two  ") == "Dune-Part-Two"
    assert _slugify("The Last of Us") == "The-Last-of-Us"


def test_slugify_special_chars() -> None:
    assert _slugify("Movie (2024)") == "Movie-2024"
    assert _slugify("Test!@#$%^&*()") == "Test"


def test_slugify_max_length() -> None:
    long_title = "A" * 100
    result = _slugify(long_title, max_len=20)
    assert len(result) == 20


def test_write_jsonl_creates_file() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "subdir" / "test.jsonl"
        header = {"platform": "test", "count": 1}
        posts = [
            CollectedPost(
                platform="test",
                source_id="abc123",
                source_type="post",
                content="Hello world",
                author="user1",
                url="https://example.com",
                created_at=datetime.now(UTC),
                metrics={"score": 10},
            )
        ]

        _write_jsonl(path, header=header, posts=posts)

        assert path.exists()
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2  # header + 1 post

        header_data = json.loads(lines[0])
        assert header_data["type"] == "collection"
        assert header_data["platform"] == "test"

        post_data = json.loads(lines[1])
        assert post_data["type"] == "post"
        assert post_data["source_id"] == "abc123"


@pytest.mark.asyncio
async def test_persist_collection_result_writes_file() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        posts = [
            CollectedPost(
                platform="reddit",
                source_id="post1",
                source_type="post",
                content="Test content",
                author="test_user",
                url="https://reddit.com/r/test",
                created_at=datetime.now(UTC),
                metrics={"score": 100},
            )
        ]

        result = CollectionResult(
            platform="reddit",
            posts=posts,
            collected_at=datetime.now(UTC),
            success=True,
        )

        file_path = await persist_collection_result(
            result,
            raw_data_dir=tmpdir,
            title="Test Movie",
            title_id=uuid.uuid4(),
            query="Test Movie",
        )

        assert file_path is not None
        assert file_path.exists()
        assert "reddit" in str(file_path)
        assert "Test-Movie" in str(file_path)


@pytest.mark.asyncio
async def test_persist_collection_result_returns_none_for_empty() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        result = CollectionResult(
            platform="reddit",
            posts=[],
            collected_at=datetime.now(UTC),
            success=True,
        )

        file_path = await persist_collection_result(
            result,
            raw_data_dir=tmpdir,
            title="Empty Test",
        )

        assert file_path is None
