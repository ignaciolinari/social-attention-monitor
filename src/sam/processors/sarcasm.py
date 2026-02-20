"""Sarcasm detection processor.

Flags sarcastic/ironic text so that downstream sentiment scores can be
interpreted more carefully.

Requires the ``transformers`` optional dependency.  When unavailable the
detector returns conservative defaults (not sarcastic).
"""

from __future__ import annotations

from threading import Lock
from typing import Any

from loguru import logger

SARCASM_MODEL = "helinivan/english-sarcasm-detector"
BATCH_SIZE = 32


class SarcasmDetector:
    """Detect sarcasm in text using a transformer classifier."""

    def __init__(self) -> None:
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._available = False
        self._load()

    def _load(self) -> None:
        try:
            import torch as _torch  # noqa: F401
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError:
            logger.info("[sarcasm] transformers not installed — sarcasm detection disabled")
            return

        try:
            logger.info(f"[sarcasm] Loading model: {SARCASM_MODEL}")
            self._tokenizer = AutoTokenizer.from_pretrained(SARCASM_MODEL)
            self._model = AutoModelForSequenceClassification.from_pretrained(SARCASM_MODEL)
            self._model.eval()
            self._available = True
            logger.info("[sarcasm] model loaded successfully")
        except Exception as exc:
            logger.warning(f"[sarcasm] Failed to load model: {exc}")

    @property
    def is_available(self) -> bool:
        return self._available

    def detect(self, text: str) -> tuple[bool, float]:
        """Return ``(is_sarcastic, confidence)`` for a single text."""
        if not self._available or not text or not text.strip():
            return (False, 0.0)
        return self.detect_batch([text])[0]

    def detect_batch(self, texts: list[str]) -> list[tuple[bool, float]]:
        """Return ``(is_sarcastic, confidence)`` for a batch of texts."""
        if not self._available:
            return [(False, 0.0) for _ in texts]

        import torch
        from scipy.special import softmax

        results: list[tuple[bool, float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            inputs = self._tokenizer(  # type: ignore[misc]
                batch,
                return_tensors="pt",
                truncation=True,
                max_length=512,
                padding=True,
            )
            with torch.no_grad():
                output = self._model(**inputs)  # type: ignore[misc]
            logits = output.logits.detach().cpu().numpy()

            for row in logits:
                probs = softmax(row)
                # Model labels: 0 = not sarcastic, 1 = sarcastic
                sarcasm_prob = float(probs[1]) if len(probs) > 1 else 0.0
                is_sarcastic = sarcasm_prob > 0.5
                results.append((is_sarcastic, round(sarcasm_prob, 4)))
        return results


# ── Module-level singleton ──────────────────────────────────────────
_detector: SarcasmDetector | None = None
_detector_lock = Lock()


def get_sarcasm_detector() -> SarcasmDetector:
    """Get or create the singleton sarcasm detector."""
    global _detector
    if _detector is not None:
        return _detector
    with _detector_lock:
        if _detector is not None:
            return _detector
        _detector = SarcasmDetector()
        return _detector
