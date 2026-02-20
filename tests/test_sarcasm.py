"""Tests for sam.processors.sarcasm module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestSarcasmDetector:
    """Tests for the SarcasmDetector class."""

    def test_unavailable_detector_returns_false(self) -> None:
        """When model is not loaded, detect returns (False, 0.0)."""
        from sam.processors.sarcasm import SarcasmDetector

        detector = SarcasmDetector.__new__(SarcasmDetector)
        detector._tokenizer = None
        detector._model = None
        detector._available = False

        is_sarcastic, confidence = detector.detect("Oh sure, this is amazing")
        assert is_sarcastic is False
        assert confidence == 0.0

    def test_unavailable_batch_returns_defaults(self) -> None:
        """When model is not loaded, detect_batch returns defaults."""
        from sam.processors.sarcasm import SarcasmDetector

        detector = SarcasmDetector.__new__(SarcasmDetector)
        detector._tokenizer = None
        detector._model = None
        detector._available = False

        results = detector.detect_batch(["Hello", "World"])
        assert len(results) == 2
        assert results[0] == (False, 0.0)
        assert results[1] == (False, 0.0)

    def test_is_available_property(self) -> None:
        """is_available reflects _available flag."""
        from sam.processors.sarcasm import SarcasmDetector

        detector = SarcasmDetector.__new__(SarcasmDetector)
        detector._available = False
        assert detector.is_available is False

        detector._available = True
        assert detector.is_available is True

    def test_detect_empty_text(self) -> None:
        """detect with empty text returns (False, 0.0)."""
        from sam.processors.sarcasm import SarcasmDetector

        detector = SarcasmDetector.__new__(SarcasmDetector)
        detector._available = True

        result = detector.detect("")
        assert result == (False, 0.0)
        result = detector.detect("   ")
        assert result == (False, 0.0)

    def test_get_sarcasm_detector_returns_instance(self) -> None:
        """get_sarcasm_detector creates an instance."""
        with patch("sam.processors.sarcasm.SarcasmDetector") as MockDetector:
            import sam.processors.sarcasm as mod

            mod._detector = None  # Reset singleton
            mock_instance = MagicMock()
            MockDetector.return_value = mock_instance

            result = mod.get_sarcasm_detector()
            assert result is mock_instance
            mod._detector = None  # Clean up
