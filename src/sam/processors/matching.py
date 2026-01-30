"""
Title matching / entity resolution helpers.

This is a lightweight foundation implementation (no extra deps):
- normalize text aggressively (casefold, strip punctuation)
- fuzzy match via difflib.SequenceMatcher

For higher quality later, consider swapping to rapidfuzz and adding alias tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]+")
_WS_RE = re.compile(r"\s+")
_SEASON_RE = re.compile(r"\b(season|s)\s*(\d+)\b", re.IGNORECASE)
_NUM_WORDS: dict[str, str] = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
    "10": "ten",
}


def normalize_title(text: str) -> str:
    """Normalize a title-ish string for fuzzy matching."""
    text = text.casefold()
    text = _SEASON_RE.sub(r" s\2", text)
    text = _NON_ALNUM_RE.sub(" ", text)
    # Map common small integers to words to reduce sequel mismatch (e.g. "part 2" vs "part two").
    tokens = [_NUM_WORDS.get(t, t) for t in text.split()]
    text = " ".join(tokens)
    text = _WS_RE.sub(" ", text).strip()
    return text


@dataclass(frozen=True)
class MatchResult:
    """Result of matching some text to a candidate title."""

    candidate: str
    score: float  # 0.0 .. 1.0


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def match_best(
    text: str,
    *,
    candidates: list[str],
    min_score: float = 0.72,
) -> MatchResult | None:
    """
    Return the best matching candidate title for `text`, or None if below threshold.
    """
    nt = normalize_title(text)
    best: MatchResult | None = None
    for c in candidates:
        nc = normalize_title(c)
        score = 1.0 if nc and nc in nt else _ratio(nt, nc)
        if best is None or score > best.score:
            best = MatchResult(candidate=c, score=score)
    if best is None or best.score < min_score:
        return None
    return best
