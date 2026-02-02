"""Text cleaning helpers.

This module centralizes text normalization/cleanup used across the pipeline.
It intentionally keeps the cleaning lightweight and dependency-free.
"""

from __future__ import annotations

import html
import re

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[(?P<text>[^\]]+?)\]\((?P<url>[^\)]+?)\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_REDDIT_QUOTE_RE = re.compile(r"(?m)^\s*(?:&gt;|>).*?$")
_SUBREDDIT_RE = re.compile(r"/r/\w+", re.IGNORECASE)
_USER_RE = re.compile(r"/u/\w+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def clean_text_for_nlp(text: str) -> str:
    """Clean text for downstream NLP steps (matching, embeddings, etc).

    Compared to sentiment cleaning, this is slightly more aggressive:
    - strips HTML tags
    - strips URLs
    - unwraps Markdown links
    - removes Reddit quote blocks
    - collapses whitespace

    It does *not* attempt stemming/lemmatization.
    """
    if not text:
        return ""

    t = html.unescape(text)
    t = _MD_LINK_RE.sub(lambda m: m.group("text"), t)
    t = _REDDIT_QUOTE_RE.sub("", t)
    t = _URL_RE.sub("", t)
    t = _SUBREDDIT_RE.sub("", t)
    t = _USER_RE.sub("", t)
    t = _HTML_TAG_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t


def clean_text_for_sentiment(text: str) -> str:
    """Clean text for sentiment analysis.

    Keeps punctuation/emojis (important for VADER) while removing noisy markup.
    """
    # For now, sentiment cleaning matches NLP cleaning except it keeps any punctuation/emojis
    # by not doing any character filtering beyond markup removal.
    return clean_text_for_nlp(text)
