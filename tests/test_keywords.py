"""Tests for sam.processors.keywords module."""

from __future__ import annotations

import pytest

from sam.processors.keywords import extract_hashtags, extract_keywords

yake = pytest.importorskip("yake", reason="yake not installed")


class TestExtractKeywords:
    """Test keyword extraction (requires yake)."""

    def test_extracts_keywords_from_text(self) -> None:
        texts = [
            "The new Batman movie has incredible cinematography and direction.",
            "Batman returns with a darker tone and better action sequences.",
        ]
        keywords = extract_keywords(texts, top_n=5)
        assert len(keywords) > 0
        assert len(keywords) <= 5
        # Each entry is (keyword, score)
        for kw, score in keywords:
            assert isinstance(kw, str)
            assert isinstance(score, float)
            assert len(kw) > 0

    def test_extracts_keywords_empty_input(self) -> None:
        keywords = extract_keywords([], top_n=5)
        assert keywords == []

    def test_extracts_from_single_text(self) -> None:
        texts = ["Artificial intelligence is transforming the entertainment industry."]
        keywords = extract_keywords(texts, top_n=3)
        assert len(keywords) > 0
        # Should find something related to AI or entertainment
        kw_strings = [kw for kw, _ in keywords]
        assert any("intelligence" in k.lower() or "entertainment" in k.lower() for k in kw_strings)


class TestExtractHashtags:
    """Test hashtag counting."""

    def test_counts_hashtags(self) -> None:
        texts = [
            "Loved this! #Batman #DCU",
            "Not great #Batman #disappointing",
            "Incredible #DCU #comics",
        ]
        result = extract_hashtags(texts, top_n=3)
        assert len(result) > 0
        # batman should be top
        tags = [tag for tag, _ in result]
        assert "#batman" in tags or "#Batman" in tags or "batman" in [t.lower() for t in tags]

    def test_empty_input(self) -> None:
        result = extract_hashtags([], top_n=5)
        assert result == []

    def test_no_hashtags(self) -> None:
        result = extract_hashtags(["No hashtags here at all"], top_n=5)
        assert result == []


class TestYakeExtractorCache:
    """B9: YAKE extractor instances are cached and reused."""

    def test_same_params_return_same_instance(self) -> None:
        from sam.processors.keywords import _get_yake_extractor

        # Clear lru_cache to start fresh.
        _get_yake_extractor.cache_clear()

        ext1 = _get_yake_extractor("en", 2, 15)
        ext2 = _get_yake_extractor("en", 2, 15)
        assert ext1 is ext2

    def test_different_params_return_different_instances(self) -> None:
        from sam.processors.keywords import _get_yake_extractor

        _get_yake_extractor.cache_clear()

        ext_a = _get_yake_extractor("en", 2, 15)
        ext_b = _get_yake_extractor("en", 3, 10)
        assert ext_a is not ext_b

    def test_dedup_lim_in_cache_key(self) -> None:
        """Different dedup_lim values should produce separate cached instances."""
        from sam.processors.keywords import _get_yake_extractor

        _get_yake_extractor.cache_clear()

        ext_a = _get_yake_extractor("en", 2, 15, dedup_lim=0.7)
        ext_b = _get_yake_extractor("en", 2, 15, dedup_lim=0.5)
        assert ext_a is not ext_b
