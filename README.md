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

# Start Postgres
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
| `GET /api/v1/trending` | Trending movies/TV shows |
| `GET /api/v1/search` | Search for titles |
| `GET /api/v1/mentions/reddit` | Reddit mentions for a title |
| `GET /api/v1/mentions/youtube` | YouTube videos for a title |
| `GET /api/v1/sentiment/analyze` | Analyze arbitrary text sentiment |

## Project Structure

```
social-attention-monitor/
├── src/
│   ├── sam/
│   │   ├── collectors/    # API data collectors
│   │   ├── processors/    # Sentiment, metrics
│   │   ├── storage/       # Database models
│   │   ├── api/           # FastAPI app
│   │   └── config.py      # Configuration
│   └── dashboard/         # Streamlit app
├── tests/
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

## Development

```bash
# Install dev dependencies
make dev

# Run tests
make test

# Lint code
make lint

# Format code
make format
```

## License

MIT
