"""Expanded unit tests for sam.utils.translation.

Covers detection helpers, edge cases in translate_text, timeout/error paths
in _translate_text_with_status, batch wrapper, and per-language locking.
"""

from __future__ import annotations

from concurrent.futures import TimeoutError as FuturesTimeoutError
from unittest.mock import MagicMock

import sam.utils.translation as translation
from sam.utils.translation import (
    TranslationBatchStats,
    _ascii_ratio,
    _get_translate_lock,
    _normalize_for_detection,
    detect_text_language,
    get_translator,
    is_english_text,
    translate_batch_to_english,
    translate_batch_to_english_with_stats,
    translate_text,
)

# ---------------------------------------------------------------------------
# _ascii_ratio
# ---------------------------------------------------------------------------


class TestAsciiRatio:
    def test_pure_ascii(self) -> None:
        assert _ascii_ratio("hello world") == 1.0

    def test_empty(self) -> None:
        assert _ascii_ratio("") == 1.0

    def test_mixed(self) -> None:
        # 3 ascii + 1 non-ascii (é) => 3/4 = 0.75
        ratio = _ascii_ratio("café")
        assert abs(ratio - 0.75) < 0.01

    def test_all_non_ascii(self) -> None:
        assert _ascii_ratio("日本語") == 0.0


# ---------------------------------------------------------------------------
# _normalize_for_detection
# ---------------------------------------------------------------------------


class TestNormalizeForDetection:
    def test_strips_urls(self) -> None:
        text = "Check https://example.com for details"
        result = _normalize_for_detection(text)
        assert "https://example.com" not in result
        assert "Check" in result

    def test_collapses_whitespace(self) -> None:
        text = "hello   world\n\there"
        result = _normalize_for_detection(text)
        assert "  " not in result

    def test_truncates_long_text(self) -> None:
        text = "a" * 2000
        result = _normalize_for_detection(text)
        assert len(result) <= 1000

    def test_empty(self) -> None:
        assert _normalize_for_detection("") == ""

    def test_only_url(self) -> None:
        result = _normalize_for_detection("https://example.com")
        assert result == ""


# ---------------------------------------------------------------------------
# detect_text_language
# ---------------------------------------------------------------------------


class TestDetectTextLanguage:
    def test_empty_string(self) -> None:
        assert detect_text_language("") == "unknown"

    def test_whitespace_only(self) -> None:
        assert detect_text_language("   ") == "unknown"

    def test_short_ascii_text(self) -> None:
        # Short ASCII text under _MIN_DETECTION_CHARS should return "en"
        assert detect_text_language("Hi there") == "en"

    def test_short_non_latin_text(self) -> None:
        # Short non-Latin should NOT short-circuit to "en"
        result = detect_text_language("日本語テスト")
        # Contains non-Latin script so won't short-circuit to "en"
        assert result != "en"

    def test_english_text_detected(self) -> None:
        text = "This is a reasonably long English sentence for detection."
        result = detect_text_language(text)
        assert result == "en"

    def test_detect_langs_none_fallback(self, monkeypatch) -> None:
        """When langdetect is not available, fallback to ascii ratio."""
        monkeypatch.setattr(translation, "detect_langs", None)

        assert detect_text_language("This is English text for testing") == "en"
        assert detect_text_language("日本語のテキスト" * 5) == "unknown"

    def test_detect_langs_exception_fallback(self, monkeypatch) -> None:
        """When langdetect raises, fallback to ascii ratio."""

        def _raise(*_a, **_kw):
            raise RuntimeError("detection failed")

        monkeypatch.setattr(translation, "detect_langs", _raise)
        result = detect_text_language("This is some English text for testing")
        assert result == "en"

    def test_detect_langs_empty_predictions(self, monkeypatch) -> None:
        monkeypatch.setattr(translation, "detect_langs", lambda _text: [])
        result = detect_text_language("This is some English text for testing")
        assert result == "en"

    def test_non_latin_script_bypasses_confidence(self, monkeypatch) -> None:
        """Non-Latin scripts should return detected language regardless of confidence."""
        mock_pred = MagicMock(lang="ar", prob=0.3)
        monkeypatch.setattr(translation, "detect_langs", lambda _text: [mock_pred])
        # Arabic script chars
        text = "مرحبا بالعالم " * 5
        result = detect_text_language(text)
        assert result == "ar"

    def test_low_confidence_returns_unknown(self, monkeypatch) -> None:
        """Low confidence on Latin script should return 'unknown'."""
        mock_pred = MagicMock(lang="fr", prob=0.2)
        monkeypatch.setattr(translation, "detect_langs", lambda _text: [mock_pred])
        # All ASCII, no non-Latin script
        result = detect_text_language("asdkfj alksjdf askdfjhasd fkjhasd")
        assert result == "unknown"


# ---------------------------------------------------------------------------
# is_english_text
# ---------------------------------------------------------------------------


class TestIsEnglishText:
    def test_english(self, monkeypatch) -> None:
        monkeypatch.setattr(translation, "detect_text_language", lambda _: "en")
        assert is_english_text("hello") is True

    def test_unknown(self, monkeypatch) -> None:
        monkeypatch.setattr(translation, "detect_text_language", lambda _: "unknown")
        assert is_english_text("something") is True

    def test_french(self, monkeypatch) -> None:
        monkeypatch.setattr(translation, "detect_text_language", lambda _: "fr")
        assert is_english_text("bonjour") is False


# ---------------------------------------------------------------------------
# get_translator
# ---------------------------------------------------------------------------


class TestGetTranslator:
    def test_caches_instances(self) -> None:
        t1 = get_translator(source="auto")
        t2 = get_translator(source="auto")
        assert t1 is t2

    def test_unsupported_language_falls_back(self, monkeypatch) -> None:
        """If GoogleTranslator raises for an unsupported language, fall back to auto."""
        orig_init = translation.GoogleTranslator.__init__

        call_count = 0

        def _patched_init(self, source="auto", target="en"):
            nonlocal call_count
            call_count += 1
            if source == "xxx_fake" and call_count <= 1:
                raise ValueError("unsupported language")
            return orig_init(self, source=source, target=target)

        # Clear cache for this test
        if "xxx_fake" in translation._translators:
            del translation._translators["xxx_fake"]

        monkeypatch.setattr(translation.GoogleTranslator, "__init__", _patched_init)
        result = get_translator(source="xxx_fake")
        assert result is not None


# ---------------------------------------------------------------------------
# _get_translate_lock
# ---------------------------------------------------------------------------


class TestGetTranslateLock:
    def test_returns_same_lock_for_same_lang(self) -> None:
        lock1 = _get_translate_lock("es")
        lock2 = _get_translate_lock("es")
        assert lock1 is lock2

    def test_different_locks_for_different_langs(self) -> None:
        lock_es = _get_translate_lock("es_test")
        lock_fr = _get_translate_lock("fr_test")
        assert lock_es is not lock_fr


# ---------------------------------------------------------------------------
# translate_text
# ---------------------------------------------------------------------------


class TestTranslateText:
    def test_empty_text(self) -> None:
        assert translate_text("") == ""

    def test_whitespace_only(self) -> None:
        assert translate_text("   ") == "   "

    def test_english_skipped(self, monkeypatch) -> None:
        monkeypatch.setattr(translation, "detect_text_language", lambda _: "en")
        assert translate_text("Hello world") == "Hello world"

    def test_unknown_skipped(self, monkeypatch) -> None:
        monkeypatch.setattr(translation, "detect_text_language", lambda _: "unknown")
        assert translate_text("blah blah") == "blah blah"

    def test_non_english_translated(self, monkeypatch) -> None:
        monkeypatch.setattr(translation, "detect_text_language", lambda _: "es")
        monkeypatch.setattr(
            translation,
            "_translate_text_with_status",
            lambda _text, source_lang="auto": ("translated text", True, False),  # noqa: ARG005
        )
        result = translate_text("Hola mundo")
        assert result == "translated text"


# ---------------------------------------------------------------------------
# _translate_text_with_status
# ---------------------------------------------------------------------------


class TestTranslateTextWithStatus:
    def test_empty_text(self) -> None:
        text, changed, failed = translation._translate_text_with_status("")
        assert text == ""
        assert changed is False
        assert failed is False

    def test_whitespace_only(self) -> None:
        text, changed, failed = translation._translate_text_with_status("  ")
        assert text == "  "
        assert changed is False
        assert failed is False

    def test_none_result(self, monkeypatch) -> None:
        """When translator returns None, return original text."""
        fake = MagicMock()
        fake.translate.return_value = None
        monkeypatch.setattr(translation, "get_translator", lambda source="auto": fake)  # noqa: ARG005

        text, changed, failed = translation._translate_text_with_status("hello")
        assert text == "hello"
        assert changed is False
        assert failed is False

    def test_successful_translation(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.translate.return_value = "translated"
        monkeypatch.setattr(translation, "get_translator", lambda source="auto": fake)  # noqa: ARG005

        text, changed, failed = translation._translate_text_with_status(
            "hola mundo", source_lang="es"
        )
        assert text == "translated"
        assert changed is True
        assert failed is False

    def test_same_text_not_changed(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.translate.return_value = "original"
        monkeypatch.setattr(translation, "get_translator", lambda source="auto": fake)  # noqa: ARG005

        text, changed, failed = translation._translate_text_with_status("original")
        assert text == "original"
        assert changed is False
        assert failed is False

    def test_timeout_returns_original(self, monkeypatch) -> None:
        """Timeout should return original text with failed=True."""
        fake = MagicMock()
        monkeypatch.setattr(translation, "get_translator", lambda source="auto": fake)  # noqa: ARG005

        # Make the executor raise a timeout
        mock_future = MagicMock()
        mock_future.result.side_effect = FuturesTimeoutError()
        mock_executor = MagicMock()
        mock_executor.submit.return_value = mock_future
        monkeypatch.setattr(translation, "_translate_executor", mock_executor)

        text, changed, failed = translation._translate_text_with_status("hola")
        assert text == "hola"
        assert changed is False
        assert failed is True

    def test_exception_returns_original(self, monkeypatch) -> None:
        """Generic exceptions should return original text with failed=True."""
        fake = MagicMock()
        monkeypatch.setattr(translation, "get_translator", lambda source="auto": fake)  # noqa: ARG005

        mock_future = MagicMock()
        mock_future.result.side_effect = ConnectionError("network error")
        mock_executor = MagicMock()
        mock_executor.submit.return_value = mock_future
        monkeypatch.setattr(translation, "_translate_executor", mock_executor)

        text, changed, failed = translation._translate_text_with_status("hola")
        assert text == "hola"
        assert changed is False
        assert failed is True


# ---------------------------------------------------------------------------
# translate_batch_to_english (legacy wrapper)
# ---------------------------------------------------------------------------


class TestTranslateBatchLegacy:
    def test_legacy_wrapper(self, monkeypatch) -> None:
        monkeypatch.setattr(
            translation,
            "detect_text_language",
            lambda text: "en" if "english" in text.lower() else "fr",
        )
        monkeypatch.setattr(
            translation,
            "_translate_text_with_status",
            lambda _text, source_lang="auto": ("translated", True, False),  # noqa: ARG005
        )

        texts = ["English text", "Texte français"]
        translated, changed, failed = translate_batch_to_english(texts)
        assert len(translated) == 2
        assert translated[0] == "English text"
        assert translated[1] == "translated"
        assert changed == 1
        assert failed == 0

    def test_empty_batch(self) -> None:
        translated, changed, failed = translate_batch_to_english([])
        assert translated == []
        assert changed == 0
        assert failed == 0


# ---------------------------------------------------------------------------
# translate_batch_to_english_with_stats — edge cases
# ---------------------------------------------------------------------------


class TestTranslateBatchWithStatsEdgeCases:
    def test_empty_strings_in_batch(self) -> None:
        """Empty/whitespace strings should pass through untouched."""
        translated, stats = translate_batch_to_english_with_stats(["", "  ", ""])
        assert translated == ["", "  ", ""]
        assert stats.attempted_count == 0
        assert stats.skipped_english_count == 0

    def test_mixed_batch_stats(self, monkeypatch) -> None:
        monkeypatch.setattr(
            translation,
            "detect_text_language",
            lambda text: "en" if text == "hello" else "es",
        )
        monkeypatch.setattr(
            translation,
            "_translate_text_with_status",
            lambda _text, source_lang="auto": (f"t:{_text}", True, False),  # noqa: ARG005
        )

        translated, stats = translate_batch_to_english_with_stats(["hello", "hola", "", "mundo"])
        assert translated[0] == "hello"  # English, skipped
        assert translated[1] == "t:hola"  # Translated
        assert translated[2] == ""  # Empty, pass-through
        assert translated[3] == "t:mundo"  # Translated
        assert stats == TranslationBatchStats(
            attempted_count=2,
            changed_count=2,
            failed_count=0,
            skipped_english_count=1,
        )
