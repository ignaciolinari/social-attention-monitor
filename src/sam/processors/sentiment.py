"""
Sentiment Analyzer

Performs sentiment analysis on social media text using VADER as baseline.
Extensible to support transformer-based models.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from loguru import logger
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from sam.processors.text_cleaning import clean_text_for_sentiment


class SentimentModel(str, Enum):
    """Available sentiment analysis models."""

    VADER = "vader"
    # Future: ROBERTA = "roberta"


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


class SentimentAnalyzer:
    """
    Performs sentiment analysis on text.

    Uses VADER (Valence Aware Dictionary and sEntiment Reasoner) as the
    baseline model. VADER is optimized for social media text and handles
    emojis, slang, and emphasis well.
    """

    # Thresholds for sentiment classification
    POSITIVE_THRESHOLD = 0.05
    NEGATIVE_THRESHOLD = -0.05

    def __init__(self, model: SentimentModel = SentimentModel.VADER) -> None:
        """
        Initialize the sentiment analyzer.

        Args:
            model: Sentiment model to use
        """
        self.model = model
        self._analyzer: SentimentIntensityAnalyzer | None = None

        self._setup_analyzer()

    def _setup_analyzer(self) -> None:
        """Initialize the sentiment analyzer."""
        if self.model == SentimentModel.VADER:
            self._analyzer = SentimentIntensityAnalyzer()
            logger.info("[sentiment] VADER analyzer initialized")
        else:
            raise ValueError(f"Unknown model: {self.model}")

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
        else:
            raise ValueError(f"Unknown model: {self.model}")

    def analyze_batch(self, texts: list[str]) -> list[SentimentResult]:
        """
        Analyze sentiment of multiple texts.

        Args:
            texts: List of texts to analyze

        Returns:
            List of SentimentResults
        """
        return [self.analyze(text) for text in texts]

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
        """Split text into sentences."""
        # Simple sentence splitting
        sentences = re.split(r"[.!?]+", text)
        return [s.strip() for s in sentences if s.strip()]


# Convenience functions
_default_analyzer: SentimentAnalyzer | None = None


def get_analyzer() -> SentimentAnalyzer:
    """Get or create the default sentiment analyzer."""
    global _default_analyzer
    if _default_analyzer is None:
        _default_analyzer = SentimentAnalyzer()
    return _default_analyzer


def analyze_sentiment(text: str) -> SentimentResult:
    """Analyze sentiment of text using default analyzer."""
    return get_analyzer().analyze(text)


def analyze_sentiment_batch(texts: list[str]) -> list[SentimentResult]:
    """Analyze sentiment of multiple texts using default analyzer."""
    return get_analyzer().analyze_batch(texts)
