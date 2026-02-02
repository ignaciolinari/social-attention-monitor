# Social Attention Monitor (SAM)

A near–real-time data pipeline that monitors social engagement and public sentiment around newly released movies and TV series.

## Features

- **Multi-platform tracking**: Reddit and YouTube data collection
- **TMDB integration**: Authoritative movie/TV metadata
- **Sentiment analysis**: VADER-based sentiment scoring
- **Real-time metrics**: Attention Index, Hype Acceleration, and more
- **Interactive dashboard**: Streamlit-based visualization
- **REST API**: FastAPI backend for data access

## Quick Start

### 1. Installation

```bash
# Clone and install
git clone https://github.com/your-username/social-attention-monitor.git
cd social-attention-monitor
pip install -e ".[dev]"
```

### 2. Configuration

```bash
# Copy environment template
cp .env.example .env

# Edit .env and add your API keys (optional for demo mode)
```

### 3. Database Setup

Recommended (Docker):

```bash
# Install Docker first (pick one):
# - Docker Desktop (official)
# - OrbStack (macOS)
# - Colima (macOS)

# Start Postgres (+ TimescaleDB) and Redis
make db-up

# Apply migrations
alembic upgrade head
```

Alternative (native Postgres on macOS):

```bash
brew install postgresql@16
brew services start postgresql@16

# Create database and user
createdb sam
psql -d sam -c "CREATE USER sam WITH PASSWORD 'sam';"
psql -d sam -c "GRANT ALL PRIVILEGES ON DATABASE sam TO sam;"
psql -d sam -c "GRANT ALL ON SCHEMA public TO sam;"

alembic upgrade head
```

### 4. Run Demo

```bash
# Run demo with mock data (no API keys required)
python -m sam.cli demo
```

### 5. Start Services

```bash
# Start the API server
make run-api

# In another terminal, start the dashboard
make run-dashboard
```

Optional (run everything via Docker):

```bash
cp .env.example .env
docker compose up -d
```

Then visit:
- **API**: http://localhost:8000/docs
- **Dashboard**: http://localhost:8501

## Architecture

```
[Reddit / YouTube APIs]
        ↓
[Collectors] → Poll every 5 minutes
        ↓
[Processors] → Sentiment analysis, metrics calculation
        ↓
[Storage] → PostgreSQL + TimescaleDB
        ↓
[FastAPI] → REST endpoints
        ↓
[Streamlit] → Interactive dashboard
```

## Key Metrics

| Metric | Description |
|--------|-------------|
| **Attention Index** | Composite score: mentions + velocity + unique users + sentiment |
| **Hype Acceleration** | Rate of change in mention velocity |
| **Sentiment Score** | VADER compound sentiment (-1 to 1) |
| **Platform Engagement** | Normalized engagement across Reddit/YouTube |

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /api/v1/pipeline/health` | Pipeline health stats (data freshness, counts) |
| `GET /api/v1/trending` | Trending movies/TV shows from TMDB |
| `GET /api/v1/search` | Search for titles |
| `GET /api/v1/db/titles` | List tracked titles from DB |
| `GET /api/v1/mentions/reddit` | Reddit mentions for a title |
| `GET /api/v1/mentions/youtube` | YouTube videos for a title |
| `GET /api/v1/metrics/trending` | Titles ranked by Attention Index |
| `GET /api/v1/metrics/timeseries` | Time series metrics for a title |
| `GET /api/v1/sentiment/analyze` | Analyze arbitrary text sentiment |
| `GET /api/v1/alerts` | List detected anomalies/alerts |
| `GET /api/v1/alerts/counts` | Alert counts by severity |
| `POST /api/v1/alerts/{id}/acknowledge` | Acknowledge an alert |
| `WS /ws` | WebSocket for real-time updates |

## Features

### Anomaly Detection

SAM includes statistical anomaly detection that monitors for:
- **Mention Spikes** - Unusual volume increases
- **Sentiment Shifts** - Rapid positive/negative changes
- **Velocity Surges** - Acceleration in mention rate
- **Viral Breakouts** - Multiple simultaneous anomalies

### Real-Time Updates

WebSocket support for live notifications:
```javascript
const ws = new WebSocket('ws://localhost:8000/ws');
ws.onmessage = (event) => console.log(JSON.parse(event.data));
ws.send(JSON.stringify({action: 'subscribe', topic: 'alerts'}));
```

### Rate Limiting

API includes built-in rate limiting (120 requests/minute per IP) with standard headers:
- `X-RateLimit-Limit` - Maximum requests per window
- `X-RateLimit-Remaining` - Remaining requests
- `Retry-After` - Seconds until reset (when limited)

## Project Structure

```
social-attention-monitor/
├── src/
│   ├── sam/
│   │   ├── alerts/        # Anomaly detection & alerts
│   │   ├── collectors/    # API data collectors
│   │   ├── processors/    # Sentiment, metrics
│   │   ├── pipeline/      # Data pipeline components
│   │   ├── scheduler/     # APScheduler jobs
│   │   ├── storage/       # Database models & repository
│   │   ├── api/           # FastAPI app
│   │   └── config.py      # Configuration
│   └── dashboard/         # Streamlit app
├── tests/                 # Unit & integration tests
├── .github/workflows/     # CI pipeline
├── pyproject.toml
└── Makefile
```

## Configuration

### API Keys (Optional for demo mode)

| Service | Environment Variable | Get Key |
|---------|---------------------|---------|
| Reddit | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | [Reddit Apps](https://www.reddit.com/prefs/apps) |
| YouTube | `YOUTUBE_API_KEY` | [Google Cloud Console](https://console.cloud.google.com) |
| TMDB | `TMDB_API_KEY` or `TMDB_ACCESS_TOKEN` | [TMDB Settings](https://www.themoviedb.org/settings/api) |

### Demo Mode

Set `DEMO_MODE=true` in `.env` to use generated mock data without API keys.

### All Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| **Core** | | |
| `SAM_ENV` | `development` | Environment: development, staging, production |
| `DEMO_MODE` | `true` | Use mock data instead of live APIs |
| `LOG_LEVEL` | `INFO` | Logging: DEBUG, INFO, WARNING, ERROR |
| `LOG_JSON` | `false` | Emit structured JSON logs |
| **Database** | | |
| `DATABASE_URL` | `postgresql+asyncpg://sam:sam@localhost:5432/sam` | Async database URL |
| `DATABASE_SYNC_URL` | `postgresql://sam:sam@localhost:5432/sam` | Sync URL for migrations |
| **Redis** | | |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| **API Server** | | |
| `API_HOST` | `0.0.0.0` | API server host |
| `API_PORT` | `8000` | API server port |
| `CORS_ALLOW_ORIGINS` | `*` | Comma-separated CORS allowlist |
| **Cache TTLs** | | |
| `SAM_CACHE_TTL_TRENDING` | `300` | Cache TTL for trending (seconds) |
| `SAM_CACHE_TTL_SEARCH` | `300` | Cache TTL for search (seconds) |
| `SAM_CACHE_TTL_METRICS` | `60` | Cache TTL for metrics (seconds) |
| `SAM_CACHE_TTL_PIPELINE_HEALTH` | `30` | Cache TTL for pipeline health (seconds) |
| **WebSocket** | | |
| `SAM_WS_CLEANUP_INTERVAL` | `60` | Dead connection cleanup interval (seconds) |
| **Collection** | | |
| `POLLING_INTERVAL_MINUTES` | `5` | Data collection interval |
| `TARGET_SUBREDDITS` | `movies,television,...` | Subreddits to monitor |
| `MAX_POSTS_PER_SUBREDDIT` | `100` | Max posts per subreddit per poll |
| **Storage** | | |
| `SAM_STORAGE_ENABLE_RAW_DATA_STORAGE` | `false` | Persist raw data to filesystem |
| `SAM_STORAGE_RAW_DATA_DIR` | `data/raw` | Directory for raw JSONL files |

## Development

```bash
# Install dev dependencies
make dev

# Run tests
make test

# Run tests with coverage
pytest tests/ -v --cov=src/sam

# Lint code
make lint

# Format code
make format

# Type check
mypy src
```

## CI/CD

The project includes GitHub Actions for:
- **Lint & Format** - ruff check and format validation
- **Type Check** - mypy static analysis
- **Tests** - pytest with coverage (Python 3.11 & 3.12)
- **Security** - pip-audit vulnerability scanning
- **Smoke Test** - End-to-end test with real Postgres & Redis

## License

MIT
