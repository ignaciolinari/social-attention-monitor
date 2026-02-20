"""Keyword and hashtag extraction.

Uses YAKE (Yet Another Keyword Extractor) for unsupervised keyword
extraction from social media text.
"""

from __future__ import annotations

import re
from collections import Counter

from loguru import logger

_HASHTAG_RE = re.compile(r"#(\w+)")


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
    combined = " ".join(t for t in texts if t and t.strip())
    if not combined.strip():
        return []

    try:
        import yake

        extractor = yake.KeywordExtractor(
            lan=language,
            n=max_ngram,
            top=top_n,
            dedupLim=0.7,
        )
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
