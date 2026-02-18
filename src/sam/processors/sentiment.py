"""
Sentiment Analyzer

Performs sentiment analysis on social media text using VADER as baseline.
Extensible to support transformer-based models.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import Any

import nltk
from loguru import logger
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from sam.processors.text_cleaning import clean_text_for_sentiment


class SentimentModel(StrEnum):
    """Available sentiment analysis models."""

    VADER = "vader"
    ROBERTA = "roberta"
    BOTH = "both"


@dataclass
class SentimentResult:
    """Result of sentiment analysis."""

    compound: float  # -1 to 1, overall sentiment
    positive: float  # 0 to 1, positive proportion
    negative: float  # 0 to 1, negative proportion
    neutral: float  # 0 to 1, neutral proportion
    label: str  # positive, negative, neutral
    model: str  # Model used for analysis
    raw_scores: dict[str, Any]
    extra: dict[str, SentimentResult] | None = None  # Results from other models if running both

    def to_dict(self) -> dict[str, Any]:
        """Serialize result for API/storage payloads."""
        payload: dict[str, Any] = {
            "compound": self.compound,
            "positive": self.positive,
            "negative": self.negative,
            "neutral": self.neutral,
            "label": self.label,
            "model": self.model,
        }
        if self.extra:
            payload["extra"] = {name: result.to_dict() for name, result in self.extra.items()}
        return payload


@dataclass(frozen=True)
class SentimentBatchTranslationStats:
    """Translation stage counters for batch sentiment analysis."""

    translate_attempted: int = 0
    translate_count: int = 0
    translate_failures: int = 0
    translate_skipped_english: int = 0

    def to_dict(self) -> dict[str, int]:
        """Serialize stats for pipeline run payloads."""
        return {
            "translate_attempted": self.translate_attempted,
            "translate_count": self.translate_count,
            "translate_failures": self.translate_failures,
            "translate_skipped_english": self.translate_skipped_english,
        }


class SentimentAnalyzer:
    """
    Performs sentiment analysis on text.

    Uses VADER (Valence Aware Dictionary and sEntiment Reasoner) as the
    baseline model. VADER is optimized for social media text and handles
    emojis, slang, and emphasis well.

    Also supports RoBERTa (specifically twitter-roberta-base-sentiment-latest)
    which provides state-of-the-art results for social media text.
    """

    # Thresholds for sentiment classification
    POSITIVE_THRESHOLD = 0.05
    NEGATIVE_THRESHOLD = -0.05

    # RoBERTa model name
    ROBERTA_MODEL = "cardiffnlp/twitter-roberta-base-sentiment-latest"
    ROBERTA_BATCH_SIZE = 32

    def __init__(self, model: SentimentModel = SentimentModel.VADER) -> None:
        """
        Initialize the sentiment analyzer.

        Args:
            model: Sentiment model to use
        """
        self.model = model
        self._analyzer: SentimentIntensityAnalyzer | None = None
        self._tokenizer: Any | None = None
        self._roberta: Any | None = None

        self._setup_analyzer()

    def _setup_analyzer(self) -> None:
        """Initialize the sentiment analyzer."""
        if self.model == SentimentModel.VADER:
            self._analyzer = SentimentIntensityAnalyzer()
            logger.info("[sentiment] VADER analyzer initialized")
        elif self.model == SentimentModel.ROBERTA:
            self._load_roberta()
        elif self.model == SentimentModel.BOTH:
            self._analyzer = SentimentIntensityAnalyzer()
            self._load_roberta()
            logger.info("[sentiment] Both VADER and RoBERTa analyzers initialized")
        else:
            raise ValueError(f"Unknown model: {self.model}")

    def _load_roberta(self) -> None:
        """Load RoBERTa model."""
        try:
            import torch as _torch  # noqa: F401
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:
            raise ImportError(
                "RoBERTa sentiment requires the 'transformers' extra. "
                "Install with: pip install 'social-attention-monitor[transformers]'"
            ) from exc

        logger.info(f"[sentiment] Loading RoBERTa model: {self.ROBERTA_MODEL}")
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(self.ROBERTA_MODEL)
            self._roberta = AutoModelForSequenceClassification.from_pretrained(self.ROBERTA_MODEL)
            self._roberta.eval()
            logger.info("[sentiment] RoBERTa model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load RoBERTa model: {e}")
            raise

    def analyze(self, text: str) -> SentimentResult:
        """
        Analyze sentiment of a single text.

        Args:
            text: Text to analyze

        Returns:
            SentimentResult with scores and label
        """
        if not text or not text.strip():
            return SentimentResult(
                compound=0.0,
                positive=0.0,
                negative=0.0,
                neutral=1.0,
                label="neutral",
                model=self.model.value,
                raw_scores={},
            )

        # Preprocess text
        cleaned = self._preprocess(text)

        if self.model == SentimentModel.VADER:
            return self._analyze_vader(cleaned)
        elif self.model == SentimentModel.ROBERTA:
            return self._analyze_roberta(cleaned)
        elif self.model == SentimentModel.BOTH:
            # Primary is VADER for backward compatibility in standard columns
            vader_res = self._analyze_vader(cleaned)
            roberta_res = self._analyze_roberta(cleaned)

            # Attach RoBERTa as extra
            vader_res.extra = {"roberta": roberta_res}
            vader_res.model = "both"  # Indicate it was a dual run
            return vader_res
        else:
            raise ValueError(f"Unknown model: {self.model}")

    def analyze_batch(self, texts: list[str]) -> list[SentimentResult]:
        """
        Analyze sentiment of multiple texts.

        For RoBERTa / BOTH modes, tokenizes in batches for efficient GPU/CPU
        utilisation instead of one-by-one.

        Args:
            texts: List of texts to analyze

        Returns:
            List of SentimentResults
        """
        if self.model == SentimentModel.VADER:
            return [self.analyze(text) for text in texts]

        # For ROBERTA / BOTH: preprocess, then batch-infer RoBERTa once
        cleaned = []
        empty_indices: set[int] = set()
        for i, t in enumerate(texts):
            if not t or not t.strip():
                cleaned.append("")  # placeholder
                empty_indices.add(i)
            else:
                cleaned.append(self._preprocess(t))

        # Batch RoBERTa
        roberta_results = self._analyze_roberta_batch(
            [c for i, c in enumerate(cleaned) if i not in empty_indices]
        )

        # Build full result list
        results: list[SentimentResult] = []
        roberta_iter = iter(roberta_results)
        for i, text in enumerate(cleaned):
            if i in empty_indices:
                results.append(
                    SentimentResult(
                        compound=0.0,
                        positive=0.0,
                        negative=0.0,
                        neutral=1.0,
                        label="neutral",
                        model=self.model.value,
                        raw_scores={},
                    )
                )
            elif self.model == SentimentModel.ROBERTA:
                results.append(next(roberta_iter))
            else:  # BOTH
                vader_res = self._analyze_vader(text)
                roberta_res = next(roberta_iter)
                vader_res.extra = {"roberta": roberta_res}
                vader_res.model = "both"
                results.append(vader_res)
        return results

    def _analyze_vader(self, text: str) -> SentimentResult:
        """Perform VADER sentiment analysis."""
        if not self._analyzer:
            raise RuntimeError("VADER analyzer not initialized")

        scores = self._analyzer.polarity_scores(text)

        # Determine label based on compound score
        compound = scores["compound"]
        if compound >= self.POSITIVE_THRESHOLD:
            label = "positive"
        elif compound <= self.NEGATIVE_THRESHOLD:
            label = "negative"
        else:
            label = "neutral"

        return SentimentResult(
            compound=compound,
            positive=scores["pos"],
            negative=scores["neg"],
            neutral=scores["neu"],
            label=label,
            model=self.model.value,
            raw_scores=scores,
        )

    def _analyze_roberta(self, text: str) -> SentimentResult:
        """Perform RoBERTa sentiment analysis."""
        import torch
        from scipy.special import softmax

        if not self._tokenizer or not self._roberta:
            raise RuntimeError("RoBERTa model not initialized")

        # Tokenize and predict
        inputs = self._tokenizer(
            text, return_tensors="pt", truncation=True, max_length=512, padding=True
        )
        with torch.no_grad():
            output = self._roberta(**inputs)
        scores = output[0][0].detach().cpu().numpy()
        scores = softmax(scores)

        # Labels for twitter-roberta-base-sentiment-latest are:
        # 0 -> Negative
        # 1 -> Neutral
        # 2 -> Positive
        negative = float(scores[0])
        neutral = float(scores[1])
        positive = float(scores[2])

        # Calculate compound score roughly compatible with VADER (-1 to 1)
        # Verify if this formula needs adjustment
        compound = positive - negative

        if compound >= self.POSITIVE_THRESHOLD:
            label = "positive"
        elif compound <= self.NEGATIVE_THRESHOLD:
            label = "negative"
        else:
            label = "neutral"

        return SentimentResult(
            compound=compound,
            positive=positive,
            negative=negative,
            neutral=neutral,
            label=label,
            model=SentimentModel.ROBERTA.value,
            raw_scores={"roberta_neg": negative, "roberta_neu": neutral, "roberta_pos": positive},
        )

    def _analyze_roberta_batch(self, texts: list[str]) -> list[SentimentResult]:
        """Batch RoBERTa inference for a list of non-empty preprocessed texts."""
        import torch
        from scipy.special import softmax

        if not self._tokenizer or not self._roberta:
            raise RuntimeError("RoBERTa model not initialized")

        if not texts:
            return []

        results: list[SentimentResult] = []
        for start in range(0, len(texts), self.ROBERTA_BATCH_SIZE):
            batch_texts = texts[start : start + self.ROBERTA_BATCH_SIZE]
            inputs = self._tokenizer(
                batch_texts,
                return_tensors="pt",
                truncation=True,
                max_length=512,
                padding=True,
            )
            with torch.no_grad():
                output = self._roberta(**inputs)
            logits = output[0].detach().cpu().numpy()  # shape: (batch, 3)

            for row in logits:
                probs = softmax(row)
                negative = float(probs[0])
                neutral = float(probs[1])
                positive = float(probs[2])
                compound = positive - negative

                if compound >= self.POSITIVE_THRESHOLD:
                    label = "positive"
                elif compound <= self.NEGATIVE_THRESHOLD:
                    label = "negative"
                else:
                    label = "neutral"

                results.append(
                    SentimentResult(
                        compound=compound,
                        positive=positive,
                        negative=negative,
                        neutral=neutral,
                        label=label,
                        model=SentimentModel.ROBERTA.value,
                        raw_scores={
                            "roberta_neg": negative,
                            "roberta_neu": neutral,
                            "roberta_pos": positive,
                        },
                    )
                )
        return results

    def _preprocess(self, text: str) -> str:
        """
        Preprocess text for sentiment analysis.

        Preserves sentiment-carrying elements like emojis and punctuation
        while cleaning up noise.
        """
        return clean_text_for_sentiment(text)

    def get_aspect_sentiment(
        self,
        text: str,
        aspects: list[str] | None = None,
    ) -> dict[str, SentimentResult]:
        """
        Perform aspect-based sentiment analysis.

        Extracts sentences mentioning specific aspects and analyzes them separately.

        Args:
            text: Text to analyze
            aspects: List of aspects to look for (e.g., ["acting", "plot", "visuals"])

        Returns:
            Dict mapping aspect to its sentiment
        """
        default_aspects = ["acting", "plot", "story", "visuals", "music", "ending"]
        aspects = aspects or default_aspects

        results = {}
        sentences = self._split_sentences(text)

        for aspect in aspects:
            aspect_sentences = [s for s in sentences if aspect.lower() in s.lower()]

            if aspect_sentences:
                # Analyze combined sentences about this aspect
                combined = " ".join(aspect_sentences)
                results[aspect] = self.analyze(combined)
            else:
                # No mention of this aspect
                results[aspect] = SentimentResult(
                    compound=0.0,
                    positive=0.0,
                    negative=0.0,
                    neutral=0.0,
                    label="not_mentioned",
                    model=self.model.value,
                    raw_scores={},
                )

        return results

    def _split_sentences(self, text: str) -> list[str]:
        """
        Split text into sentences.

        Prefer NLTK when its tokenizer data is available; otherwise fall back to
        a lightweight regex splitter. We intentionally avoid downloading NLTK
        data at runtime (can fail in CI/production).
        """
        try:
            sentences = nltk.sent_tokenize(text)
            # NLTK is not typed (returns Any); ensure we always return list[str].
            return [str(s) for s in sentences]
        except LookupError:
            # Simple sentence splitting fallback.
            sentences = re.split(r"[.!?]+", text)
            return [s.strip() for s in sentences if s.strip()]


# Convenience functions
_default_analyzer: SentimentAnalyzer | None = None
_default_analyzer_lock = Lock()


def analyze_sentiment_batch_with_translation(
    texts: list[str],
    *,
    translate: bool,
    log_context: str = "sentiment",
) -> tuple[list[SentimentResult], SentimentBatchTranslationStats]:
    """
    Optionally translate texts to English, then run batch sentiment analysis.

    Returns:
        Tuple of (sentiment results, typed translation stats).
    """
    prepared = texts
    translation_stats = SentimentBatchTranslationStats()

    if translate:
        try:
            from sam.utils.translation import translate_batch_to_english_with_stats

            prepared, stats = translate_batch_to_english_with_stats(texts)
            translation_stats = SentimentBatchTranslationStats(
                translate_attempted=stats.attempted_count,
                translate_count=stats.changed_count,
                translate_failures=stats.failed_count,
                translate_skipped_english=stats.skipped_english_count,
            )
        except Exception as exc:
            logger.warning(
                f"[{log_context}] Translation step failed; using original text for sentiment: {exc}"
            )
            prepared = texts
            translation_stats = SentimentBatchTranslationStats(
                translate_attempted=len(texts),
                translate_failures=len(texts),
            )

    return analyze_sentiment_batch(prepared), translation_stats


def get_analyzer() -> SentimentAnalyzer:
    """Get or create the default sentiment analyzer."""
    global _default_analyzer
    if _default_analyzer is not None:
        return _default_analyzer

    with _default_analyzer_lock:
        if _default_analyzer is not None:
            return _default_analyzer

        from sam.config import get_settings

        settings = get_settings()
        # Convert string setting to SentimentModel enum
        try:
            model = SentimentModel(settings.sentiment_model)
        except ValueError:
            logger.warning(
                f"Invalid sentiment_model '{settings.sentiment_model}' in settings, using VADER"
            )
            model = SentimentModel.VADER

        fallback_enabled = getattr(settings, "sentiment_fallback_to_vader_on_error", True)
        try:
            _default_analyzer = SentimentAnalyzer(model=model)
        except Exception as exc:
            if model != SentimentModel.VADER and fallback_enabled:
                logger.warning(
                    "Could not initialize sentiment model "
                    f"'{model.value}' ({type(exc).__name__}: {exc}); falling back to VADER"
                )
                _default_analyzer = SentimentAnalyzer(model=SentimentModel.VADER)
            else:
                if model == SentimentModel.VADER:
                    raise RuntimeError(
                        "Default VADER sentiment model could not be initialized."
                    ) from exc
                raise RuntimeError(
                    "Requested sentiment model could not be initialized and fallback is disabled. "
                    f"model={model.value}"
                ) from exc
        return _default_analyzer


def analyze_sentiment(text: str) -> SentimentResult:
    """Analyze sentiment of text using default analyzer."""
    return get_analyzer().analyze(text)


def analyze_sentiment_batch(texts: list[str]) -> list[SentimentResult]:
    """Analyze sentiment of multiple texts using default analyzer."""
    return get_analyzer().analyze_batch(texts)
