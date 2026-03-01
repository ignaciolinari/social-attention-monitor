"""Heavyweight VADER-vs-RoBERTa sentiment comparison logic.

Extracted from ``dashboard/app.py`` to keep the main dashboard file slim.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sam.config import get_settings

if TYPE_CHECKING:
    from sam.processors.sentiment import SentimentAnalyzer


def _parse_sarcasm(sentiment_payload: Any) -> tuple[bool | None, str]:
    """Extract sarcasm flag + display string from a sentiment dict."""
    if not isinstance(sentiment_payload, dict):
        return None, "—"
    raw_flag = sentiment_payload.get("is_sarcastic")
    if not isinstance(raw_flag, bool):
        return None, "—"
    raw_conf = sentiment_payload.get("sarcasm_confidence")
    if isinstance(raw_conf, (int, float)):
        return raw_flag, f"{'Yes' if raw_flag else 'No'} ({float(raw_conf):.2f})"
    return raw_flag, "Yes" if raw_flag else "No"


def _parse_dominant_emotion(sentiment_payload: Any) -> tuple[str | None, str]:
    """Extract dominant emotion label + display string from emotions dict."""
    if not isinstance(sentiment_payload, dict):
        return None, "—"
    raw_emotions = sentiment_payload.get("emotions")
    if not isinstance(raw_emotions, dict) or not raw_emotions:
        return None, "—"
    valid_scores: list[tuple[str, float]] = []
    for label, value in raw_emotions.items():
        if isinstance(label, str) and isinstance(value, (int, float)):
            valid_scores.append((label, float(value)))
    if not valid_scores:
        return None, "—"
    top_label, top_score = max(valid_scores, key=lambda item: item[1])
    return top_label, f"{top_label} ({top_score:.2f})"


def build_sentiment_comparison_rows(
    mentions: list[dict[str, Any]],
    *,
    translate_enabled: bool,
    vader: SentimentAnalyzer,
    roberta: SentimentAnalyzer,
) -> tuple[list[dict[str, Any]], int]:
    """Run VADER + RoBERTa on *mentions*, backfill sarcasm/emotion, return rows.

    Returns
    -------
    rows
        List of dicts ready for ``pd.DataFrame``.
    disagreements
        Number of mentions where the two models disagree on positive/negative.
    """
    analyzable_mentions: list[dict[str, Any]] = []
    original_texts: list[str] = []
    for mention in mentions:
        original = (mention.get("content") or "").strip()
        if not original:
            continue
        analyzable_mentions.append(mention)
        original_texts.append(original)

    if not analyzable_mentions:
        return [], 0

    processed_texts = original_texts
    if translate_enabled:
        from sam.utils.translation import translate_batch_to_english

        processed_texts, _changed_count, _failed_count = translate_batch_to_english(original_texts)

    vader_results = vader.analyze_batch(processed_texts)
    roberta_results = roberta.analyze_batch(processed_texts)

    mention_sentiments: list[dict[str, Any]] = []
    for mention in analyzable_mentions:
        raw_sentiment = mention.get("sentiment")
        mention_sentiments.append(dict(raw_sentiment) if isinstance(raw_sentiment, dict) else {})

    settings = get_settings()
    sarcasm_enabled = bool(getattr(settings, "enable_sarcasm_detection", False))
    emotion_enabled = bool(getattr(settings, "enable_emotion_detection", False))

    # Backfill missing NLP fields so older DB mentions still render useful signals.
    if sarcasm_enabled:
        missing_sarcasm_idxs = [
            idx
            for idx, payload in enumerate(mention_sentiments)
            if not isinstance(payload.get("is_sarcastic"), bool)
        ]
        if missing_sarcasm_idxs:
            try:
                from sam.processors.sarcasm import get_sarcasm_detector

                sarcasm_detector = get_sarcasm_detector()
                sarcasm_results = sarcasm_detector.detect_batch(
                    [processed_texts[idx] for idx in missing_sarcasm_idxs]
                )
                for idx, (is_sarc, conf) in zip(missing_sarcasm_idxs, sarcasm_results, strict=True):
                    mention_sentiments[idx]["is_sarcastic"] = bool(is_sarc)
                    mention_sentiments[idx]["sarcasm_confidence"] = round(float(conf), 4)
            except Exception:
                logging.debug("Sarcasm detection batch failed", exc_info=True)

    if emotion_enabled:
        missing_emotion_idxs = [
            idx
            for idx, payload in enumerate(mention_sentiments)
            if not isinstance(payload.get("emotions"), dict) or not payload.get("emotions")
        ]
        if missing_emotion_idxs:
            try:
                from sam.processors.emotions import get_emotion_detector

                emotion_detector = get_emotion_detector()
                emotion_results = emotion_detector.detect_batch(
                    [processed_texts[idx] for idx in missing_emotion_idxs]
                )
                for idx, emotions in zip(missing_emotion_idxs, emotion_results, strict=True):
                    if not isinstance(emotions, dict) or not emotions:
                        continue
                    mention_sentiments[idx]["emotions"] = {
                        label: round(float(score), 4) for label, score in emotions.items()
                    }
            except Exception:
                logging.debug("Emotion detection batch failed", exc_info=True)

    disagreements = 0
    rows: list[dict[str, Any]] = []
    for mention, original_text, processed_text, mention_sentiment, v_res, r_res in zip(
        analyzable_mentions,
        original_texts,
        processed_texts,
        mention_sentiments,
        vader_results,
        roberta_results,
        strict=True,
    ):
        sarcasm_flag, sarcasm_display = _parse_sarcasm(mention_sentiment)
        top_emotion_label, top_emotion_display = _parse_dominant_emotion(mention_sentiment)
        source_type_raw = mention.get("source_type")
        source_type = source_type_raw if isinstance(source_type_raw, str) else "—"

        if (v_res.label == "positive" and r_res.label == "negative") or (
            v_res.label == "negative" and r_res.label == "positive"
        ):
            disagreements += 1

        content_display = processed_text.replace("\n", " ")
        if processed_text != original_text:
            content_display = f"🌐 {content_display}"

        rows.append(
            {
                "Platform": mention.get("platform"),
                "Source": source_type,
                "Content": content_display,
                "VADER": f"{v_res.compound:.2f} ({v_res.label})",
                "RoBERTa": f"{r_res.compound:.2f} ({r_res.label})",
                "Sarcasm": sarcasm_display,
                "Top Emotion": top_emotion_display,
                "Diff": abs(v_res.compound - r_res.compound),
                "_sarcastic": sarcasm_flag,
                "_top_emotion": top_emotion_label,
                "_original": (
                    original_text.replace("\n", " ") if processed_text != original_text else None
                ),
            }
        )

    return rows, disagreements
