"""
FastAPI Application

Main API server for Social Attention Monitor.

Routes live in ``sam.api.routes.*``; shared state and helpers live in
``sam.api.dependencies``.  This module wires everything together: lifespan,
error handlers, CORS, middleware, and router inclusion.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.status import HTTP_422_UNPROCESSABLE_CONTENT

from sam import __version__

# Re-export dependency helpers so that ``from sam.api.main import ...``
# still works for test fixtures and the dashboard.
from sam.api.dependencies import (  # noqa: F401
    VALID_PLATFORMS,
    DbMentionsResult,
    close_collectors,
    init_collectors,
    is_collector_enabled,
)
from sam.api.metrics import (  # noqa: F401
    Timer,
    counter_inc,
    histogram_observe,
    prometheus_text,
)
from sam.api.middleware import RateLimiter, _path_requires_auth  # noqa: F401
from sam.api.routes.alerts import router as alerts_router
from sam.api.routes.benchmark import router as benchmark_router
from sam.api.routes.box_office import router as box_office_router
from sam.api.routes.collectors import router as collectors_router
from sam.api.routes.compare import router as compare_router

# Route modules
from sam.api.routes.health import router as health_router
from sam.api.routes.language import router as language_router
from sam.api.routes.mentions import router as mentions_router
from sam.api.routes.metrics_routes import router as metrics_router
from sam.api.routes.pipeline import router as pipeline_router
from sam.api.routes.sentiment import router as sentiment_router
from sam.api.routes.titles import router as titles_router
from sam.api.routes.trending import router as trending_router
from sam.api.routes.watchlists import router as watchlists_router
from sam.api.routes.ws import router as ws_router

# ---------------------------------------------------------------------------
# Backward-compatibility re-exports
# ---------------------------------------------------------------------------
# Existing code may ``from sam.api.main import …`` for schemas, websocket
# helpers, middleware, or metrics utilities.  Keep those import paths working.
from sam.api.schemas import (  # noqa: F401
    AlertAckResponse,
    AlertCountsResponse,
    AlertResponse,
    AlertsListResponse,
    ApiQuotaInfo,
    CollectorPlatformStatus,
    CollectorStatusResponse,
    DbTitleResponse,
    ErrorResponse,
    HealthResponse,
    MentionResponse,
    MentionsResponse,
    MetricsSnapshotResponse,
    MetricsTimeseriesResponse,
    PipelineHealthResponse,
    PipelineQuotaResponse,
    PipelineRunDetail,
    PipelineRunInfo,
    PipelineRunsResponse,
    PipelineSentimentStats,
    ReadinessResponse,
    TitleResponse,
    TitlesResponse,
    TrendingMetricsItem,
    TrendingMetricsResponse,
    TrendingResponse,
)
from sam.api.websocket import (  # noqa: F401
    ConnectionManager,
    broadcast_alert,
    broadcast_metrics_update,
    ws_manager,
)
from sam.config import get_settings, install_sighup_handler
from sam.logging import setup_logging

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler."""
    settings = get_settings()
    logger.info(f"[api] Starting SAM API v{__version__} (demo_mode={settings.demo_mode})")

    install_sighup_handler()
    await init_collectors()
    await ws_manager.start_cleanup_task(settings.ws_cleanup_interval_seconds)
    await ws_manager.start_alert_relay_task()

    yield

    logger.info("[api] Shutting down...")
    await ws_manager.stop_alert_relay_task()
    await ws_manager.stop_cleanup_task()
    await close_collectors()


# ---------------------------------------------------------------------------
# App creation
# ---------------------------------------------------------------------------

setup_logging()
app = FastAPI(
    title="Social Attention Monitor API",
    description="Real-time social attention tracking for film & TV releases",
    version=__version__,
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(error="http_error", detail=exc.detail).model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception(f"[api] unhandled error: {exc}")
    _settings = get_settings()
    detail = str(exc) if _settings.is_development else "An unexpected error occurred."
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(error="internal_server_error", detail=detail).model_dump(),
    )


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=HTTP_422_UNPROCESSABLE_CONTENT,
        content=ErrorResponse(error="validation_error", detail=str(exc)).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=HTTP_422_UNPROCESSABLE_CONTENT,
        content=ErrorResponse(error="request_validation_error", detail=exc.errors()).model_dump(),
    )


# ---------------------------------------------------------------------------
# CORS + Middleware
# ---------------------------------------------------------------------------

settings = get_settings()
cors_origins = settings.cors_allow_origins_list
if not settings.is_development and cors_origins == ["*"]:
    cors_origins = []

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from sam.api.middleware import register_middleware  # noqa: E402

register_middleware(app)

# ---------------------------------------------------------------------------
# Include routers
# ---------------------------------------------------------------------------

app.include_router(health_router)
app.include_router(pipeline_router)
app.include_router(collectors_router)
app.include_router(trending_router)
app.include_router(mentions_router)
app.include_router(sentiment_router)
app.include_router(titles_router)
app.include_router(metrics_router)
app.include_router(alerts_router)
app.include_router(ws_router)
app.include_router(box_office_router)
app.include_router(language_router)
app.include_router(compare_router)
app.include_router(watchlists_router)
app.include_router(benchmark_router)
