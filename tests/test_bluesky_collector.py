"""Tests for BlueskyCollector._parse_post and related parsing logic."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from sam.collectors.bluesky import BlueskyCollector


@pytest.fixture
def collector() -> BlueskyCollector:
    return BlueskyCollector(demo_mode=True)


def _make_post_view(
    *,
    text: str = "Hello Bluesky!",
    handle: str = "alice.bsky.social",
    did: str = "did:plc:abc123",
    cid: str = "bafyreihash",
    uri: str = "at://did:plc:abc123/app.bsky.feed.post/3abc",
    created_at: str | datetime | None = "2026-01-30T12:00:00Z",
    like_count: int = 10,
    repost_count: int = 2,
    reply_count: int = 3,
) -> SimpleNamespace:
    """Build a minimal PostView-like object matching the atproto SDK shape."""
    record = SimpleNamespace(text=text, created_at=created_at)
    author = SimpleNamespace(handle=handle, did=did)
    return SimpleNamespace(
        record=record,
        author=author,
        cid=cid,
        uri=uri,
        like_count=like_count,
        repost_count=repost_count,
        reply_count=reply_count,
    )


class TestParsePost:
    """Tests for BlueskyCollector._parse_post."""

    def test_basic_parsing(self, collector: BlueskyCollector) -> None:
        post_view = _make_post_view()
        result = collector._parse_post(post_view)

        assert result is not None
        assert result.platform == "bluesky"
        assert result.source_id == "bafyreihash"
        assert result.source_type == "post"
        assert result.content == "Hello Bluesky!"
        assert result.author == "alice.bsky.social"
        assert result.metrics["likes"] == 10
        assert result.metrics["reposts"] == 2
        assert result.metrics["replies"] == 3

    def test_url_construction(self, collector: BlueskyCollector) -> None:
        post_view = _make_post_view(
            handle="bob.bsky.social",
            uri="at://did:plc:xyz/app.bsky.feed.post/rkey123",
        )
        result = collector._parse_post(post_view)

        assert result is not None
        assert result.url == "https://bsky.app/profile/bob.bsky.social/post/rkey123"

    def test_url_none_when_no_handle(self, collector: BlueskyCollector) -> None:
        post_view = _make_post_view(handle=None)
        # Override author to have handle=None
        post_view.author = SimpleNamespace(handle=None, did="did:plc:abc")
        result = collector._parse_post(post_view)

        assert result is not None
        assert result.url is None

    def test_z_suffix_timestamp(self, collector: BlueskyCollector) -> None:
        post_view = _make_post_view(created_at="2026-02-01T08:30:00Z")
        result = collector._parse_post(post_view)

        assert result is not None
        assert result.created_at.tzinfo is not None
        assert result.created_at.year == 2026
        assert result.created_at.month == 2
        assert result.created_at.day == 1
        assert result.created_at.hour == 8
        assert result.created_at.minute == 30

    def test_iso_offset_timestamp(self, collector: BlueskyCollector) -> None:
        post_view = _make_post_view(created_at="2026-03-15T14:00:00+00:00")
        result = collector._parse_post(post_view)

        assert result is not None
        assert result.created_at.tzinfo is not None
        assert result.created_at.hour == 14

    def test_naive_string_timestamp_gets_utc(self, collector: BlueskyCollector) -> None:
        post_view = _make_post_view(created_at="2026-04-10T10:00:00")
        result = collector._parse_post(post_view)

        assert result is not None
        assert result.created_at.tzinfo is not None
        assert result.created_at.tzinfo == UTC

    def test_datetime_object_aware(self, collector: BlueskyCollector) -> None:
        dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        post_view = _make_post_view(created_at=dt)
        result = collector._parse_post(post_view)

        assert result is not None
        assert result.created_at == dt

    def test_datetime_object_naive_gets_utc(self, collector: BlueskyCollector) -> None:
        dt = datetime(2026, 5, 1, 12, 0, 0)
        post_view = _make_post_view(created_at=dt)
        result = collector._parse_post(post_view)

        assert result is not None
        assert result.created_at.tzinfo == UTC
        assert result.created_at.hour == 12

    def test_none_timestamp_falls_back_to_now(self, collector: BlueskyCollector) -> None:
        post_view = _make_post_view(created_at=None)
        before = datetime.now(UTC)
        result = collector._parse_post(post_view)
        after = datetime.now(UTC)

        assert result is not None
        assert before <= result.created_at <= after

    def test_zero_metrics(self, collector: BlueskyCollector) -> None:
        post_view = _make_post_view(like_count=0, repost_count=0, reply_count=0)
        result = collector._parse_post(post_view)

        assert result is not None
        assert result.metrics["likes"] == 0
        assert result.metrics["reposts"] == 0
        assert result.metrics["replies"] == 0

    def test_none_metrics_default_to_zero(self, collector: BlueskyCollector) -> None:
        post_view = _make_post_view()
        post_view.like_count = None
        post_view.repost_count = None
        post_view.reply_count = None
        result = collector._parse_post(post_view)

        assert result is not None
        assert result.metrics["likes"] == 0
        assert result.metrics["reposts"] == 0
        assert result.metrics["replies"] == 0

    def test_raw_data_includes_uri_and_cid(self, collector: BlueskyCollector) -> None:
        post_view = _make_post_view(
            cid="bafycid999",
            uri="at://did:plc:test/app.bsky.feed.post/key1",
            did="did:plc:test",
        )
        result = collector._parse_post(post_view)

        assert result is not None
        assert result.raw_data["uri"] == "at://did:plc:test/app.bsky.feed.post/key1"
        assert result.raw_data["cid"] == "bafycid999"
        assert result.raw_data["author_did"] == "did:plc:test"

    def test_empty_text_returns_empty_string(self, collector: BlueskyCollector) -> None:
        post_view = _make_post_view(text="")
        result = collector._parse_post(post_view)

        assert result is not None
        assert result.content == ""

    def test_none_text_returns_empty_string(self, collector: BlueskyCollector) -> None:
        post_view = _make_post_view()
        post_view.record.text = None
        result = collector._parse_post(post_view)

        assert result is not None
        assert result.content == ""

    def test_malformed_post_returns_none(self, collector: BlueskyCollector) -> None:
        """A post_view that raises during parsing should return None, not crash."""
        # Missing required attributes entirely
        broken = SimpleNamespace()
        result = collector._parse_post(broken)
        assert result is None
