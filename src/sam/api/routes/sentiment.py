"""Sentiment analysis endpoint."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query

from sam.api import dependencies as deps

router = APIRouter(prefix="/api/v1/sentiment", tags=["sentiment"])


@router.get("/analyze")
async def analyze_text_sentiment(
    text: str = Query(..., min_length=1, max_length=5000, description="Text to analyze"),
) -> dict[str, Any]:
    """Analyze sentiment of arbitrary text."""
    result = await asyncio.to_thread(deps.analyze_sentiment, text)
    payload = deps.sentiment_payload(result)
    return {
        "text": text[:100] + "..." if len(text) > 100 else text,
        "sentiment": payload,
        "model": result.model,
    }
