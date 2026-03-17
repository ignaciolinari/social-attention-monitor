# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.3] - 2026-03-08

### Added
- **Shared title-aware collection flow**: Live mention refresh and the scheduled collector now reuse the same title-context, query-building, title-match filtering, deduplication, and spam-filtering primitives.
- **Metrics approximation flags**: Metrics API responses now expose `mentions_capped`, `mentions_fetch_limit`, and `is_approximate`, and dashboard consumers surface warnings when a snapshot was computed from the 10k mention cap.

### Changed
- **Live mention refresh parity**: API-triggered refreshes now follow the same authoritative ingestion rules as scheduled collector runs, and stale DB-backed mention responses preserve the true `last_collected_at` timestamp instead of returning `now`.
- **Shared quota accounting**: Current-day YouTube quota usage now prefers shared Redis-backed state so API-driven live collection and the separate collector process see the same budget and `/api/v1/pipeline/quota` reports the same truth.
- **Runtime collector toggles**: YouTube/Bluesky toggles now require Redis persistence and return `503` if the toggle cannot be durably stored.
- **Health and startup contract**: `/health` remains a liveness/dependency report but can now return `healthy`, `degraded`, or `unhealthy`, while `/ready` is the strict readiness gate used by Docker healthchecks and the local launcher. One-command startup paths now apply migrations before the stack is treated as ready.

### Fixed
- **Title attribution safety**: `get_title_by_name()` now resolves only exact active title matches when `title_id` is omitted, and title upserts refresh `updated_at` so stale rows are not retired prematurely.
- **Short-title matching**: Ambiguous short titles no longer get an unconditional `score=1.0` substring match that bypasses threshold checks.
- **Bluesky identity stability**: Mentions now use the stable post URI instead of `cid`, so edits do not create duplicate identities.

### Documentation
- **Operational docs**: Updated the README, setup guide, API reference, and troubleshooting notes for the new readiness semantics, migration-on-start behavior, Redis-backed toggles/quota, and metrics approximation surfacing.

## [0.3.2] - 2026-03-07

### Added
- **Title Catalog page**: New dashboard tab for browsing the full DB-backed title list, with search, filter controls, trending badges, CSV export, and quick jumps into analysis tabs.

### Changed
- **Dashboard title pickers**: Title-driven analytics tabs now search beyond the current trending subset by using DB-backed selectors with media-type, status, and trending-only filters.
- **Trending badges**: Currently trending titles are now marked with `🔥` inside the broader DB-backed selectors and catalog results for easier scanning.
- **Dashboard theming**: Removed the custom sidebar light/dark toggle so the dashboard now follows Streamlit's native theme and user settings.

### Documentation
- **Dashboard docs**: Updated the README and feature guide to describe the expanded title browsing flow, the new Title Catalog page, the new selector filters, and the native-theme behavior.

## [0.3.1] - 2026-03-06

### Added
- **Executive Overview**: New dashboard landing page aggregating top titles, system health, recent alerts, and pipeline metrics.
- **System Health in pipeline**: `check_system_health()` now runs after each collector cycle; issues are broadcast via Redis Pub/Sub (throttled to once per 15 min per alert type).
- **System Health block on Alerts page**: Dashboard displays system-level issues (no ingest, collector failure, quota, Redis degraded) from `GET /api/v1/alerts/system-health`.
- **Share of Voice**: Per-title share (% of total mentions) shown on the Trending page.
- **CSV export**: Download buttons on Trending and Compare Titles pages for table export.
- **Box Office correlation**: Pearson and Spearman correlation coefficients between attention index and revenue, with sample size and interpretation.
- **Pipeline observability metrics**: `raw_storage_failures` and `mentions_capped_titles` now surfaced in quality metrics.
- **Bluesky retry**: Tenacity-based retry with exponential backoff for 429, 500, 502, 503, 504 errors.
- **Benchmark smoke test**: CI seeds test data and runs positive benchmark test with valid `title_id`.
- **System health broadcast throttling**: `publish_system_health_throttled()` in cache module; Redis-backed 15-min throttle per alert type.

### Changed
- **Smoke test startup**: Replaced fixed `sleep 5` with retry loop (up to 30s) waiting for API readiness.
- **Pre-commit mypy**: Uses `python -m mypy` instead of hardcoded `.venv/bin/python`.
- **Metrics snapshots**: `compute_and_upsert_metrics_snapshots_multi` now returns `(count, mentions_capped)`; runner tracks `mentions_capped_titles` in stats.
- **Attention Index param**: `_calculate_attention_index` parameter renamed from `sentiment_momentum` to `avg_sentiment` for clarity.
- **Dashboard default page**: Executive Overview is now the default/landing view.

### Fixed
- **Watchlist delete 404**: Use `get_json_nocache` for watchlists list to prevent stale cache showing already-deleted items and causing 404 on subsequent delete attempts.
- **Version display**: Bumped to 0.3.1 in `sam.__version__` and `pyproject.toml` so dashboard and API show correct version.

### Dashboard Display
- **Trending**: Added `hype_acceleration` and `share_of_voice_pct` columns.
- **Time Series**: Added hype acceleration and sentiment volatility charts.
- **Compare Titles**: Summary table now includes hype acceleration, sentiment volatility, negative ratio.
- **Alpha Metrics**: Author Diversity card now shows repeat author ratio when available.
- **Box Office**: Correlation metrics (Pearson, Spearman) before scatter plot.
- **Alerts**: System Health block above Recent Alerts.

### Documentation
- **Snapshot bucketing**: Expanded `_snapshot_bucket` docstring to document 5-min polls vs 30-min bucket coalescing behavior.

## [0.3.0] - 2026-03-05

### Added
- **Language detection pipeline**: `detect_languages()` in `enrichment.py` uses `langdetect` to identify mention languages (ISO-639-1). Wired into `runner.py` Phase 2b; `detected_language` column is now populated in the `mentions` table.
- **TMDB revenue/budget enrichment**: `enrich_titles_with_details()` fetches revenue and budget from TMDB's `/movie/{id}` detail endpoint for movie titles. Called automatically after `get_trending()` in the collector pipeline.
- **Box Office endpoint** (`GET /api/v1/metrics/box-office`): Scatter-plot data correlating attention index with TMDB revenue/budget.
- **Language Breakdown endpoint** (`GET /api/v1/metrics/language-breakdown`): Per-language mention counts and average sentiment for a title.
- **Compare endpoint** (`GET /api/v1/metrics/compare`): Parallel timeseries for 2–5 titles. Reports `missing_ids` for titles not found.
- **Benchmark endpoint** (`GET /api/v1/metrics/benchmark`): First-N-days trajectory comparison against averaged peers.
- **Watchlists CRUD**: `GET/POST/PUT/DELETE /api/v1/watchlists` with `Watchlist` model, Alembic migration, and Pydantic schemas.
- **Dashboard pages**: Box Office correlation, Language Segmentation, Historical Benchmark, Title Comparison, and Watchlists management pages in Streamlit.
- **Pydantic response models**: `BoxOfficeResponse`, `LanguageBreakdownResponse`, `CompareResponse`, `BenchmarkResponse` wired to route decorators for OpenAPI docs.
- **`delete_json` helper** in `dashboard/api_client.py` for consistency with `get_json`/`post_json`.
- **CI smoke tests**: GitHub Actions checks for box-office, watchlists, and benchmark endpoints.
- **Shared test fixtures**: `conftest.py` with `DummySession`, `DummyRedis`, and `client` fixture; duplicated boilerplate removed from 5 test files.
- **Watchlists utilized by collector**: Runner now fetches titles from all user watchlists (via `get_all_watchlist_tmdb_ids()`), resolves them through `tmdb.get_details()` (movie→tv fallback), deduplicates against trending, and processes them alongside trending titles.
- **Alembic migration** (`c6d7e8f9a0b1`): Adds `revenue`/`budget` columns to `titles` and `detected_language` to `mentions`.

### Changed
- **Benchmark endpoint**: Replaced fragile `[0.0]`/`[1]` average calculation with explicit logic; added `ORDER BY popularity DESC` for deterministic comparison selection; peer averages are now computed from per-title daily trajectories rather than raw snapshot density.
- **Watchlist model**: `tmdb_ids` type annotation corrected from `dict[str, Any]` to `list[int]`.
- **Watchlist update route**: Explicitly sets `updated_at = datetime.now(UTC)` to avoid SQLAlchemy JSONB mutation detection issues.
- **Dashboard watchlists page**: Refactored from raw `httpx` to shared `api_client` helpers and now supports edit/update flows in addition to create/delete.
- **Historical benchmark chart**: Both traces now use consistent day-level points and "Day N" x-axis labels.
- **Benchmark endpoint**: `comparison_limit` query parameter (default 20, max 50) replaces hardcoded `LIMIT 20`.
- **Language route**: `.as_string()` → `.astext` for standard SQLAlchemy JSONB access pattern.

### Fixed
- **TMDB revenue/budget always `NULL`**: `data.get('revenue') or None` treated `0` as falsy, converting valid TMDB data to `None`. Changed to `data.get('revenue')`.
- **Compare endpoint silently dropped missing titles**: Now returns `missing_ids` list in response.
- **Defensive `getattr`** removed from `box_office.py` and `dependencies.py` — `revenue`/`budget` are proper model attributes.
- **Watchlist dashboard create/update requests**: Mutating requests now send JSON bodies that match the FastAPI request models.
- **Watchlist auth coverage**: `POST /api/v1/watchlists` is now protected when `SAM_API_KEY` is configured.
- **Language breakdown**: Unknown `title_id` now returns `404` consistently with other title-scoped metrics endpoints.
- **Language breakdown neutral averages**: A real `0.0` average sentiment is preserved instead of being converted to `null`.
- **Language persistence**: Detected language now uses the same composite identity keying as sentiment to avoid cross-platform/source collisions.
- **Repository async mock warning**: Watchlist helper now tolerates async-mocked `result.all()` in tests without emitting unawaited coroutine warnings.
- **Migration docstring** `Revises:` corrected to match actual `down_revision`.
- **Sidebar**: Removed unused `is_light` variable assignment.
- **Plotly `dict()` calls**: Replaced with dict literals for linter compliance.

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

## [0.1.0] - Initial release

### Added

- Multi-platform collection: Reddit, YouTube, Bluesky, TMDB
- NLP pipeline: VADER and RoBERTa sentiment, translation, spam filtering, keyword extraction
- Metrics: Attention Index, Hype Acceleration, timeseries snapshots
- Anomaly detection and real-time WebSocket alerts
- FastAPI REST API and Streamlit dashboard
- Docker Compose stack
- GitHub Actions CI (lint, type check, tests, security audit)
