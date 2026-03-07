# API Reference

The FastAPI server provides REST endpoints for data access and a WebSocket connection for real-time alerting.

**Base URL**: `http://localhost:8000`
**Swagger Docs**: `GET /docs`
**Redoc**: `GET /redoc`

---

## REST Endpoints

### 1. Health & Pipeline Status
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Basic system liveness, dependency states, and toggle states. |
| `GET` | `/ready` | Readiness probe (returns 503 when required dependencies are unavailable). |
| `GET` | `/api/v1/pipeline/health` | Comprehensive operational stats (data freshness, mention counts in last 24h, latest runs). |

### 2. Collectors
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/collectors/status` | Returns enabled/configured status for Reddit, YouTube, Bluesky. |
| `PUT` | `/api/v1/collectors/{platform}/toggle?enabled=true\|false` | Toggle YouTube or Bluesky on/off at runtime. Reddit cannot be toggled (requires `.env`). |

### 3. General Data Access
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/trending` | Fetches trending movies/TV shows from TMDB. Query: `?media_type=all|movie|tv`, `?limit=20`. |
| `GET` | `/api/v1/search` | Search TMDB for titles. Query: `?query=<query>`. |
| `GET` | `/api/v1/db/titles` | Lists titles tracked in the SAM database. Query: `q`, `limit`, `offset`, `include_inactive`. |
| `GET` | `/api/v1/mentions/reddit` | Reddit mentions. Query: `title` (required), `title_id` (optional, preferred), `limit`, `offset`. |
| `GET` | `/api/v1/mentions/youtube` | YouTube mentions. Query: `title` (required), `title_id` (optional, preferred), `limit`, `offset`. |
| `GET` | `/api/v1/mentions/bluesky` | Bluesky mentions. Query: `title` (required), `title_id` (optional, preferred), `limit`, `offset`. |

### 4. Metrics
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/metrics/trending` | Returns locally tracked titles ranked by Attention Index. |
| `GET` | `/api/v1/metrics/timeseries` | Historical sentiment and velocity. Query: `title_id` (required), `window_hours`, `hours` (lookback). |
| `GET` | `/api/v1/metrics/box-office` | Titles with revenue/budget alongside attention index for correlation analysis. Query: `window_hours`, `limit`. |
| `GET` | `/api/v1/metrics/language-breakdown` | Mention aggregation by detected language with per-language sentiment. Returns `404` when `title_id` is unknown. Query: `title_id` (required), `hours`. |
| `GET` | `/api/v1/metrics/compare` | Parallel timeseries for multi-title comparison. Query: `title_ids` (comma-separated, 2–5 required), `window_hours`, `hours`. |
| `GET` | `/api/v1/metrics/benchmark` | Compare a title's first-N-days day-level trajectory against averaged peers of the same media type. Query: `title_id` (required), `comparison_type=movie|tv`, `days`, `window_hours`, `comparison_limit`. |
| `GET` | `/api/v1/sentiment/analyze` | Submit arbitrary text via `?text=` for an ad-hoc sentiment score. |

### 5. Watchlists
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/watchlists` | List all user-defined watchlists. Query: `limit`, `offset`. |
| `POST` | `/api/v1/watchlists` | Create a new watchlist. JSON body: `{"name": "...", "tmdb_ids": [...]}`. |
| `PUT` | `/api/v1/watchlists/{id}` | Update an existing watchlist. JSON body: `{"name": "...", "tmdb_ids": [...]}`. |
| `DELETE` | `/api/v1/watchlists/{id}` | Delete a watchlist. |

### 6. Alerts
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/alerts` | Lists recent alerts. Filters: `limit`, `offset`, `title_id`, `severity`, `hours`, `unacknowledged_only`. |
| `GET` | `/api/v1/alerts/counts` | Summary counts of alerts grouped by severity. |
| `POST`| `/api/v1/alerts/{alert_id}/acknowledge`| Marks a specific alert as acknowledged. |
| `POST`| `/api/v1/alerts/run-detection`| Manually trigger anomaly detection run. |

### 7. Pipeline & WebSocket Status
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/pipeline/quota` | Returns detailed usage of platform API limits (e.g., YouTube's Daily Budget). |
| `GET` | `/api/v1/pipeline/runs` | Paginated pipeline run history. Query: `limit`, `offset`, `status`. |
| `GET` | `/api/v1/alerts/system-health` | System-wide health indicators (no ingest, collector failure, quota, Redis). Run automatically each collector cycle; results broadcast via WebSocket (throttled 15 min/type). |
| `GET` | `/api/v1/metrics/app` | Application metrics in JSON format. |
| `GET` | `/metrics` | Prometheus-compatible metrics in text format, including API-process metrics plus persisted collector totals derived from pipeline runs. |
| `GET` | `/api/v1/ws/status` | Returns WebSocket connection count and subscription stats. |

---

## WebSockets

Real-time anomalies and per-title metrics updates are pushed to connected clients. Alerts and metrics are relayed through Redis pub/sub so scheduler-generated events are delivered to API WebSocket clients even when the scheduler and API run in separate processes. System health broadcasts are throttled to once per 15 minutes per alert type to avoid spam.

**Endpoint**: `ws://localhost:8000/ws`

### Protocol

Clients receive a welcome message on connect and are auto-subscribed to `all`. Send JSON messages to control subscriptions:

| Action | Payload | Description |
|--------|---------|-------------|
| `subscribe` | `{"action": "subscribe", "topic": "alerts"}` | Subscribe to a topic. |
| `unsubscribe` | `{"action": "unsubscribe", "topic": "alerts"}` | Unsubscribe from a topic. |
| `ping` | `{"action": "ping"}` | Receives `{"type": "pong"}` for liveness. |
| `status` | `{"action": "status"}` | Returns connection count and subscription stats. |

**Topics**: `alerts` (anomaly notifications), `metrics` (metric updates), `all` (everything).

### Example (JS)
```javascript
const ws = new WebSocket('ws://localhost:8000/ws');
ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === 'connected') {
        console.log('Subscribed to:', data.subscribed);  // default: ["all"]
    } else if (data.type === 'alert') {
        console.log("New Alert!", data.data);
    }
};
// Subscribe to alerts only
ws.send(JSON.stringify({action: 'subscribe', topic: 'alerts'}));
```

---

## Authentication

> [!IMPORTANT]
> When the `SAM_API_KEY` environment variable is set, mutation and admin endpoints require authentication.

**How to authenticate:** Include one of the following headers in your request:
- `Authorization: Bearer <your-api-key>`
- `X-API-Key: <your-api-key>`

### Protected Endpoints

| Method | Endpoint | Reason |
|--------|----------|--------|
| `PUT` | `/api/v1/collectors/{platform}/toggle` | Collector control |
| `POST` | `/api/v1/alerts/run-detection` | Admin action |
| `POST` | `/api/v1/alerts/{id}/acknowledge` | Alert mutation |
| `GET` | `/api/v1/sentiment/analyze` | Expensive NLP endpoint |
| `GET` | `/health?external=true` | External dependency probes |
| `POST` / `PUT` / `DELETE` | Any `/api/*` path | Mutation operations |

Unprotected endpoints (other GET requests, basic health checks, metrics) remain publicly accessible.

---

## Rate Limiting

> [!IMPORTANT]
> The API employs sliding-window rate limiting by IP to prevent abuse (default 120 requests/minute).

Endpoints return the following standard headers:
- `X-RateLimit-Limit`: Maximum requests allowed in the window.
- `X-RateLimit-Remaining`: Requests left in the current window.
- `Retry-After`: Seconds to wait before retrying (only present if 429 Too Many Requests is triggered).

> [!NOTE]
> The following endpoints are excluded from rate limiting: `/health` (without `external=true`), `/ready`, `/api/v1/pipeline/health`, `/metrics`.

---

## Caching

High-cost endpoints are cached in Redis. TTLs are configurable via environment variables:

| Endpoint / Area | Env Variable | Default |
|-----------------|--------------|---------|
| Trending | `SAM_CACHE_TTL_TRENDING` | 300s |
| Search | `SAM_CACHE_TTL_SEARCH` | 300s |
| Metrics | `SAM_CACHE_TTL_METRICS` | 60s |
| Pipeline Health | `SAM_CACHE_TTL_PIPELINE_HEALTH` | 30s |

### Mentions Refresh

When mentions data is older than `MENTIONS_REFRESH_STALE_MINUTES` (default 30), the API triggers a background refresh on the first request. The refresh now routes through the same authoritative collector ingestion path used by scheduled runs, so refreshed mentions receive the same persistence, snapshot, alerting, and accounting behavior.
