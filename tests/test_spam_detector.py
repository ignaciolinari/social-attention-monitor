"""Tests for sam.processors.spam_detector module."""

from __future__ import annotations

from datetime import UTC, datetime

from sam.collectors.base import CollectedPost
from sam.processors.spam_detector import filter_spam, is_likely_spam


def _make_post(content: str, *, source_id: str = "test", **kwargs) -> CollectedPost:
    return CollectedPost(
        source_id=source_id,
        content=content,
        author="testuser",
        url="https://example.com",
        platform="test",
        source_type="post",
        created_at=datetime.now(UTC),
        **kwargs,
    )


class TestIsLikelySpam:
    """Test the is_likely_spam heuristic."""

    def test_clean_text_is_not_spam(self) -> None:
        post = _make_post("I really enjoyed the latest episode. Great acting!")
        is_spam, score = is_likely_spam(post)
        assert is_spam is False
        assert score < 0.5

    def test_url_heavy_text_is_spam(self) -> None:
        post = _make_post("https://spam.com https://more.spam.com https://yet.more.spam.com")
        is_spam, score = is_likely_spam(post)
        assert score > 0.2  # Significant URL density penalty

    def test_promo_language_is_spam(self) -> None:
        post = _make_post(
            "BUY NOW! Subscribe to my channel! Use code SAVE20 for a discount! "
            "Free giveaway click here! https://deals.com https://buynow.com"
        )
        is_spam, score = is_likely_spam(post)
        assert score >= 0.3  # Promo + URL penalties combined

    def test_emoji_spam_detected(self) -> None:
        post = _make_post("🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥 CHECK THIS OUT")
        is_spam, score = is_likely_spam(post)
        assert score > 0.1  # Elevated score

    def test_all_caps_detected(self) -> None:
        post = _make_post("THIS IS ALL CAPS AND IT IS VERY LOUD AND ANNOYING AND KEEPS GOING")
        is_spam, score = is_likely_spam(post)
        assert score > 0.1  # Some penalty for all-caps


class TestFilterSpam:
    """Test the filter_spam batch function."""

    def test_filter_removes_spam_posts(self) -> None:
        clean = _make_post("Great movie, loved the ending.", source_id="clean")
        spammy = _make_post(
            "BUY NOW!!! Subscribe FREE giveaway "
            "https://spam.com/buy https://deal.com/now https://offer.com/free "
            "🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥",
            source_id="spam",
        )
        posts = [clean, spammy]
        filtered, count = filter_spam(posts)
        assert count == 1
        assert len(filtered) == 1
        assert filtered[0].source_id == "clean"

    def test_filter_empty_list(self) -> None:
        filtered, count = filter_spam([])
        assert filtered == []
        assert count == 0
