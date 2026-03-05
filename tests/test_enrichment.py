"""Tests for sam.pipeline.enrichment — shared NLP enrichment service."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sam.collectors.base import CollectedPost
from sam.pipeline.enrichment import (
    analyze_texts_for_sentiment,
    analyze_texts_for_sentiment_with_stats,
    build_enriched_sentiment_map,
    build_sentiment_map,
    enrich_sentiments_with_nlp,
    merge_numeric_stats,
    translate_before_sentiment_enabled,
)
from sam.processors.sentiment import SentimentBatchTranslationStats, SentimentResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_post(source_id: str, content: str = "test") -> CollectedPost:
    return CollectedPost(
        platform="test",
        source_id=source_id,
        source_type="post",
        content=content,
        author="author",
        url="https://example.com",
        created_at=datetime.now(UTC),
        metrics={},
    )


def _make_sentiment(label: str = "positive", compound: float = 0.9) -> SentimentResult:
    return SentimentResult(
        compound=compound,
        positive=max(compound, 0.0),
        negative=abs(min(compound, 0.0)),
        neutral=0.0,
        label=label,
        model="test",
        raw_scores={},
        confidence=0.9,
    )


# ---------------------------------------------------------------------------
# translate_before_sentiment_enabled
# ---------------------------------------------------------------------------


class TestTranslateBeforeSentimentEnabled:
    def test_returns_true_when_enabled(self) -> None:
        settings = SimpleNamespace(translate_before_sentiment=True)
        assert translate_before_sentiment_enabled(settings) is True

    def test_returns_false_when_disabled(self) -> None:
        settings = SimpleNamespace(translate_before_sentiment=False)
        assert translate_before_sentiment_enabled(settings) is False

    def test_returns_false_for_non_bool_value(self) -> None:
        settings = SimpleNamespace(translate_before_sentiment="yes")
        assert translate_before_sentiment_enabled(settings) is False

    def test_returns_false_when_attribute_missing(self) -> None:
        settings = SimpleNamespace()
        assert translate_before_sentiment_enabled(settings) is False

    def test_reads_from_get_settings_when_none(self) -> None:
        with patch("sam.config.get_settings") as mock_gs:
            mock_gs.return_value = SimpleNamespace(translate_before_sentiment=True)
            assert translate_before_sentiment_enabled(None) is True


# ---------------------------------------------------------------------------
# merge_numeric_stats
# ---------------------------------------------------------------------------


class TestMergeNumericStats:
    def test_merge_empty_update(self) -> None:
        target: dict[str, int | float] = {"a": 1}
        merge_numeric_stats(target, {})
        assert target == {"a": 1}

    def test_merge_new_keys(self) -> None:
        target: dict[str, int | float] = {}
        merge_numeric_stats(target, {"x": 5, "y": 2.5})
        assert target == {"x": 5, "y": 2.5}

    def test_merge_adds_int_values(self) -> None:
        target: dict[str, int | float] = {"a": 3}
        merge_numeric_stats(target, {"a": 7})
        assert target["a"] == 10
        assert isinstance(target["a"], int)

    def test_merge_adds_float_values(self) -> None:
        target: dict[str, int | float] = {"a": 1.5}
        merge_numeric_stats(target, {"a": 2.5})
        assert target["a"] == 4.0
        assert isinstance(target["a"], float)

    def test_merge_int_and_float_produces_float(self) -> None:
        target: dict[str, int | float] = {"a": 3}
        merge_numeric_stats(target, {"a": 1.5})
        assert target["a"] == 4.5
        assert isinstance(target["a"], float)


# ---------------------------------------------------------------------------
# build_sentiment_map
# ---------------------------------------------------------------------------


class TestBuildSentimentMap:
    def test_basic_mapping(self) -> None:
        posts = [_make_post("id1"), _make_post("id2")]
        sentiments = [_make_sentiment("positive", 0.9), _make_sentiment("negative", -0.7)]
        result = build_sentiment_map(posts, sentiments)
        assert set(result.keys()) == {"id1", "id2"}
        assert result["id1"]["label"] == "positive"
        assert result["id2"]["label"] == "negative"

    def test_empty_lists(self) -> None:
        assert build_sentiment_map([], []) == {}

    def test_mismatched_lengths_raises(self) -> None:
        posts = [_make_post("id1")]
        sentiments = [_make_sentiment(), _make_sentiment()]
        with pytest.raises(ValueError):
            build_sentiment_map(posts, sentiments)

    def test_uses_composite_keys_when_source_ids_collide(self) -> None:
        post_a = _make_post("shared")
        post_b = _make_post("shared")
        post_a.platform = "reddit"
        post_b.platform = "youtube"
        sentiments = [_make_sentiment("positive", 0.3), _make_sentiment("negative", -0.4)]

        result = build_sentiment_map([post_a, post_b], sentiments)

        assert "shared" not in result
        assert result["reddit:post:shared"]["label"] == "positive"
        assert result["youtube:post:shared"]["label"] == "negative"


# ---------------------------------------------------------------------------
# analyze_texts_for_sentiment
# ---------------------------------------------------------------------------


class TestAnalyzeTextsForSentiment:
    @patch("sam.pipeline.enrichment.analyze_sentiment_batch_with_translation")
    def test_returns_sentiments_only(self, mock_batch: MagicMock) -> None:
        sentiments = [_make_sentiment()]
        mock_batch.return_value = (sentiments, MagicMock())
        result = analyze_texts_for_sentiment(["hello"], translate=False)
        assert result == sentiments

    @patch("sam.pipeline.enrichment.analyze_sentiment_batch_with_translation")
    def test_passes_translate_flag(self, mock_batch: MagicMock) -> None:
        mock_batch.return_value = ([], MagicMock())
        analyze_texts_for_sentiment(["hi"], translate=True, log_context="test")
        mock_batch.assert_called_once_with(["hi"], translate=True, log_context="test")


# ---------------------------------------------------------------------------
# analyze_texts_for_sentiment_with_stats
# ---------------------------------------------------------------------------


class TestAnalyzeTextsForSentimentWithStats:
    @patch("sam.pipeline.enrichment.analyze_sentiment_batch_with_translation")
    def test_returns_stats_with_timing(self, mock_batch: MagicMock) -> None:
        sentiments = [_make_sentiment()]
        stats = SentimentBatchTranslationStats(translate_count=1)
        mock_batch.return_value = (sentiments, stats)
        result_s, result_stats = analyze_texts_for_sentiment_with_stats(["hello"], translate=True)
        assert result_s == sentiments
        assert "sentiment_ms_total" in result_stats
        assert result_stats["translate_count"] == 1


# ---------------------------------------------------------------------------
# enrich_sentiments_with_nlp
# ---------------------------------------------------------------------------


class TestEnrichSentimentsWithNlp:
    def test_sarcasm_enrichment(self) -> None:
        sentiment_map: dict[str, dict[str, Any]] = {
            "id1": {"label": "positive", "score": 0.9},
        }
        settings = SimpleNamespace(
            enable_sarcasm_detection=True,
            enable_emotion_detection=False,
            enable_aspect_sentiment=False,
        )
        with patch("sam.processors.sarcasm.get_sarcasm_detector") as mock_get:
            mock_detector = MagicMock()
            mock_detector.detect_batch.return_value = [(True, 0.95)]
            mock_get.return_value = mock_detector

            enrich_sentiments_with_nlp(["text1"], sentiment_map, ["id1"], settings)

        assert sentiment_map["id1"]["is_sarcastic"] is True
        assert sentiment_map["id1"]["sarcasm_confidence"] == 0.95

    def test_emotion_enrichment(self) -> None:
        sentiment_map: dict[str, dict[str, Any]] = {
            "id1": {"label": "positive", "score": 0.9},
        }
        settings = SimpleNamespace(
            enable_sarcasm_detection=False,
            enable_emotion_detection=True,
            enable_aspect_sentiment=False,
        )
        with patch("sam.processors.emotions.get_emotion_detector") as mock_get:
            mock_detector = MagicMock()
            mock_detector.detect_batch.return_value = [{"joy": 0.8, "sadness": 0.1}]
            mock_get.return_value = mock_detector

            enrich_sentiments_with_nlp(["text1"], sentiment_map, ["id1"], settings)

        assert sentiment_map["id1"]["emotions"] == {"joy": 0.8, "sadness": 0.1}

    def test_sarcasm_failure_is_silenced(self) -> None:
        sentiment_map: dict[str, dict[str, Any]] = {"id1": {"label": "positive"}}
        settings = SimpleNamespace(
            enable_sarcasm_detection=True,
            enable_emotion_detection=False,
            enable_aspect_sentiment=False,
        )
        with patch("sam.processors.sarcasm.get_sarcasm_detector", side_effect=RuntimeError("boom")):
            enrich_sentiments_with_nlp(["text1"], sentiment_map, ["id1"], settings)
        # Should not crash; sarcasm fields should be absent
        assert "is_sarcastic" not in sentiment_map["id1"]

    def test_skips_missing_source_ids(self) -> None:
        sentiment_map: dict[str, dict[str, Any]] = {}  # No entries at all
        settings = SimpleNamespace(
            enable_sarcasm_detection=True,
            enable_emotion_detection=False,
            enable_aspect_sentiment=False,
        )
        with patch("sam.processors.sarcasm.get_sarcasm_detector") as mock_get:
            mock_detector = MagicMock()
            mock_detector.detect_batch.return_value = [(False, 0.1)]
            mock_get.return_value = mock_detector

            # Should not crash even though "id1" is not in sentiment_map
            enrich_sentiments_with_nlp(["text1"], sentiment_map, ["id1"], settings)

    def test_aspect_sentiment_skips_short_texts(self) -> None:
        sentiment_map: dict[str, dict[str, Any]] = {"id1": {"label": "positive"}}
        settings = SimpleNamespace(
            enable_sarcasm_detection=False,
            enable_emotion_detection=False,
            enable_aspect_sentiment=True,
        )
        with patch("sam.processors.sentiment.get_analyzer") as mock_get:
            mock_analyzer = MagicMock()
            mock_get.return_value = mock_analyzer

            # Short text (<100 chars) should be skipped
            enrich_sentiments_with_nlp(["short"], sentiment_map, ["id1"], settings)
            mock_analyzer.get_aspect_sentiment.assert_not_called()


# ---------------------------------------------------------------------------
# build_enriched_sentiment_map (async)
# ---------------------------------------------------------------------------


class TestBuildEnrichedSentimentMap:
    @pytest.mark.asyncio
    async def test_no_enrichment_when_disabled(self) -> None:
        posts = [_make_post("id1")]
        sentiments = [_make_sentiment()]
        settings = SimpleNamespace(
            enable_sarcasm_detection=False,
            enable_emotion_detection=False,
            enable_aspect_sentiment=False,
        )
        result = await build_enriched_sentiment_map(posts, sentiments, settings)
        assert "id1" in result
        assert result["id1"]["label"] == "positive"

    @pytest.mark.asyncio
    async def test_empty_posts_returns_empty(self) -> None:
        settings = SimpleNamespace(
            enable_sarcasm_detection=True,
            enable_emotion_detection=True,
            enable_aspect_sentiment=True,
        )
        result = await build_enriched_sentiment_map([], [], settings)
        assert result == {}

    @pytest.mark.asyncio
    async def test_enrichment_runs_when_enabled(self) -> None:
        posts = [_make_post("id1", content="some text")]
        sentiments = [_make_sentiment()]
        settings = SimpleNamespace(
            enable_sarcasm_detection=True,
            enable_emotion_detection=False,
            enable_aspect_sentiment=False,
        )
        with patch("sam.pipeline.enrichment.enrich_sentiments_with_nlp") as mock_enrich:
            await build_enriched_sentiment_map(posts, sentiments, settings)
            assert mock_enrich.called
