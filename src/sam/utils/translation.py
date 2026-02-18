"""Translation utilities backed by deep-translator."""

from __future__ import annotations

import re
from dataclasses import dataclass
from threading import Lock

from deep_translator import GoogleTranslator
from loguru import logger

try:
    from langdetect import DetectorFactory, detect_langs

    # Make language detection deterministic across process runs.
    DetectorFactory.seed = 0
except ImportError:  # pragma: no cover - dependency is expected, fallback is defensive.
    detect_langs = None


_NON_LATIN_SCRIPT_RE = re.compile(
    r"[\u0400-\u04FF\u0590-\u05FF\u0600-\u06FF\u0900-\u097F\u3040-\u30FF\u3400-\u9FFF\uAC00-\uD7AF]"
)
_URL_RE = re.compile(r"https?://\S+")
_MULTISPACE_RE = re.compile(r"\s+")

_ENGLISH_CONFIDENCE_THRESHOLD = 0.80
_MIN_DETECTION_CHARS = 20

# Cache translator instance
_translator: GoogleTranslator | None = None
_translator_lock = Lock()
_translate_call_lock = Lock()


@dataclass(frozen=True)
class TranslationBatchStats:
    attempted_count: int = 0
    changed_count: int = 0
    failed_count: int = 0
    skipped_english_count: int = 0


def get_translator() -> GoogleTranslator:
    """Get or create GoogleTranslator instance."""
    global _translator
    if _translator is not None:
        return _translator
    with _translator_lock:
        if _translator is None:
            # Use Google Translate (auto-detect source -> English)
            _translator = GoogleTranslator(source="auto", target="en")
    return _translator


def _normalize_for_detection(text: str) -> str:
    """Normalize text before language detection."""
    compact = _MULTISPACE_RE.sub(" ", _URL_RE.sub(" ", text)).strip()
    # Keep detection lightweight and bounded.
    return compact[:1000]


def _ascii_ratio(text: str) -> float:
    if not text:
        return 1.0
    ascii_count = sum(1 for ch in text if ch.isascii())
    return ascii_count / len(text)


def is_english_text(text: str) -> bool:
    """
    Best-effort language gate for translation.

    Returns True when text appears to be English (or language cannot be
    confidently inferred), so translation can be skipped.
    """
    if not text or not text.strip():
        return True

    normalized = _normalize_for_detection(text)
    if not normalized:
        return True

    # Fast path for obviously non-Latin scripts.
    if _NON_LATIN_SCRIPT_RE.search(normalized):
        return False

    # Very short texts are hard for statistical language detectors.
    if len(normalized) < _MIN_DETECTION_CHARS:
        return _ascii_ratio(normalized) >= 0.98

    if detect_langs is None:
        return _ascii_ratio(normalized) >= 0.98

    try:
        predictions = detect_langs(normalized)
    except Exception:
        return _ascii_ratio(normalized) >= 0.98

    if not predictions:
        return _ascii_ratio(normalized) >= 0.98

    top = predictions[0]
    top_lang = getattr(top, "lang", "")
    top_prob = float(getattr(top, "prob", 0.0))
    return bool(top_lang == "en" and top_prob >= _ENGLISH_CONFIDENCE_THRESHOLD)


def translate_text(text: str) -> str:
    """
    Translate text to English when it is likely non-English.

    Returns original text if translation is skipped/failed or text is empty.
    """
    if not text or not text.strip():
        return text
    if is_english_text(text):
        return text

    translated, _, _ = _translate_text_with_status(text)
    return translated


def _translate_text_with_status(text: str) -> tuple[str, bool, bool]:
    """Translate text and return (text, changed, failed)."""
    if not text or not text.strip():
        return text, False, False

    try:
        translator = get_translator()
        # deep-translator handles chunks automatically in recent versions,
        # but basic constraints apply (5000 chars).
        # For social media, texts are usually short enough.
        if len(text) > 4500:
            logger.warning(
                "Translation input truncated for provider limit "
                "({original_length} -> {truncated_length})",
                original_length=len(text),
                truncated_length=4500,
            )
        truncated = text[:4500]  # Safety truncate
        # deep-translator does not document thread safety for shared instances.
        # Serialize calls on the cached translator to avoid concurrent access.
        with _translate_call_lock:
            result = translator.translate(truncated)
        if result is None:
            return text, False, False
        return result, result != text, False
    except Exception as exc:
        logger.warning("Translation failed ({error_type})", error_type=type(exc).__name__)
        return text, False, True


def translate_batch_to_english_with_stats(
    texts: list[str],
) -> tuple[list[str], TranslationBatchStats]:
    """
    Translate likely non-English texts in a batch.

    Returns translated texts and detailed stats.
    """
    translated_texts: list[str] = []
    attempted = 0
    changed = 0
    failed = 0
    skipped_english = 0

    for text in texts:
        if not text or not text.strip():
            translated_texts.append(text)
            continue

        if is_english_text(text):
            translated_texts.append(text)
            skipped_english += 1
            continue

        attempted += 1
        translated, was_changed, was_failed = _translate_text_with_status(text)
        translated_texts.append(translated)
        if was_changed:
            changed += 1
        if was_failed:
            failed += 1

    return translated_texts, TranslationBatchStats(
        attempted_count=attempted,
        changed_count=changed,
        failed_count=failed,
        skipped_english_count=skipped_english,
    )


def translate_batch_to_english(texts: list[str]) -> tuple[list[str], int, int]:
    """Translate likely non-English texts and return legacy stats tuple."""
    translated_texts, stats = translate_batch_to_english_with_stats(texts)
    return translated_texts, stats.changed_count, stats.failed_count
