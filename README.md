# Social Attention Monitor (SAM)

[![CI](https://github.com/ignaciolinari/social-attention-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/ignaciolinari/social-attention-monitor/actions) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A near–real-time data pipeline that monitors social engagement and public sentiment around newly released movies and TV series.

<!-- ![Dashboard Preview](docs/assets/dashboard_demo.webp) -->

### Dashboard Highlights

**Trending Now:** Monitor real-time traction and acceleration of social discussions.
<!-- ![Trending Now Snapshot](docs/assets/tab_trending.png) -->

**Sentiment Analysis:** Understand public sentiment using VADER & RoBERTa models.
<!-- ![Sentiment Analysis Snapshot](docs/assets/tab_sentiment.png) -->

**Live Alerts:** Catch viral hype spikes and sentiment shifts the moment they happen.
<!-- ![Live Alerts Snapshot](docs/assets/tab_alerts.png) -->

## Overview

SAM is a highly configurable ETL and Processing pipeline that tracks trending media from **TMDB**, polls social platforms (**Reddit, YouTube, and Bluesky**) for mentions, processes text through advanced **NLP Sentiment Engines**, and visualizes attention metrics in a unified **Interactive Dashboard**.

### API Access & Compliance

The pipeline is designed to operate **fully within official APIs and their terms of service**. It uses YouTube (with built-in quota limits), Bluesky (generous limits), and TMDB (liberal usage). Reddit only with approved access. Twitter/X could be added with paid API access. The architecture can also be adapted to ingest externally sourced or scraped data, but responsibility for compliance with applicable laws and platform ToS lies with the operator.

## Key Features

- **Multi-Platform Collection**: Native support for Reddit, YouTube, TMDB, and Bluesky.
- **Advanced NLP Processors**: Dual-engine sentiment analysis (VADER & RoBERTa), translation fallbacks, emotion classification, and sarcasm detection.
- **Metric Computation**: Intelligent scoring via "Attention Index" and "Hype Acceleration".
- **Real-Time Alerting**: Statistical anomaly detection for mention spikes and viral breakouts pushed instantly via WebSockets.
- **API Quota Management**: Built-in limits tracking for external platforms (e.g., YouTube Daily Budget protection).

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

> [!NOTE]
> For full live data setup with actual API keys for Reddit/YouTube/Bluesky, refer to the [Setup Guide](docs/setup.md).

## Project Structure

```text
social-attention-monitor/
├── src/
│   ├── sam/
│   │   ├── alerts/        # Anomaly detection & WebSockets
│   │   ├── collectors/    # Polling integrations (Reddit, YouTube, Bluesky)
│   │   ├── processors/    # Text cleaning, spam, sentiment NLP
│   │   ├── storage/       # PostgreSQL models & Redis configuration
│   │   ├── scheduler/     # APScheduler runner for periodic ETL
│   │   └── api/           # FastAPI application
│   └── dashboard/         # Streamlit visual interface
├── docs/                  # In-depth architectural and operational guides
├── tests/                 # Unit & integration tests
└── docker-compose.yml     # Container orchestration
```

## CI/CD Pipeline

The project uses GitHub Actions to enforce quality:
- code linting (`ruff`)
- static type checking (`mypy`)
- tested coverage (`pytest`)
- dependency security audits (`pip-audit`)

## License

MIT
