"""Title matching / entity resolution helpers.

Uses rapidfuzz for high-performance fuzzy matching with alias table support.
Falls back to difflib if rapidfuzz is not installed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from loguru import logger

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

# ── Title alias table ───────────────────────────────────────────────
# Maps common shorthand/abbreviations to canonical titles.
TITLE_ALIASES: dict[str, str] = {
    "tlou": "The Last of Us",
    "tlou2": "The Last of Us Season 2",
    "got": "Game of Thrones",
    "hotd": "House of the Dragon",
    "dune 2": "Dune: Part Two",
    "dune two": "Dune: Part Two",
    "wl": "The White Lotus",
    "white lotus": "The White Lotus",
    "sev": "Severance",
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


def _alias_matches(normalized_text: str, alias: str) -> bool:
    """Return True when alias matches full token(s) in normalized text."""
    alias_norm = normalize_title(alias)
    if not alias_norm:
        return False
    text_tokens = normalized_text.split()
    alias_tokens = alias_norm.split()
    if not alias_tokens:
        return False
    if len(alias_tokens) == 1:
        return alias_tokens[0] in text_tokens

    window_size = len(alias_tokens)
    for idx in range(len(text_tokens) - window_size + 1):
        if text_tokens[idx : idx + window_size] == alias_tokens:
            return True
    return False


# Select matching backend
try:
    from rapidfuzz.fuzz import ratio as _rapidfuzz_ratio

    def _ratio(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        return _rapidfuzz_ratio(a, b) / 100.0  # rapidfuzz returns 0-100

    _BACKEND = "rapidfuzz"
except ImportError:
    from difflib import SequenceMatcher

    def _ratio(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a, b).ratio()

    _BACKEND = "difflib"
    logger.info("[matching] rapidfuzz not installed, using difflib fallback")


def match_best(
    text: str,
    *,
    candidates: list[str],
    min_score: float = 0.72,
) -> MatchResult | None:
    """Return the best matching candidate title for `text`, or None if below threshold.

    Checks alias table first for exact matches before falling back to fuzzy matching.
    """
    nt = normalize_title(text)
    normalized_candidates = {normalize_title(candidate): candidate for candidate in candidates}

    # Check alias table first
    for alias, canonical in TITLE_ALIASES.items():
        canonical_candidate = normalized_candidates.get(normalize_title(canonical))
        if canonical_candidate and _alias_matches(nt, alias):
            return MatchResult(candidate=canonical_candidate, score=1.0)

    best: MatchResult | None = None
    for c in candidates:
        nc = normalize_title(c)
        score = 1.0 if nc and nc in nt else _ratio(nt, nc)
        if best is None or score > best.score:
            best = MatchResult(candidate=c, score=score)
    if best is None or best.score < min_score:
        return None
    return best
