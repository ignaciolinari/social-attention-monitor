"""Collector status and toggle endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from sam.api import dependencies as deps
from sam.api.schemas import CollectorPlatformStatus, CollectorStatusResponse

router = APIRouter(prefix="/api/v1/collectors", tags=["collectors"])


@router.get("/status", response_model=CollectorStatusResponse)
async def collectors_status() -> CollectorStatusResponse:
    """Get enabled/disabled status of all collector platforms."""
    platforms = []
    for name in deps.TOGGLEABLE_PLATFORMS | {"reddit"}:
        enabled = await deps.is_collector_enabled(name)
        api_ok = deps.api_keys_configured(name)
        toggleable = name in deps.TOGGLEABLE_PLATFORMS
        message = None
        if name == "reddit":
            message = (
                "Reddit API access denied. Set REDDIT_ENABLED=true in .env "
                "with valid API keys to enable."
            )
        platforms.append(
            CollectorPlatformStatus(
                platform=name,
                enabled=enabled,
                api_configured=api_ok,
                toggleable=toggleable,
                message=message,
            )
        )
    return CollectorStatusResponse(collectors=platforms)


@router.put("/{platform}/toggle")
async def toggle_collector(
    platform: str,
    enabled: bool = Query(..., description="Enable or disable the collector"),
) -> CollectorPlatformStatus:
    """Toggle a collector on or off at runtime."""
    if platform not in deps.TOGGLEABLE_PLATFORMS | {"reddit"}:
        raise HTTPException(status_code=404, detail=f"Unknown platform: {platform}")

    if platform == "reddit":
        raise HTTPException(
            status_code=403,
            detail=(
                "Reddit cannot be toggled from the dashboard. "
                "Set REDDIT_ENABLED=true in .env with valid API keys to enable."
            ),
        )

    from sam.cache import collector_toggle_set

    persisted = await collector_toggle_set(platform, enabled)
    if not persisted:
        raise HTTPException(
            status_code=503,
            detail="Runtime collector toggles require Redis to be available.",
        )

    deps.set_collector_override(platform, enabled)
    logger.info(f"[api] Collector '{platform}' toggled to enabled={enabled}")

    api_ok = deps.api_keys_configured(platform)
    return CollectorPlatformStatus(
        platform=platform,
        enabled=enabled,
        api_configured=api_ok,
        toggleable=True,
    )
