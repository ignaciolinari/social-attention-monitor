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
