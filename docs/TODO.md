# SAM - TODO List

## Phase 1: Foundation ✅

- [x] Project structure
- [x] pyproject.toml with dependencies
- [x] Pre-commit hooks
- [x] .env.example
- [x] Makefile
- [x] PostgreSQL setup
- [x] SQLAlchemy models
- [x] Alembic migrations
- [x] Database connection
- [x] TMDB connector
- [x] Reddit connector
- [x] YouTube connector
- [x] Base collector class
- [x] Pydantic settings
- [x] Logging configuration
- [x] Docker Compose setup

---

## Phase 2: Core Pipeline ✅

- [x] Scheduled polling with APScheduler
- [x] Scheduler safety: prevent overlapping runs (DB advisory lock/lease) + record job run status (started/finished/error)
- [x] Collector resilience: rate limiting + retries/backoff + explicit 429/quota handling
- [x] Title matching algorithm
- [x] Raw data storage to filesystem
- [x] Text cleaning pipeline
- [x] VADER sentiment integration
- [x] Metrics calculation engine
- [x] Attention Index calculation
- [x] Hype Acceleration calculation
- [x] Metrics snapshots persistence (metrics_snapshots)
- [x] Idempotent ingestion: stable mention identity + upsert/dedup + required DB indexes/constraints
- [x] Backfill/recompute command: recompute metrics snapshots for a date range

---

## Phase 3: API & Dashboard

- [ ] FastAPI core endpoints
- [ ] Pydantic schemas
- [ ] Redis caching layer
- [ ] API hardening: pagination + consistent error schema + basic rate limiting (optional auth)
- [ ] Pipeline health endpoint: newest mention age, per-platform counts, processing lag
- [ ] Trending Now view
- [ ] Time Series view
- [ ] Platform Comparison view
- [ ] Sentiment Distribution view

---

## Phase 4: Advanced Features

- [ ] Alert system with anomaly detection
- [ ] WebSocket for real-time updates
- [ ] Transformer-based sentiment (optional)
- [ ] Dashboard polish

---

## Phase 5: Production Readiness

- [ ] Monitoring and logging
- [ ] Documentation
- [ ] Unit tests
- [ ] Integration tests
- [ ] CI pipeline: run tests + lint/format + type-check on PRs
- [ ] Minimal end-to-end smoke test (boot Postgres, run one scheduler tick, hit /health)
- [ ] TimescaleDB hypertables + retention policy
