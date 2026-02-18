from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import sam.config as config_mod
import sam.processors.sentiment as sentiment_mod
import sam.utils.translation as translation_mod
from sam.processors.sentiment import (
    SentimentAnalyzer,
    SentimentBatchTranslationStats,
    SentimentModel,
    SentimentResult,
    analyze_sentiment,
)


@pytest.fixture(autouse=True)
def _reset_default_analyzer():
    """Reset the module-level cached analyzer between tests."""
    sentiment_mod._default_analyzer = None
    yield
    sentiment_mod._default_analyzer = None


def test_sentiment_vader_basic() -> None:
    """Test VADER analyzer with basic inputs."""
    analyzer = SentimentAnalyzer(model=SentimentModel.VADER)

    # Neutral
    r = analyzer.analyze(" ")
    assert r.label == "neutral"
    assert r.compound == 0.0

    # Positive
    r = analyzer.analyze("this is amazing and wonderful")
    assert r.label == "positive"
    assert r.compound > 0.05

    # Negative
    r = analyzer.analyze("this is terrible and awful")
    assert r.label == "negative"
    assert r.compound < -0.05


def test_default_analyzer() -> None:
    """Test the default analyzer global function."""
    r = analyze_sentiment("good job")
    assert r.model in ("vader", "both")
    assert r.label in ["positive", "neutral"]


def _make_roberta_mocks():
    """Create mock tokenizer and model for RoBERTa tests."""
    torch = pytest.importorskip("torch")
    mock_tokenizer = MagicMock()
    mock_tokenizer.return_value = {
        "input_ids": torch.tensor([[1, 2, 3]]),
        "attention_mask": torch.tensor([[1, 1, 1]]),
    }

    logits = torch.tensor([[0.0, 0.0, 10.0]])  # strongly positive
    mock_model = MagicMock()
    mock_model.return_value = (logits,)

    return mock_tokenizer, mock_model


def test_sentiment_roberta_mocked() -> None:
    """Test RoBERTa analyzer with mocked model and tokenizer."""
    pytest.importorskip("transformers")
    mock_tokenizer, mock_model = _make_roberta_mocks()
    with (
        patch("transformers.AutoTokenizer") as mock_tokenizer_cls,
        patch("transformers.AutoModelForSequenceClassification") as mock_model_cls,
    ):
        mock_tokenizer_cls.from_pretrained.return_value = mock_tokenizer
        mock_model_cls.from_pretrained.return_value = mock_model

        analyzer = SentimentAnalyzer(model=SentimentModel.ROBERTA)

        r = analyzer.analyze("this is a test")

        assert r.model == "roberta"
        assert r.label == "positive"
        assert r.positive > r.negative
        mock_model.eval.assert_called_once()

        mock_tokenizer_cls.from_pretrained.assert_called_with(SentimentAnalyzer.ROBERTA_MODEL)
        mock_model_cls.from_pretrained.assert_called_with(SentimentAnalyzer.ROBERTA_MODEL)


def test_sentiment_unknown_model() -> None:
    """Test initialization with unknown model."""
    with pytest.raises(ValueError, match="Unknown model"):
        SentimentAnalyzer(model="unknown_model")  # type: ignore


def test_sentiment_both_mode() -> None:
    """Test BOTH mode returns VADER primary with RoBERTa in extra."""
    pytest.importorskip("transformers")
    mock_tokenizer, mock_model = _make_roberta_mocks()
    with (
        patch("transformers.AutoTokenizer") as mock_tokenizer_cls,
        patch("transformers.AutoModelForSequenceClassification") as mock_model_cls,
    ):
        mock_tokenizer_cls.from_pretrained.return_value = mock_tokenizer
        mock_model_cls.from_pretrained.return_value = mock_model

        analyzer = SentimentAnalyzer(model=SentimentModel.BOTH)

        r = analyzer.analyze("this is amazing and wonderful")

        # Primary result should be VADER-based values
        assert r.model == "both"
        assert r.label == "positive"
        assert r.compound > 0.05

        # Extra must contain roberta
        assert r.extra is not None
        assert "roberta" in r.extra
        roberta_res = r.extra["roberta"]
        assert roberta_res.model == "roberta"
        assert roberta_res.label == "positive"
        assert roberta_res.positive > roberta_res.negative


def test_get_analyzer_falls_back_on_roberta_init_runtime_error(monkeypatch) -> None:
    """Fallback should apply when RoBERTa setup fails with non-import errors."""
    settings = SimpleNamespace(
        sentiment_model="roberta",
        sentiment_fallback_to_vader_on_error=True,
    )
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)

    def _fail_load_roberta(_self: SentimentAnalyzer) -> None:
        raise OSError("model download failed")

    monkeypatch.setattr(SentimentAnalyzer, "_load_roberta", _fail_load_roberta)

    analyzer = sentiment_mod.get_analyzer()
    assert analyzer.model == SentimentModel.VADER


def test_get_analyzer_raises_when_fallback_disabled(monkeypatch) -> None:
    """RoBERTa setup errors should surface when fallback is disabled."""
    settings = SimpleNamespace(
        sentiment_model="roberta",
        sentiment_fallback_to_vader_on_error=False,
    )
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)

    def _fail_load_roberta(_self: SentimentAnalyzer) -> None:
        raise OSError("model download failed")

    monkeypatch.setattr(SentimentAnalyzer, "_load_roberta", _fail_load_roberta)

    with pytest.raises(RuntimeError, match="fallback is disabled"):
        sentiment_mod.get_analyzer()


def test_sentiment_result_to_dict_serializes_nested_extra() -> None:
    roberta = SentimentResult(
        compound=0.7,
        positive=0.8,
        negative=0.1,
        neutral=0.1,
        label="positive",
        model="roberta",
        raw_scores={"roberta_pos": 0.8},
    )
    primary = SentimentResult(
        compound=0.2,
        positive=0.6,
        negative=0.2,
        neutral=0.2,
        label="positive",
        model="both",
        raw_scores={"compound": 0.2},
        extra={"roberta": roberta},
    )

    payload = primary.to_dict()
    assert payload["model"] == "both"
    assert payload["compound"] == 0.2
    assert "raw_scores" not in payload
    assert payload["extra"]["roberta"]["model"] == "roberta"
    assert payload["extra"]["roberta"]["compound"] == 0.7


def test_analyze_sentiment_batch_with_translation_uses_translated_texts(monkeypatch) -> None:
    captured: dict[str, list[str]] = {"texts": []}

    def _fake_translate(_texts: list[str]):
        return ["hello", "world"], translation_mod.TranslationBatchStats(
            attempted_count=2,
            changed_count=1,
            failed_count=0,
            skipped_english_count=1,
        )

    def _fake_analyze(texts: list[str]) -> list[SentimentResult]:
        captured["texts"] = texts
        return [
            SentimentResult(
                compound=0.0,
                positive=0.0,
                negative=0.0,
                neutral=1.0,
                label="neutral",
                model="vader",
                raw_scores={},
            )
            for _ in texts
        ]

    monkeypatch.setattr(translation_mod, "translate_batch_to_english_with_stats", _fake_translate)
    monkeypatch.setattr(sentiment_mod, "analyze_sentiment_batch", _fake_analyze)

    results, stats = sentiment_mod.analyze_sentiment_batch_with_translation(
        ["hola", "world"],
        translate=True,
        log_context="test",
    )

    assert len(results) == 2
    assert captured["texts"] == ["hello", "world"]
    assert isinstance(stats, SentimentBatchTranslationStats)
    assert stats.to_dict() == {
        "translate_attempted": 2,
        "translate_count": 1,
        "translate_failures": 0,
        "translate_skipped_english": 1,
    }


def test_analyze_sentiment_batch_with_translation_falls_back_on_translate_error(
    monkeypatch,
) -> None:
    captured: dict[str, list[str]] = {"texts": []}

    def _fail_translate(_texts: list[str]):
        raise RuntimeError("boom")

    def _fake_analyze(texts: list[str]) -> list[SentimentResult]:
        captured["texts"] = texts
        return [
            SentimentResult(
                compound=0.0,
                positive=0.0,
                negative=0.0,
                neutral=1.0,
                label="neutral",
                model="vader",
                raw_scores={},
            )
            for _ in texts
        ]

    monkeypatch.setattr(translation_mod, "translate_batch_to_english_with_stats", _fail_translate)
    monkeypatch.setattr(sentiment_mod, "analyze_sentiment_batch", _fake_analyze)

    source = ["uno", "dos", "tres"]
    results, stats = sentiment_mod.analyze_sentiment_batch_with_translation(
        source,
        translate=True,
        log_context="test",
    )

    assert len(results) == 3
    assert captured["texts"] == source
    assert isinstance(stats, SentimentBatchTranslationStats)
    assert stats.to_dict() == {
        "translate_attempted": 3,
        "translate_count": 0,
        "translate_failures": 3,
        "translate_skipped_english": 0,
    }
