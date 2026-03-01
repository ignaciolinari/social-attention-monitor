"""Keyword and hashtag extraction.

Uses YAKE (Yet Another Keyword Extractor) for unsupervised keyword
extraction from social media text.
"""

from __future__ import annotations

import functools
import random
import re
from collections import Counter
from typing import Any

from loguru import logger

_HASHTAG_RE = re.compile(r"#(\w+)")

# Maximum number of texts to feed into YAKE.  Keywords converge well with
# sampling, and this avoids O(n) string concatenation + YAKE memory usage
# on viral titles with thousands of mentions.
_MAX_KEYWORD_TEXTS = 500


@functools.lru_cache(maxsize=8)
def _get_yake_extractor(
    language: str, max_ngram: int, top_n: int, dedup_lim: float = 0.7
) -> Any:  # yake.KeywordExtractor
    """Return a cached YAKE extractor for the given parameters.

    Uses ``lru_cache`` with a bounded size (max 8 entries) to avoid
    unbounded memory growth while still caching the most common
    parameter combinations.
    """
    import yake  # noqa: F811

    return yake.KeywordExtractor(lan=language, n=max_ngram, top=top_n, dedupLim=dedup_lim)


def extract_keywords(
    texts: list[str],
    *,
    top_n: int = 15,
    language: str = "en",
    max_ngram: int = 2,
) -> list[tuple[str, float]]:
    """Extract top keywords from a collection of texts using YAKE.

    Args:
        texts: List of text documents to extract keywords from.
        top_n: Number of top keywords to return.
        language: Language code.
        max_ngram: Maximum n-gram size.

    Returns:
        List of ``(keyword, score)`` tuples.  Lower YAKE score = more relevant.
        Scores are inverted (1 - score) so higher = more relevant.
    """
    # Cap input size BEFORE joining to avoid O(n) string concatenation
    # on viral titles with thousands of mentions.  Keywords converge well
    # with sampling, so this doesn't meaningfully affect quality.
    if len(texts) > _MAX_KEYWORD_TEXTS:
        texts = random.sample(texts, _MAX_KEYWORD_TEXTS)

    combined = " ".join(t for t in texts if t and t.strip())
    if not combined.strip():
        return []

    try:
        extractor = _get_yake_extractor(language, max_ngram, top_n)
        raw = extractor.extract_keywords(combined)
        # Invert score so higher = more relevant (YAKE uses lower = better)
        return [(kw, round(1.0 - min(score, 1.0), 4)) for kw, score in raw]
    except ImportError:
        logger.warning("[keywords] yake not installed — keyword extraction disabled")
        return []
    except Exception as exc:
        logger.warning(f"[keywords] extraction failed: {exc}")
        return []


def extract_hashtags(texts: list[str], *, top_n: int = 20) -> list[tuple[str, int]]:
    """Extract and count hashtags from a collection of texts.

    Returns:
        List of ``(hashtag, count)`` tuples sorted by frequency.
    """
    counter: Counter[str] = Counter()
    for text in texts:
        if not text:
            continue
        for tag in _HASHTAG_RE.findall(text):
            counter[tag.lower()] += 1
    return counter.most_common(top_n)
