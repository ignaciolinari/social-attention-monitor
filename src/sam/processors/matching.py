"""Title matching / entity resolution helpers.

Uses rapidfuzz for high-performance fuzzy matching with alias table support.
Falls back to difflib if rapidfuzz is not installed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

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

_STOPWORD_TITLES = {"it", "you", "us", "her", "them", "life", "dark", "victory"}


def _coerce_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def normalize_title(text: str) -> str:
    """Normalize a title-ish string for fuzzy matching."""
    text = _coerce_text(text)
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


def build_title_candidates(title: str, original_title: str | None = None) -> list[str]:
    """Return a deduplicated list of candidate title strings."""
    candidates: list[str] = []
    seen: set[str] = set()
    for raw in (_coerce_text(title), _coerce_text(original_title)):
        if not raw:
            continue
        normalized = normalize_title(raw)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(raw)
    return candidates


def title_match_threshold(title: str, *, original_title: str | None = None) -> float:
    """Return a stricter threshold for shorter or ambiguous titles."""
    candidates = build_title_candidates(title, original_title)
    lengths = [len(normalize_title(candidate).split()) for candidate in candidates]
    shortest = min(lengths, default=1)
    primary = normalize_title(_coerce_text(title))
    if primary in _STOPWORD_TITLES:
        return 0.96
    if shortest <= 1:
        return 0.92
    if shortest == 2:
        return 0.82
    return 0.72


def build_search_query(
    title: str,
    *,
    original_title: str | None = None,
    media_type: str | None = None,
    release_date: datetime | None = None,
) -> str:
    """Build a search query that carries more context than the raw title."""
    primary = _coerce_text(title).strip()
    if not primary:
        return ""

    parts = [f'"{primary}"']
    if media_type == "movie":
        parts.append("movie")
    elif media_type == "tv":
        parts.append("series")

    if isinstance(release_date, datetime):
        parts.append(str(release_date.year))

    normalized_primary = normalize_title(primary)
    safe_original_title = _coerce_text(original_title)
    normalized_original = normalize_title(safe_original_title)
    if normalized_original and normalized_original not in normalized_primary:
        parts.append(f'"{safe_original_title}"')

    return " ".join(parts)


def match_title_text(
    text: str,
    *,
    title: str,
    original_title: str | None = None,
    min_score: float | None = None,
) -> MatchResult | None:
    """Match text against a title/original-title pair."""
    threshold = (
        min_score
        if min_score is not None
        else title_match_threshold(_coerce_text(title), original_title=_coerce_text(original_title))
    )
    return match_best(
        text,
        candidates=build_title_candidates(_coerce_text(title), _coerce_text(original_title)),
        min_score=threshold,
    )


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


def _candidate_matches(normalized_text: str, candidate: str) -> bool:
    """Return True when a candidate appears as full token(s) in text."""
    candidate_norm = normalize_title(candidate)
    if not candidate_norm:
        return False
    text_tokens = normalized_text.split()
    candidate_tokens = candidate_norm.split()
    if not candidate_tokens:
        return False
    if len(candidate_tokens) == 1:
        return candidate_tokens[0] in text_tokens

    window_size = len(candidate_tokens)
    for idx in range(len(text_tokens) - window_size + 1):
        if text_tokens[idx : idx + window_size] == candidate_tokens:
            return True
    return False


# Select matching backend
try:
    from rapidfuzz.fuzz import ratio as _rapidfuzz_ratio

    def _ratio(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        return float(_rapidfuzz_ratio(a, b) / 100.0)  # rapidfuzz returns 0-100

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
        if (
            nt == nc
            or _candidate_matches(nt, c)
            and (len(nc.split()) > 1 or nc not in _STOPWORD_TITLES)
        ):
            score = 1.0
        else:
            score = _ratio(nt, nc)
        if best is None or score > best.score:
            best = MatchResult(candidate=c, score=score)
    if best is None or best.score < min_score:
        return None
    return best
