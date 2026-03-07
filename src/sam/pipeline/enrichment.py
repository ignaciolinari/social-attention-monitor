"""Shared NLP enrichment service.

Provides a single source of truth for sentiment analysis, sarcasm detection,
emotion detection, and translation-before-sentiment logic.  Both the
scheduler runner and the API import from here — no duplicated logic.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from time import perf_counter
from typing import Any, cast

from loguru import logger

from sam.collectors.base import CollectedPost, collected_post_key
from sam.processors.sentiment import (
    SentimentResult,
    analyze_sentiment_batch_with_metadata,
    analyze_sentiment_batch_with_translation,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def translate_before_sentiment_enabled(settings: Any | None = None) -> bool:
    """Read runtime setting for translate-before-sentiment behaviour."""
    if settings is None:
        from sam.config import get_settings

        settings = get_settings()
    value = getattr(settings, "translate_before_sentiment", False)
    provider = getattr(settings, "translation_provider", "google_web")
    enabled = value if isinstance(value, bool) else False
    return enabled and provider != "disabled"


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_LANG_MIN_CHARS = 20  # skip very short texts


def detect_languages(texts: list[str]) -> list[str | None]:
    """Detect the language of each text using *langdetect*.

    Returns a list parallel to *texts* with ISO-639-1 codes (e.g. ``"en"``,
    ``"es"``) or ``None`` when detection fails or the text is too short.

    Designed to be called from ``asyncio.to_thread`` (CPU-bound).
    """
    from sam.utils.translation import detect_text_languages_batch

    results = detect_text_languages_batch(texts)
    return [
        lang if lang not in {"unknown", ""} and len(text) >= _LANG_MIN_CHARS else None
        for text, lang in zip(texts, results, strict=True)
    ]


# ---------------------------------------------------------------------------
# Core sentiment pipeline
# ---------------------------------------------------------------------------


def analyze_texts_for_sentiment(
    texts: list[str],
    *,
    translate: bool,
    log_context: str = "enrichment",
) -> list[SentimentResult]:
    """Analyze sentiments, optionally translating texts to English first.

    This is the **synchronous** entry-point intended to be called from
    ``asyncio.to_thread`` so the CPU-bound work stays off the event loop.
    """
    sentiments, _stats = analyze_sentiment_batch_with_translation(
        texts,
        translate=translate,
        log_context=log_context,
    )
    return sentiments


def analyze_texts_for_sentiment_with_stats(
    texts: list[str],
    *,
    translate: bool,
    log_context: str = "enrichment",
) -> tuple[list[SentimentResult], dict[str, int | float]]:
    """Translate (optionally) + analyze sentiment and return observability stats.

    Intended to be called from ``asyncio.to_thread`` (CPU-bound).
    """
    stage_start = perf_counter()
    sentiments, translation_stats = analyze_sentiment_batch_with_translation(
        texts,
        translate=translate,
        log_context=log_context,
    )
    stage_ms_total = round((perf_counter() - stage_start) * 1000, 2)
    result_stats = cast(dict[str, int | float], translation_stats.to_dict())
    result_stats["sentiment_ms_total"] = stage_ms_total
    return sentiments, result_stats


def analyze_texts_for_sentiment_with_stats_and_languages(
    texts: list[str],
    *,
    translate: bool,
    log_context: str = "enrichment",
) -> tuple[list[SentimentResult], dict[str, int | float], list[str | None]]:
    """Translate/analyze sentiment and also return detected languages."""
    stage_start = perf_counter()
    sentiments, translation_stats, detected_languages = analyze_sentiment_batch_with_metadata(
        texts,
        translate=translate,
        log_context=log_context,
    )
    stage_ms_total = round((perf_counter() - stage_start) * 1000, 2)
    result_stats = cast(dict[str, int | float], translation_stats.to_dict())
    result_stats["sentiment_ms_total"] = stage_ms_total
    return sentiments, result_stats, detected_languages


def merge_numeric_stats(target: dict[str, int | float], update: dict[str, int | float]) -> None:
    """Add numeric values from *update* into *target* by key."""
    for key, value in update.items():
        current = target.get(key, 0)
        if isinstance(current, int) and isinstance(value, int):
            target[key] = current + value
        else:
            target[key] = float(current) + float(value)


# ---------------------------------------------------------------------------
# Sentiment map building
# ---------------------------------------------------------------------------


def build_sentiment_map(
    posts: list[CollectedPost],
    sentiments: list[SentimentResult],
) -> dict[str, dict[str, Any]]:
    """Build a sentiment map from parallel lists.

    Uses legacy ``source_id`` keys when unique. If ``source_id`` collisions are
    present in the batch, uses ``platform:source_type:source_id`` keys for the
    colliding entries.
    """
    result: dict[str, dict[str, Any]] = {}
    source_id_counts = Counter(post.source_id for post in posts)
    for post, s in zip(posts, sentiments, strict=True):
        payload = s.to_dict()
        if source_id_counts[post.source_id] > 1:
            result[collected_post_key(post)] = payload
        else:
            result[post.source_id] = payload
    return result


# ---------------------------------------------------------------------------
# Optional NLP enrichments (sarcasm, emotion)
# ---------------------------------------------------------------------------


def enrich_sentiments_with_nlp(
    texts: list[str],
    sentiment_map: dict[str, dict[str, Any]],
    source_ids: list[str],
    settings: Any,
) -> None:
    """Enrich sentiment dicts in-place with emotion/sarcasm when enabled.

    Runs synchronously — already off the event-loop when called via
    ``asyncio.to_thread``.
    """
    if settings.enable_sarcasm_detection:
        try:
            from sam.processors.sarcasm import get_sarcasm_detector

            sarcasm_detector = get_sarcasm_detector()
            results = sarcasm_detector.detect_batch(texts)
            for sid, (is_sarc, conf) in zip(source_ids, results, strict=True):
                payload = sentiment_map.get(sid)
                if payload is None:
                    continue
                payload["is_sarcastic"] = bool(is_sarc)
                payload["sarcasm_confidence"] = round(float(conf), 4)
        except Exception as exc:
            logger.debug(f"[enrichment] Sarcasm enrichment skipped: {exc}")

    if settings.enable_emotion_detection:
        try:
            from sam.processors.emotions import get_emotion_detector

            emotion_detector = get_emotion_detector()
            emotion_results = emotion_detector.detect_batch(texts)
            for sid, emotions in zip(source_ids, emotion_results, strict=True):
                payload = sentiment_map.get(sid)
                if payload is None or not isinstance(emotions, dict) or not emotions:
                    continue
                payload["emotions"] = {
                    label: round(float(score), 4) for label, score in emotions.items()
                }
        except Exception as exc:
            logger.debug(f"[enrichment] Emotion enrichment skipped: {exc}")

    # Aspect-based sentiment — only run on longer texts where per-aspect
    # analysis is meaningful (e.g. reviews, long comments).
    _ASPECT_MIN_CHARS = 100
    if settings.enable_aspect_sentiment:
        try:
            from sam.processors.sentiment import get_analyzer

            analyzer = get_analyzer()
            for sid, text in zip(source_ids, texts, strict=True):
                if len(text) < _ASPECT_MIN_CHARS:
                    continue
                payload = sentiment_map.get(sid)
                if payload is None:
                    continue
                aspect_results = analyzer.get_aspect_sentiment(text)
                # Only keep aspects that were actually mentioned in the text.
                payload["aspects"] = {
                    aspect: res.to_dict()
                    for aspect, res in aspect_results.items()
                    if res.label != "not_mentioned"
                }
        except Exception as exc:
            logger.debug(f"[enrichment] Aspect sentiment skipped: {exc}")


async def build_enriched_sentiment_map(
    posts: list[CollectedPost],
    sentiments: list[SentimentResult],
    settings: Any,
) -> dict[str, dict[str, Any]]:
    """Build sentiment map and apply optional NLP enrichments (async)."""
    sentiment_map = build_sentiment_map(posts, sentiments)
    if not posts:
        return sentiment_map

    should_enrich = (
        settings.enable_emotion_detection
        or settings.enable_sarcasm_detection
        or settings.enable_aspect_sentiment
    )

    if should_enrich:
        await asyncio.to_thread(
            enrich_sentiments_with_nlp,
            [p.content for p in posts],
            sentiment_map,
            [p.source_id for p in posts],
            settings,
        )

    return sentiment_map
