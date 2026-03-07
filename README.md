# Social Attention Monitor (SAM)

[![CI](https://github.com/ignaciolinari/social-attention-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/ignaciolinari/social-attention-monitor/actions) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT) [![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/) [![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=flat&logo=fastapi)](https://fastapi.tiangolo.com/) [![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?style=flat&logo=streamlit&logoColor=white)](https://streamlit.io/) [![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=flat&logo=postgresql&logoColor=white)](https://www.postgresql.org/) [![Docker](https://img.shields.io/badge/Docker-2CA5E0?style=flat&logo=docker&logoColor=white)](https://www.docker.com/)

**Supported Platforms (tested):**<br>
![TMDB](https://img.shields.io/badge/TMDB-01B4E4?style=for-the-badge&logo=themoviedb&logoColor=white) ![YouTube](https://img.shields.io/badge/YouTube-FF0000?style=for-the-badge&logo=youtube&logoColor=white) ![Bluesky](https://img.shields.io/badge/Bluesky-0285FF?style=for-the-badge&logo=bluesky&logoColor=white) <br>
<br>
**Supported Platforms (not tested):**<br>
![Reddit](https://img.shields.io/badge/Reddit-FF4500?style=for-the-badge&logo=reddit&logoColor=white) *(implemented, missing API keys)* <br>
<br>
**Pending Integration:**<br>
![X](https://img.shields.io/badge/X-000000?style=for-the-badge&logo=x&logoColor=white)

A near–real-time data pipeline that monitors social engagement and public sentiment around newly released movies and TV series.

<!-- ![Dashboard Preview](docs/assets/dashboard_demo.webp) -->

### Dashboard Highlights

**Executive Overview:** At-a-glance landing page with top titles, system health, recent alerts, and pipeline metrics.

**Trending Now:** Monitor real-time traction, share of voice, and hype acceleration. Export to CSV.
<!-- ![Trending Now Snapshot](docs/assets/tab_trending.png) -->

**Sentiment Analysis:** Understand public sentiment using VADER & RoBERTa models.
<!-- ![Sentiment Analysis Snapshot](docs/assets/tab_sentiment.png) -->

**Live Alerts:** Catch viral hype spikes and sentiment shifts the moment they happen. Includes system health monitoring.
<!-- ![Live Alerts Snapshot](docs/assets/tab_alerts.png) -->

## Overview

SAM is a highly configurable ETL and Processing pipeline that tracks trending media from **TMDB**, polls social platforms (**Reddit, YouTube, and Bluesky**) for mentions, processes text through advanced **NLP Sentiment Engines**, and visualizes attention metrics in a unified **Interactive Dashboard**.

### API Access & Compliance

The pipeline is designed to operate **fully within official APIs and their terms of service**. It uses YouTube (with built-in quota limits), Bluesky (generous limits), and TMDB (liberal usage). Reddit only with approved access. Twitter/X could be added with paid API access. The architecture can also be adapted to ingest externally sourced or scraped data, but responsibility for compliance with applicable laws and platform ToS lies with the operator.

## Key Features

- **Multi-Platform Collection**: Native support for Reddit, YouTube, TMDB, and Bluesky with per-platform circuit breakers.
- **Advanced NLP Pipeline**: Dual-engine sentiment (VADER & RoBERTa), aspect-based sentiment, emotion classification, sarcasm detection, content deduplication, and auto-translation.
- **Metric Computation**: Intelligent scoring via "Attention Index" and "Hype Acceleration", plus alpha metrics (Audience Fatigue, Viral Coefficient, Sentiment Divergence).
- **Box Office Correlation**: Automatic revenue/budget data from TMDB, scatter-plot analysis of social attention vs. commercial performance.
- **Language Segmentation**: Automatic language detection on mentions with per-language sentiment breakdowns.
- **Comparative Title Analytics**: Side-by-side comparison of up to 5 titles with overlaid attention, velocity, and sentiment charts.
- **User-Defined Watchlists**: Create, edit, and delete persistent watchlists to track custom sets of titles beyond TMDB trending.
- **Historical Benchmarking**: Compare a title's early day-level trajectory against averaged daily peer performance.
- **Dark/Light Mode**: Toggle between dark and light themes in the dashboard.
- **Real-Time Alerting**: Statistical anomaly detection for mention spikes and viral breakouts pushed instantly via WebSockets.
- **Security**: Optional API key authentication for mutation and expensive endpoints, with sliding-window rate limiting.
- **Observability**: Prometheus-compatible `/metrics` endpoint, pipeline self-health alerts (run every cycle, throttled broadcast), per-title timing, and structured log correlation IDs.
- **Resilience**: Title quarantine for failing titles, SIGHUP hot-reload, translation timeouts, and graceful collector degradation.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, code style, and pull request workflow.

## Documentation

Comprehensive guides on the internals, configuration, and API:

- **[System Architecture](docs/architecture.md)**: Details on the Collector -> Processor -> Storage -> API flow.
- **[Feature Deep-Dive](docs/features.md)**: NLP capabilities, platform tracking, alert specifications, and Dashboard tabs.
- **[Setup & Configuration](docs/setup.md)**: Local macOS, Docker Compose, and environment variable references.
- **[Troubleshooting](docs/troubleshooting.md)**: Common issues and FAQ.
- **[API Reference](docs/api_reference.md)**: REST endpoints, Rate Limiting, and WebSockets.

## Requirements

- **Python 3.11+**
- PostgreSQL 16 (with optional TimescaleDB)
- Redis

## Quick Start (Demo Mode)

You can run the full pipeline instantly using mock data (no API keys required).

```bash
# 1. Clone and install
git clone https://github.com/ignaciolinari/social-attention-monitor.git
cd social-attention-monitor
pip install -e ".[dev]"

# 2. Start PostgreSQL (+ TimescaleDB) and Redis via Docker Compose
make db-up
alembic upgrade head

# 3. Start the API Server
make run-api

# 4. In a new terminal, start the Interactive Dashboard
make run-dashboard

# 5. In a third terminal, start the Background Collector
make run-collector
```

Then visit:
- **API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Dashboard**: [http://localhost:8501](http://localhost:8501)

### One-Command Local Launcher (venv + Docker infra)

If you prefer one command for local development:

```bash
./run-all-local.sh start
```

Useful companion commands:

```bash
./run-all-local.sh status
./run-all-local.sh logs
./run-all-local.sh stop
```

This launcher starts:
- `postgres` + `redis` via Docker Compose
- `api` + `dashboard` + `collector` via `.venv`

### Dashboard-Only Mode (No New DB Population)

If you want to inspect the dashboard without ingesting fresh data, start API + dashboard only and leave the collector off:

```bash
make db-up
make run-api
make run-dashboard
# do not run: make run-collector
```

> [!NOTE]
> For full live data setup with actual API keys for Reddit/YouTube/Bluesky, refer to the [Setup Guide](docs/setup.md).

## Project Structure

```text
social-attention-monitor/
├── src/
│   ├── sam/
│   │   ├── alerts/        # Anomaly detection & WebSocket broadcasting
│   │   ├── api/           # FastAPI (routes/, schemas, middleware, metrics)
│   │   ├── collectors/    # Platform integrations (Reddit, YouTube, Bluesky, TMDB)
│   │   ├── pipeline/      # Shared enrichment & metrics snapshot logic
│   │   ├── processors/    # NLP (sentiment, emotions, sarcasm, spam, keywords)
│   │   ├── storage/       # PostgreSQL models, repository & Alembic migrations
│   │   ├── scheduler/     # APScheduler runner for periodic ETL
│   │   └── utils/         # Translation, shared helpers
│   └── dashboard/         # Streamlit app (pages/, sidebar, api_client)
├── docs/                  # Architecture, features, setup, API reference
├── tests/                 # Unit & integration tests (400+)
└── docker-compose.yml     # Container orchestration
```

## CI/CD Pipeline

The project uses GitHub Actions to enforce quality:
- code linting (`ruff`)
- static type checking (`mypy`)
- tested coverage (`pytest`) with Codecov upload (`fail_ci_if_error: true`)
- dependency security audits (`pip-audit`)
- end-to-end smoke test (Postgres + Redis + API + collector seed + benchmark)

## License

MIT
