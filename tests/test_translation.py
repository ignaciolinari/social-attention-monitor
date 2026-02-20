from __future__ import annotations

import sam.utils.translation as translation


def test_translate_text_skips_english_without_calling_translator(monkeypatch) -> None:
    monkeypatch.setattr(translation, "detect_text_language", lambda _text: "en")

    def _fail_get_translator():
        raise AssertionError("translator should not be initialized for English text")

    monkeypatch.setattr(translation, "get_translator", _fail_get_translator)

    text = "This is already in English."
    assert translation.translate_text(text) == text


def test_translate_batch_only_attempts_non_english(monkeypatch) -> None:
    monkeypatch.setattr(
        translation,
        "detect_text_language",
        lambda text: "en" if text == "Hello world" else "fr",
    )

    def _fake_translate(text: str, source_lang: str = "auto") -> tuple[str, bool, bool]:  # noqa: ARG001
        if text == "Hola mundo":
            return "Hello world", True, False
        if text == "Bonjour monde":
            return text, False, True
        return text, False, False

    monkeypatch.setattr(translation, "_translate_text_with_status", _fake_translate)

    translated, stats = translation.translate_batch_to_english_with_stats(
        ["Hello world", "Hola mundo", "Bonjour monde"]
    )

    assert translated == ["Hello world", "Hello world", "Bonjour monde"]
    assert stats.skipped_english_count == 1
    assert stats.attempted_count == 2
    assert stats.changed_count == 1
    assert stats.failed_count == 1


def test_translate_text_warns_and_truncates_long_input(monkeypatch) -> None:
    monkeypatch.setattr(translation, "detect_text_language", lambda _text: "es")

    class _FakeTranslator:
        def __init__(self) -> None:
            self.last_input = ""

        def translate(self, text: str) -> str:
            self.last_input = text
            return f"translated:{len(text)}"

    warnings: list[tuple[str, dict[str, int]]] = []

    class _FakeLogger:
        def warning(self, message: str, **kwargs: int) -> None:
            warnings.append((message, kwargs))

    fake_translator = _FakeTranslator()
    monkeypatch.setattr(translation, "get_translator", lambda source="auto": fake_translator)  # noqa: ARG005
    monkeypatch.setattr(translation, "logger", _FakeLogger())

    source_text = "x" * 5001
    translated = translation.translate_text(source_text)

    assert fake_translator.last_input == source_text[:4500]
    assert translated == "translated:4500"
    assert warnings
    warning_message, warning_fields = warnings[0]
    assert "Translation input truncated" in warning_message
    assert warning_fields["original_length"] == 5001
    assert warning_fields["truncated_length"] == 4500
