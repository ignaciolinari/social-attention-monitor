# System Architecture

Social Attention Monitor (SAM) uses a modular, near-real-time data pipeline architecture composed of several specialized services to track, process, and visualize engagement across social platforms.

## Overview

The core pipeline follows an Extract-Transform-Load (ETL) approach, enriched with real-time NLP processing:

```
[External Social APIs] (Reddit, YouTube, Bluesky, TMDB)
        ↓ (Polling)
[Collectors Layer] → Managed by APScheduler (every 5 mins)
        ↓
[Processors Layer] → Text cleaning, Spam filtering, multi-model Sentiment Analysis
        ↓
[Storage Layer]    → PostgreSQL + TimescaleDB (Time-series metrics)
        ↓
[FastAPI Server]   → REST endpoints + WebSocket real-time alerts
        ↓
[Streamlit App]    → Interactive Dashboard
```

---

## 1. Collectors Layer
The collector architecture is built around the `BaseCollector` interface, ensuring a consistent contract for fetching posts and comments.

- **Execution**: Managed by `APScheduler` in `runner.py`.
- **Concurrency**: Each title flows through one authoritative ingestion path. Bounded cross-title concurrency is configurable, and expensive sub-steps like YouTube comment expansion are parallelized separately.
- **Target Polling**: Polls external platform APIs at defined intervals (default: 5 minutes) for a dynamic list of tracked titles.

*(See [Features Documentation](features.md) for details on supported platforms.)*

---

## 2. Processors Layer
Data flows from collectors through a sequence of processing modules before storage:

1. **Text Cleaning**: Removes URLs, HTML tags, and normalizing whitespace.
2. **Spam Detection**: Rule-based and heuristic filtering to drop likely bot or spam content.
3. **Sentiment Analysis**:
   - Primary: VADER (fast, rules-based).
   - Secondary: RoBERTa (transformer-based, deep contextual understanding).
   - Translation: Non-English text can optionally be translated to English before sentiment scoring, but the translation path is provider-gated and disabled by default for the hot ingestion path.
4. **Advanced NLP (Optional)**:
   - Sarcasm Detection
   - Emotion Classification (Joy, Anger, Sadness, etc.)
   - Keyword / Topic Extraction
5. **Metrics Calculation**: Aggregates individual posts into time-windowed snapshots (calculating moving averages, velocities, and composite scores like Attention Index).

---

## 3. Storage & Caching Layer

### Primary Database (PostgreSQL)
- **Time-Series Optimization**: Leverages TimescaleDB extensions when available for efficient storage and querying of high-volume sentiment snapshots over time. TimescaleDB is *optional*—migrations detect if the extension is preloaded and skip gracefully on plain PostgreSQL (e.g. CI, simple dev setups).
- **ORM**: SQLAlchemy 2.0 (asyncio).
- **Alembic**: Used for schema migrations.
- **Lease & PipelineRun Models**: Distributed lease records prevent concurrent collector runs across instances; pipeline run history tracks execution stats for observability.

### Cache Engine (Redis)
Redis serves three crucial functions:
1. **API Caching**: Caches intense DB queries (e.g., trending titles, search, and aggregated metrics) with configurable TTLs.
2. **Distributed Toggles**: Shares feature flags (like `YOUTUBE_ENABLED`) safely across separate processes (e.g., FastAPI vs. the Scheduler).
3. **Event Fanout Pub/Sub**: Facilitates system-wide distribution of anomaly alerts and metrics updates before they push to connected WebSocket clients. System health alerts are throttled (15 min per type) via Redis keys to avoid spamming clients.

---

## 4. API Layer (FastAPI)
The backend service exposes data to the dashboard and external clients. The API is structured into modular route files under `api/routes/` with shared logic in `api/dependencies.py`.
- **REST Endpoints**: Serves metrics, configuration states, platform quotas, mentions, and system health via route modules (`health`, `pipeline`, `collectors`, `mentions`, `metrics_routes`, `box_office`, `language`, `compare`, `benchmark`, `watchlists`, `alerts`, `sentiment`, `titles`, `trending`, `ws`).
- **Middleware** (`api/middleware.py`): API key authentication for mutation endpoints (`POST`/`PUT`/`DELETE` under `/api/*`) and selected expensive/probe endpoints, plus sliding-window rate limiting (in-memory with Redis upgrade path).
- **Prometheus Metrics**: `/metrics` endpoint for operational monitoring.
- **WebSockets (`/ws`)**: Pushes real-time alerting anomalies and metrics updates to active clients.

---

## 5. Presentation Layer (Streamlit)
The dashboard provides operational observability, structured as modular pages under `dashboard/pages/` with shared helpers in `dashboard/api_client.py`, `dashboard/helpers.py`, and `dashboard/sidebar.py`.
- **Multipage Navigation**: Sidebar-driven navigation across 16 pages, including Executive Overview (landing), Compare Titles, Box Office (with correlation coefficients), Language Segmentation, Historical Benchmark, and Watchlists. CSV export available on Trending and Compare tables.
- **State Management**: Utilizes Streamlit's `st.session_state` to decouple heavy API calls from rapid UI redraws.
