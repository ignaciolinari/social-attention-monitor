"""Spam / bot detection heuristics.

Lightweight, rule-based scoring to filter low-quality content before
sentiment analysis.  No ML models — just pattern matching.
"""

from __future__ import annotations

import re

from sam.collectors.base import CollectedPost

# ── Patterns ────────────────────────────────────────────────────────
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_PROMO_RE = re.compile(
    r"(subscribe|follow me|check out my|link in bio|use code|discount|giveaway"
    r"|download now|click here|free trial|sign up)",
    re.IGNORECASE,
)
_EMOJI_SPAM_RE = re.compile(
    r"([\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
    r"\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF]){5,}",
)
_HASHTAG_RE = re.compile(r"#\w+")

# Thresholds
SPAM_THRESHOLD = 0.6


def compute_spam_score(post: CollectedPost) -> float:
    """Return a 0.0–1.0 spam likelihood score for a post.

    Higher = more likely spam.
    """
    text = post.content or ""
    if not text.strip():
        return 0.0

    score = 0.0
    length = len(text)

    # 1. URL density
    urls = _URL_RE.findall(text)
    url_char_count = sum(len(u) for u in urls)
    if length > 0 and url_char_count / length > 0.4:
        score += 0.3

    # 2. Promotional language
    promo_matches = _PROMO_RE.findall(text)
    if promo_matches:
        score += min(0.3, len(promo_matches) * 0.1)

    # 3. Excessive emoji
    if _EMOJI_SPAM_RE.search(text):
        score += 0.15

    # 4. ALL-CAPS ratio
    alpha_count = sum(1 for c in text if c.isalpha())
    if alpha_count > 20:
        caps_count = sum(1 for c in text if c.isupper())
        caps_ratio = caps_count / alpha_count
        if caps_ratio > 0.7:
            score += 0.15

    # 5. Very short + many hashtags
    hashtags = _HASHTAG_RE.findall(text)
    if len(hashtags) > 5 and length < 200:
        score += 0.2

    # 6. Very short content (< 10 chars)
    stripped = text.strip()
    if len(stripped) < 10:
        score += 0.1

    return min(1.0, score)


def is_likely_spam(post: CollectedPost) -> tuple[bool, float]:
    """Return ``(is_spam, score)`` for a single post."""
    score = compute_spam_score(post)
    return (score >= SPAM_THRESHOLD, round(score, 4))


def filter_spam(
    posts: list[CollectedPost],
    threshold: float = SPAM_THRESHOLD,
) -> tuple[list[CollectedPost], int]:
    """Filter out likely spam posts.

    Returns (filtered_posts, spam_count).
    """
    kept: list[CollectedPost] = []
    spam_count = 0
    for post in posts:
        score = compute_spam_score(post)
        if score < threshold:
            kept.append(post)
        else:
            spam_count += 1
    return kept, spam_count


def detect_duplicate_content(posts: list[CollectedPost]) -> set[int]:
    """Return indices of posts with duplicate content from different authors.

    These are likely bot-reposted content.
    """
    content_map: dict[str, list[int]] = {}
    for i, post in enumerate(posts):
        key = (post.content or "").strip().lower()[:200]
        if key:
            content_map.setdefault(key, []).append(i)

    duplicate_indices: set[int] = set()
    for _key, indices in content_map.items():
        if len(indices) > 1:
            # Keep the first occurrence, mark the rest as duplicates
            authors = {posts[i].author for i in indices}
            if len(authors) > 1:
                # Same content from different authors = bot
                duplicate_indices.update(indices[1:])
    return duplicate_indices
