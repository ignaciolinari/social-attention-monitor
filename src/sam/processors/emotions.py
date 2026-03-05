"""Emotion detection processor.

Classifies text into fine-grained emotions (anger, disgust, fear, joy,
neutral, sadness, surprise) using a DistilRoBERTa model.

Requires the ``transformers`` optional dependency.  When unavailable the
detector returns empty results instead of raising.
"""

from __future__ import annotations

from threading import Lock
from typing import Any

from loguru import logger

EMOTION_MODEL = "j-hartmann/emotion-english-distilroberta-base"
EMOTION_LABELS = ("anger", "disgust", "fear", "joy", "neutral", "sadness", "surprise")
BATCH_SIZE = 32


class EmotionDetector:
    """Detect emotions in text using a transformer classifier."""

    def __init__(self) -> None:
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._available = False
        # Guard concurrent transformer forward-passes from multiple
        # ``asyncio.to_thread`` workers — model is not thread-safe.
        self._inference_lock = Lock()
        self._load()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        try:
            import torch as _torch  # noqa: F401
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError:
            logger.info("[emotions] transformers not installed — emotion detection disabled")
            return

        try:
            logger.info(f"[emotions] Loading model: {EMOTION_MODEL}")
            self._tokenizer = AutoTokenizer.from_pretrained(  # type: ignore[no-untyped-call,unused-ignore]
                EMOTION_MODEL
            )
            self._model = AutoModelForSequenceClassification.from_pretrained(EMOTION_MODEL)
            self._model.eval()
            self._available = True
            logger.info("[emotions] model loaded successfully")
        except Exception as exc:
            logger.warning(f"[emotions] Failed to load model: {exc}")

    # ------------------------------------------------------------------
    @property
    def is_available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------
    def detect(self, text: str) -> dict[str, float]:
        """Return emotion probabilities for a single text."""
        if not self._available or not text or not text.strip():
            return {}
        return self.detect_batch([text])[0]

    def detect_batch(self, texts: list[str]) -> list[dict[str, float]]:
        """Return emotion probabilities for a batch of texts."""
        if not self._available:
            return [{} for _ in texts]

        import torch
        from scipy.special import softmax

        results: list[dict[str, float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            inputs = self._tokenizer(  # type: ignore[misc]
                batch,
                return_tensors="pt",
                truncation=True,
                max_length=512,
                padding=True,
            )
            with self._inference_lock, torch.no_grad():
                output = self._model(**inputs)  # type: ignore[misc]
            logits = output.logits.detach().cpu().numpy()

            for row in logits:
                probs = softmax(row)
                results.append(
                    {label: round(float(probs[i]), 4) for i, label in enumerate(EMOTION_LABELS)}
                )
        return results


# ── Module-level singleton ──────────────────────────────────────────
_detector: EmotionDetector | None = None
_detector_lock = Lock()


def get_emotion_detector() -> EmotionDetector:
    """Get or create the singleton emotion detector."""
    global _detector
    if _detector is not None:
        return _detector
    with _detector_lock:
        if _detector is not None:
            return _detector
        _detector = EmotionDetector()
        return _detector
