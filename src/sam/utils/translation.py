"""Translation utilities backed by deep-translator."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
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

# Timeout (seconds) for a single translation call.  Prevents the pipeline
# from blocking indefinitely if the translation provider is slow/unreachable.
_TRANSLATE_TIMEOUT_SECONDS = 10

# Dedicated executor for translation calls so timeouts work even when
# the caller is already inside a thread pool.
_translate_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="translate")

# Cache translator instances per source language
_translators: dict[str, GoogleTranslator] = {}
_translators_lock = Lock()
# Per-language locks so translations for different languages can run in
# parallel while still serializing calls for the same shared translator
# instance (deep-translator is not documented as thread-safe).
_translate_locks: dict[str, Lock] = {}
_translate_locks_guard = Lock()


@dataclass(frozen=True)
class TranslationBatchStats:
    attempted_count: int = 0
    changed_count: int = 0
    failed_count: int = 0
    skipped_english_count: int = 0


def _get_translate_lock(source_lang: str) -> Lock:
    """Return a per-language lock, creating it lazily if needed."""
    lock = _translate_locks.get(source_lang)
    if lock is not None:
        return lock
    with _translate_locks_guard:
        if source_lang not in _translate_locks:
            _translate_locks[source_lang] = Lock()
        return _translate_locks[source_lang]


def get_translator(source: str = "auto") -> GoogleTranslator:
    """Get or create GoogleTranslator instance for a specific source language."""
    global _translators
    if source in _translators:
        return _translators[source]
    with _translators_lock:
        if source not in _translators:
            try:
                _translators[source] = GoogleTranslator(source=source, target="en")
            except Exception:
                # Fallback to auto if the language is unsupported by deep-translator
                if "auto" not in _translators:
                    _translators["auto"] = GoogleTranslator(source="auto", target="en")
                return _translators["auto"]
    return _translators[source]


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


def detect_text_language(text: str) -> str:
    """
    Detect the likely language of the text.
    Returns ISO language code (e.g. 'en', 'ar') or 'unknown'.
    """
    if not text or not text.strip():
        return "unknown"

    normalized = _normalize_for_detection(text)
    if not normalized:
        return "unknown"

    # Very short texts are hard for statistical language detectors.
    if (
        len(normalized) < _MIN_DETECTION_CHARS
        and not _NON_LATIN_SCRIPT_RE.search(normalized)
        and _ascii_ratio(normalized) >= 0.98
    ):
        return "en"

    if detect_langs is None:
        return "en" if _ascii_ratio(normalized) >= 0.98 else "unknown"

    try:
        predictions = detect_langs(normalized)
    except Exception:
        return "en" if _ascii_ratio(normalized) >= 0.98 else "unknown"

    if not predictions:
        return "en" if _ascii_ratio(normalized) >= 0.98 else "unknown"

    top = predictions[0]
    top_lang = str(getattr(top, "lang", "unknown"))
    top_prob = float(getattr(top, "prob", 0.0))

    # If English is detected with high confidence
    if top_lang == "en" and top_prob >= _ENGLISH_CONFIDENCE_THRESHOLD:
        return "en"

    # For non-Latin scripts, we ignore the confidence threshold, because we know it's not English
    # and deep-translator usually handles it well.
    if _NON_LATIN_SCRIPT_RE.search(normalized):
        return top_lang

    # Otherwise return the detected top language, but if it's very low confidence just say unknown
    return top_lang if top_prob > 0.5 else "unknown"


def is_english_text(text: str) -> bool:
    """
    Best-effort language gate for translation.

    Returns True when text appears to be English (or language cannot be
    confidently inferred), so translation can be skipped.
    """
    return detect_text_language(text) in ("en", "unknown")


def translate_text(text: str) -> str:
    """
    Translate text to English when it is likely non-English.

    Returns original text if translation is skipped/failed or text is empty.
    """
    if not text or not text.strip():
        return text

    detected_lang = detect_text_language(text)
    if detected_lang in ("en", "unknown"):
        return text

    translated, _, _ = _translate_text_with_status(text, source_lang=detected_lang)
    return translated


def _translate_text_with_status(text: str, source_lang: str = "auto") -> tuple[str, bool, bool]:
    """Translate text and return (text, changed, failed).

    Applies a per-call timeout so the pipeline never blocks indefinitely
    on a slow/unreachable translation provider.
    """
    if not text or not text.strip():
        return text, False, False

    try:
        translator = get_translator(source=source_lang)
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
        # Use per-language locks so translations for different source languages
        # can proceed in parallel while serializing calls for the same translator.
        call_lock = _get_translate_lock(source_lang)

        def _do_translate() -> str | None:
            with call_lock:
                return translator.translate(truncated)  # type: ignore[no-any-return]

        future = _translate_executor.submit(_do_translate)
        result: str | None = future.result(timeout=_TRANSLATE_TIMEOUT_SECONDS)

        if result is None:
            return text, False, False
        return result, result != text, False
    except FuturesTimeoutError:
        # Cancel the future to prevent result delivery.  Note: the underlying
        # thread may still hold the per-language lock until the translation call
        # returns from the provider.  Subsequent calls for the same source
        # language will block behind it until that completes.
        future.cancel()
        logger.warning("Translation timed out after {timeout}s", timeout=_TRANSLATE_TIMEOUT_SECONDS)
        return text, False, True
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

        detected_lang = detect_text_language(text)
        if detected_lang in ("en", "unknown"):
            translated_texts.append(text)
            skipped_english += 1
            continue

        attempted += 1
        translated, was_changed, was_failed = _translate_text_with_status(
            text, source_lang=detected_lang
        )
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
