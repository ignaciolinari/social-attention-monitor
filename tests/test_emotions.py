"""Tests for sam.processors.emotions module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestEmotionDetector:
    """Tests for the EmotionDetector class."""

    def test_unavailable_detector_returns_empty(self) -> None:
        """When model is not loaded, detect returns empty dict."""
        from sam.processors.emotions import EmotionDetector

        detector = EmotionDetector.__new__(EmotionDetector)
        detector._tokenizer = None
        detector._model = None
        detector._available = False

        result = detector.detect("This is great!")
        assert result == {}

    def test_unavailable_batch_returns_empty_dicts(self) -> None:
        """When model is not loaded, detect_batch returns empty dicts."""
        from sam.processors.emotions import EmotionDetector

        detector = EmotionDetector.__new__(EmotionDetector)
        detector._tokenizer = None
        detector._model = None
        detector._available = False

        results = detector.detect_batch(["Hello", "World"])
        assert len(results) == 2
        assert results[0] == {}
        assert results[1] == {}

    def test_is_available_property(self) -> None:
        """is_available reflects _available flag."""
        from sam.processors.emotions import EmotionDetector

        detector = EmotionDetector.__new__(EmotionDetector)
        detector._available = False
        assert detector.is_available is False

        detector._available = True
        assert detector.is_available is True

    def test_detect_empty_text(self) -> None:
        """detect with empty text returns empty dict regardless of availability."""
        from sam.processors.emotions import EmotionDetector

        detector = EmotionDetector.__new__(EmotionDetector)
        detector._available = True  # Would be available, but text is empty

        result = detector.detect("")
        assert result == {}
        result = detector.detect("   ")
        assert result == {}

    def test_get_emotion_detector_returns_instance(self) -> None:
        """get_emotion_detector creates an instance."""
        with patch("sam.processors.emotions.EmotionDetector") as MockDetector:
            import sam.processors.emotions as mod

            mod._detector = None  # Reset singleton
            mock_instance = MagicMock()
            MockDetector.return_value = mock_instance

            result = mod.get_emotion_detector()
            assert result is mock_instance
            mod._detector = None  # Clean up
