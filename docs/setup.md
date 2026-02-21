# Setup & Configuration

## 1. Installation Methods

You can run SAM fully natively (Python + external Postgres/Redis), or you can use Docker to spin up the entire cluster seamlessly.

### A. Recommended (Docker Compose)
The easiest way to get started is to use Docker for the databases, or everything.

1. **Install Docker** (Docker Desktop, OrbStack, or Colima).
2. **Clone and Setup Environment**:
```bash
git clone https://github.com/ignaciolinari/social-attention-monitor.git
cd social-attention-monitor
cp .env.example .env
```
3. **Start the Database & Redis Background Services**:
```bash
make db-up
```
4. **Install Python Dependencies and Apply Migrations**:
```bash
pip install -e ".[dev]"
alembic upgrade head
```
5. **Start APIs and Dashboard (in separate terminals)**:
```bash
make run-api
make run-dashboard
```

> [!TIP]
> To run the entire stack—API, Dashboard, and DBs—simply use: `docker compose up -d`

---

### B. Alternative (Native macOS + Homebrew)
If you prefer running Postgres natively without Docker:

1. **Install PostgreSQL**:
```bash
brew install postgresql@16
brew services start postgresql@16
```
2. **Create Database**:
```bash
createdb sam
psql -d sam -c "CREATE USER sam WITH PASSWORD 'sam';"
psql -d sam -c "GRANT ALL PRIVILEGES ON DATABASE sam TO sam;"
psql -d sam -c "GRANT ALL ON SCHEMA public TO sam;"
```
3. **Install Redis**:
```bash
brew install redis
brew services start redis
```
5. **Start APIs, Dashboard, and Collector (in separate terminals)**:
```bash
make run-api
make run-dashboard
make run-collector
```

---

## 2. Configuration Reference

SAM is highly configurable. All configurations live in the `.env` file and are loaded through `src/sam/config.py`.

### Demo Mode
By default, SAM runs in demo mode, which uses mock data without requiring any API keys.
To disable demo mode and use live data:
```env
DEMO_MODE=false
```

### Platform API Access & Compliance

The pipeline is intended to run **fully legally** using official APIs and their terms of service:

| Platform | Status | Notes |
|----------|--------|-------|
| **YouTube** | Supported | Official Data API v3 with quota limits. SAM tracks and respects daily budget. |
| **Bluesky** | Supported | AT Protocol; virtually no restrictive limits. |
| **TMDB** | Supported | Very liberal usage for trending and search. |
| **Reddit** | Restricted | Originally intended; API access is now tightly restricted. Enable only if you have approved access. |
| **Twitter/X** | Not included | Can be added if you have paid API access. |
| **Custom / Scraped** | Possible | The structure can be populated with externally sourced data; compliance is the operator's responsibility. |

### API Keys
> [!IMPORTANT]
> If you are **not** using demo mode, you MUST provide at least one platform API key.

| Service | Environment Variable(s) | Where to get it |
|---------|------------------------|-----------------|
| **Reddit** | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` | [Reddit Apps](https://www.reddit.com/prefs/apps) |
| **YouTube**| `YOUTUBE_API_KEY` | [Google Cloud Console](https://console.cloud.google.com) |
| **TMDB**   | `TMDB_API_KEY` or `TMDB_ACCESS_TOKEN` | [TMDB API Settings](https://www.themoviedb.org/settings/api) |
| **Bluesky**| `BLUESKY_IDENTIFIER` (e.g. `user.bsky.social`), `BLUESKY_APP_PASSWORD` | Bluesky App Settings |

### Sentiment Models
Choose which sentiment architecture to run:
```env
SENTIMENT_MODEL=vader     # Fast, Dictionary-based (default)
# OR
SENTIMENT_MODEL=roberta   # Deep Learning, context-aware. Requires transformers extra (`pip install ".[transformers]"`)
# OR
SENTIMENT_MODEL=both      # Runs both pipelines simultaneously for comparison.

SAM_TRANSLATE_BEFORE_SENTIMENT=false # Translates non-English content to English before sentiment scoring
SAM_SENTIMENT_FALLBACK_TO_VADER=true # Falls back to VADER if RoBERTa fails to load
```

### Feature Toggles (NLP Processors)
```env
SAM_ENABLE_YOUTUBE_COMMENTS=true     # Collects comments on YouTube videos
SAM_YOUTUBE_COMMENTS_PER_VIDEO=30    # Comment depth per video
SAM_ENABLE_EMOTION_DETECTION=false   # (Requires transformers extra) Categories like Joy, Sadness
SAM_ENABLE_SARCASM_DETECTION=false   # (Requires transformers extra) Detects sarcastic tone
SAM_ENABLE_SPAM_FILTER=true          # Rules-based spam removal
SAM_ENABLE_KEYWORD_EXTRACTION=true   # Identify top phrases
SAM_ENABLE_ASPECT_SENTIMENT=false    # Long-form aspect breakdown
```

### Core Infrastructure
```env
SAM_ENV=development                       # Env: development, staging, production
LOG_LEVEL=INFO
LOG_JSON=false                            # Emit structured JSON logs (for log aggregators)
DATABASE_URL=postgresql+asyncpg://sam:sam@localhost:5432/sam
DATABASE_SYNC_URL=postgresql://sam:sam@localhost:5432/sam   # For Alembic migrations
API_HOST=0.0.0.0
API_PORT=8000
DASHBOARD_PORT=8501
REDIS_URL=redis://localhost:6379/0
```

### Collection & Storage Configuration
```env
POLLING_INTERVAL_MINUTES=5                        # How often the pipeline runs
TARGET_SUBREDDITS=movies,television,netflix       # CSV of subreddits to monitor
MAX_POSTS_PER_SUBREDDIT=100                       # Fetch depth

# YouTube search tuning
YOUTUBE_SEARCH_ORDER=relevance                    # relevance, date, rating, viewCount, title, videoCount
YOUTUBE_PUBLISHED_AFTER_DAYS=30                   # Only fetch videos from last N days

MENTIONS_REFRESH_STALE_MINUTES=30                 # Refresh mentions in API when older than this

SAM_STORAGE_ENABLE_RAW_DATA_STORAGE=false         # Save raw API JSONL dumps
SAM_STORAGE_RAW_DATA_DIR=data/raw                 # Location for dumps
```

### Cache & WebSocket
```env
# Cache TTLs (seconds)
SAM_CACHE_TTL_TRENDING=300
SAM_CACHE_TTL_SEARCH=300
SAM_CACHE_TTL_METRICS=60
SAM_CACHE_TTL_PIPELINE_HEALTH=30

# WebSocket dead connection cleanup interval (seconds)
SAM_WS_CLEANUP_INTERVAL=60
```

### Dashboard API Connection
When the Dashboard runs in a different context (e.g. Docker), it needs to reach the API:
```env
# For Docker Compose (dashboard container): API_HOST=api, API_PORT=8000
# For remote API: use SAM_API_BASE_URL to override entirely
SAM_API_BASE_URL=http://api:8000                  # Optional: full API base URL
API_HOST=127.0.0.1                               # Default when running locally
API_PORT=8000
SAM_DASHBOARD_HTTP_TIMEOUT=10                     # HTTP timeout for API calls (seconds)
```

### Demo Mode Database (Optional)
To use a separate database when running in demo mode:
```env
DATABASE_DEMO_URL=postgresql+asyncpg://...        # Async URL for demo
DATABASE_DEMO_SYNC_URL=postgresql://...           # Sync URL for migrations
# Or: SAM_DEMO_DATABASE_URL, SAM_DEMO_DATABASE_SYNC_URL
```

---

## 3. Developer Tools

### Makefile Targets
| Target | Purpose |
|--------|---------|
| `make install` | Install production dependencies |
| `make dev` | Install dev deps + pre-commit |
| `make test` | Run tests with coverage |
| `make test-ci` | Run tests as in CI (XML output) |
| `make test-integration` | Run tests with Docker Postgres on port 5433 |
| `make run-api` | Start FastAPI server |
| `make run-dashboard` | Start Streamlit dashboard |
| `make run-collector` | Start background collector |
| `make db-up` | Start Postgres + Redis via Docker |
| `make db-down` | Stop Docker services |
| `make db-upgrade` | Apply Alembic migrations |
| `make db-migrate` | Create new migration (prompts for message) |
| `make db-downgrade` | Rollback one migration |
| `make lint` | Run ruff check |
| `make format` | Run ruff format |
| `make ci-check` | Lint + format check + mypy + tests |
| `make lock` | Regenerate requirements lockfiles (uv) |
| `make audit` | Run pip-audit for vulnerabilities |

### Integration Tests
Integration tests require a real Postgres instance. Use `make test-integration` which:
1. Starts Postgres on port 5433 (avoiding conflicts with local dev)
2. Creates `sam_test` database
3. Runs pytest with `SAM_TEST_DATABASE_URL=postgresql+asyncpg://sam:sam@127.0.0.1:5433/sam_test`

### Pre-commit
`make dev` installs pre-commit hooks. Before each commit, the following run automatically:
- Trailing whitespace, end-of-file, YAML checks
- Ruff (lint + format)
- mypy on `src/`

### CI & Dependabot
GitHub Actions enforce lint, type check, tests, and security audit. Dependabot opens weekly PRs for pip and GitHub Actions updates.

---

## 4. Data Retention & Privacy

- **Stored data**: SAM stores titles (TMDB metadata), mentions (post/comment content, platform IDs, sentiment scores), metrics snapshots, and alerts. Raw API responses can optionally be dumped to disk when `SAM_STORAGE_ENABLE_RAW_DATA_STORAGE=true`.
- **Retention**: Data is kept indefinitely by default. Implement periodic cleanup (e.g. delete mentions older than N days) if required for compliance.
- **GDPR / Privacy**: If you process data about EU users, ensure your use case complies with applicable laws. SAM does not implement retention limits or erasure workflows out of the box.

---

## 5. Production Deployment

For production, consider:

- **Secrets**: Never commit `.env`. Use a secrets manager or platform-specific env injection. Rotate API keys and DB passwords periodically.
- **Database**: Use a managed PostgreSQL (e.g. RDS, Cloud SQL, Supabase) with backups. TimescaleDB is optional but recommended for time-series queries.
- **Redis**: Use managed Redis or a resilient cluster. Required for cache and collector toggles.
- **CORS**: Set `CORS_ALLOW_ORIGINS` to specific origins, not `*`.
- **Environment**: Set `SAM_ENV=production`, `LOG_LEVEL=INFO` (or `WARNING`), `LOG_JSON=true` for structured logs.
- **Reverse proxy**: Put the API and Dashboard behind nginx, Caddy, or a cloud load balancer for TLS and rate limiting at the edge.
- **Scaling**: Run a single collector instance (distributed lease prevents duplicates). Scale API replicas as needed; they share Redis for cache and toggles.
