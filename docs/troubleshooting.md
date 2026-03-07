# Troubleshooting & FAQ

## Common Issues

### Reddit: 403 Forbidden / API access denied

Reddit has restricted API access for many applications. If you see 403 errors or "API access denied":

- Ensure you have **approved** API access from Reddit (applications may need to apply).
- Set `REDDIT_ENABLED=true` in `.env` only when you have valid, approved credentials.
- If you don't have access, disable Reddit: `REDDIT_ENABLED=false` (default). The pipeline runs fine with YouTube and Bluesky only.

### YouTube: Quota exceeded / 403

YouTube Data API v3 has a daily quota (default 10,000 units). SAM tracks usage and respects the budget.

- **Quota exceeded**: Wait until midnight Pacific Time (PT) when the quota resets, or request a quota increase in Google Cloud Console.
- **Reduce consumption**: Set `SAM_ENABLE_YOUTUBE_COMMENTS=false` to skip comment collection (saves 1 unit per video vs 100+ for search).
- **Increase search window**: Reducing `YOUTUBE_PUBLISHED_AFTER_DAYS` can reduce search calls but may miss older content.
- Check `/api/v1/pipeline/quota` for current usage.

### TimescaleDB: Extension not found / NOTICE during migrations

TimescaleDB is **optional**. If you see `RAISE NOTICE 'timescaledb not preloaded...'` during `alembic upgrade head`:

- Migrations continue successfully; SAM works on plain PostgreSQL.
- For TimescaleDB: use the official image `timescale/timescaledb:latest-pg16` and ensure the extension is enabled in your Postgres config (`shared_preload_libraries`).
- Docker Compose uses the TimescaleDB image by default.

### Database connection refused

- **Local**: Ensure Postgres and Redis are running. With Docker: `make db-up`.
- **Wrong port**: Default is 5432. If using `make test-integration`, Postgres runs on 5433.
- **Credentials**: Verify `DATABASE_URL` and `POSTGRES_*` match your setup.
- **Async vs sync**: Use `DATABASE_URL` (asyncpg) for the app; `DATABASE_SYNC_URL` (psycopg2) for Alembic.

### Dashboard can't reach API

When the Dashboard runs in Docker or on another host:

- Set `API_HOST=api` and `API_PORT=8000` (for Docker Compose, where the API service is named `api`).
- Or set `SAM_API_BASE_URL=http://host:port` to override entirely.
- Ensure the API container is healthy: `curl http://localhost:8000/health`

### API fails to start: `[Errno 48] Address already in use`

If `make run-api` fails with an address-in-use error, another process is already bound to port `8000`.

- Find and stop the conflicting process, or
- Start API on another port, for example: `API_PORT=8001 make run-api`
- If you change API port, update dashboard connection settings accordingly (`SAM_API_BASE_URL` or `API_HOST`/`API_PORT`).

### RoBERTa sentiment model fails to load

RoBERTa requires the `transformers` extra:

```bash
pip install ".[transformers]"
```

If it still fails (e.g. missing CUDA, out-of-memory), set `SAM_SENTIMENT_FALLBACK_TO_VADER=true` (default) to fall back to VADER.

### First live run is slow / model download warnings

On the first non-demo run, NLP models (RoBERTa, sarcasm, emotions) may be downloaded and initialized, which can noticeably increase cycle time.

- This is expected on first run; subsequent runs are faster due to local cache.
- You may see Hugging Face unauthenticated warnings. Optionally set `HF_TOKEN` for higher rate limits and faster model downloads.

### No mentions / empty metrics

- **Demo mode**: In demo mode, data is generated. Check `SAM_DEMO_MODE=false` for live data.
- **Collector not running**: Start the collector: `make run-collector`.
- **API keys**: Ensure at least one of YouTube or Bluesky has valid credentials and is enabled.
- **Titles**: TMDB must return trending titles first; then collectors search for mentions. Run a few poll cycles.

### One-shot collector says lease is active

If `python -m sam.scheduler.runner --once` or a manual recovery run reports that the collector lease is active:

- A healthy collector process may already be running. Check that first and stop it if your intent is to run manually.
- If the previous process died and left stale state behind, run:

```bash
python -m sam.scheduler.runner --once --force-clear-lease --limit-titles 2
```

- `--force-clear-lease` is intentionally conservative: it clears only stale state and refuses to evict an active lease owned by a live collector.

## FAQ

**Can I run SAM without Reddit?**
Yes. Reddit defaults to disabled. YouTube and Bluesky are sufficient for the pipeline.

**Can I add Twitter/X?**
The architecture supports it. A Twitter collector would need to be implemented; Twitter's API now requires paid access.

**Is data retention configurable?**
Data is stored indefinitely. See [Setup](setup.md#data-retention--privacy) for privacy notes. Retention policies can be added (e.g. periodic cleanup jobs).

**How do I run the full stack in Docker?**
`docker compose up -d` starts postgres, redis, api, collector, and dashboard.

**What does "mentions capped" mean in pipeline stats?**
Metrics snapshots fetch up to 10,000 mentions per title per window. If a title has more, the snapshot is computed from the cap and `mentions_capped_titles` is incremented in pipeline run stats. Check Pipeline Observability for this metric. For very high-volume titles, consider increasing the limit in `metrics_snapshots.py` or splitting by platform.
