# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-02-28

### Added
- **API modularization**: Split monolithic `api/main.py` into `api/routes/*`, `api/middleware.py`, `api/dependencies.py`, `api/schemas.py`, `api/websocket.py`, and `api/metrics.py`.
- **Dashboard modularization**: Split `dashboard/app.py` into `pages/*`, `api_client.py`, `helpers.py`, `sidebar.py`, and `sentiment_analysis.py`.
- **Shared enrichment module** (`sam.pipeline.enrichment`): Single source of truth for sentiment analysis + NLP enrichment used by both runner and API.
- **API key authentication**: Mutation/admin endpoints are now protected when `SAM_API_KEY` is set (collectors toggle, alert acknowledge, run-detection, sentiment analyze, PUT/DELETE).
- **Rate limiting**: Sliding-window rate limiter (in-memory with Redis upgrade path); `/health`, `/metrics`, and pipeline health excluded.
- **Title quarantine**: Dead-letter queue for titles that fail 3+ consecutive times, with Redis-backed per-title failure tracking.
- **Per-title timing** (`per_title_ms`): Runner now persists per-title elapsed milliseconds in pipeline run stats for the Pipeline Observability dashboard.
- **Prometheus metrics**: `/metrics` endpoint with `collection_cycles_total`, `mentions_inserted_total`, `sentiment_analysis_seconds`, and more.
- **Pipeline self-health**: `AlertManager.check_system_health()` exposed at `GET /api/v1/alerts/system-health`.
- **Pipeline runs endpoint**: `GET /api/v1/pipeline/runs` with pagination and status filtering.
- **Log correlation**: Runner binds `run_id` and `title` to all log messages via `logger.contextualize()`.
- **SIGHUP config reload**: `install_sighup_handler()` clears `get_settings` cache on SIGHUP.
- **DB index**: `ix_mentions_title_source_type` on `(title_id, source_type)` with Alembic migration.
- **Aspect sentiment**: Wired into enrichment pipeline for long-form content (>100 chars).
- **Content deduplication**: `detect_duplicate_content` integrated in runner before persistence.
- **VADER parallelism**: `ProcessPoolExecutor` for VADER batches ≥64 texts.
- **Dashboard API key support**: `api_client.py` sends `X-API-Key` header when `SAM_API_KEY` is set.

### Changed
- **Docker**: Non-root user, `HEALTHCHECK` pointing to `/health`, `restart: unless-stopped`.
- **Docker Compose**: Environment variables use `${VAR:-default}` syntax; dashboard `depends_on: api: condition: service_healthy`.
- **Mention endpoints**: Consolidated into `GET /api/v1/mentions/{platform}` with backward-compatible `/mentions/reddit`, `/mentions/youtube`, `/mentions/bluesky` aliases.
- **Projection query**: `get_mentions_in_window_lightweight()` for metrics snapshot computation (avoids heavy ORM load).
- **Keyword sampling**: Capped at 500 texts via `random.sample()`.
- **Translation**: Per-language locks and retry logic replace global serialization lock.
- **NLP concurrency**: `_inference_lock` / `_roberta_lock` for thread-safe transformer inference.
- **ORM cleanup**: `cleanup_stale_state()` refactored from raw SQL to ORM statements.
- **Redis close**: Hardened with try/except/finally for event loop teardown safety.
- **Collector shutdown**: Per-collector `contextlib.suppress(Exception)` in `close_collectors()`.
- **Runner metrics import**: Narrowed to `except ImportError`.
- **Pipeline runs limit**: Route and repository aligned at max 100.
- **`content_truncated`**: Added to `MentionResponse` schema.
- **Documentation**: `features.md` updated with Security, Observability, and Resilience sections; `README.md` Key Features and project structure reflect modular architecture; `architecture.md`, `setup.md`, and `api_reference.md` updated for auth and new endpoints.

### Removed
- Dead code: `MetricsCalculator.detect_anomaly`, `calculate_platform_normalized`, `_bool_flag`.

### Fixed
- Dockerfile healthcheck pointed to wrong path (`/api/v1/health` → `/health`).
- Mention alias functions were dead code (missing `@router.get` decorators).
- Redis degraded warnings now rate-limited to prevent log spam.
- Collector `close()` methods guard against `None` client.
- `test_repository_unit` RuntimeWarning from unawaited `session.add` coroutine.

## [0.1.0] - Unreleased

### Added

- Multi-platform collection: Reddit, YouTube, Bluesky, TMDB
- NLP pipeline: VADER and RoBERTa sentiment, translation, spam filtering, keyword extraction
- Metrics: Attention Index, Hype Acceleration, timeseries snapshots
- Anomaly detection and real-time WebSocket alerts
- FastAPI REST API and Streamlit dashboard
- Docker Compose stack
- GitHub Actions CI (lint, type check, tests, security audit)
