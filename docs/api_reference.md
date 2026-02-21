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
| `GET` | `/api/v1/search` | Search TMDB for titles. Query: `?q=<query>`. |
| `GET` | `/api/v1/db/titles` | Lists titles tracked in the SAM database. |
| `GET` | `/api/v1/mentions/reddit` | Reddit mentions. Query: `title` (required), `title_id` (optional, preferred), `limit`, `offset`. |
| `GET` | `/api/v1/mentions/youtube` | YouTube mentions. Query: `title` (required), `title_id` (optional, preferred), `limit`, `offset`. |
| `GET` | `/api/v1/mentions/bluesky` | Bluesky mentions. Query: `title` (required), `title_id` (optional, preferred), `limit`, `offset`. |

### 4. Metrics
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/metrics/trending` | Returns locally tracked titles ranked by Attention Index. |
| `GET` | `/api/v1/metrics/timeseries` | Historical sentiment and velocity. Query: `title_id` (required), `window_hours`, `hours` (lookback). |
| `GET` | `/api/v1/sentiment/analyze` | Submit arbitrary text via `?text=` for an ad-hoc sentiment score. |

### 5. Alerts
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/alerts` | Lists all active (unacknowledged) statistical anomalies. |
| `GET` | `/api/v1/alerts/counts` | Summary counts of alerts grouped by severity. |
| `POST`| `/api/v1/alerts/{alert_id}/acknowledge`| Marks a specific alert as acknowledged, removing it from active views. |
| `POST`| `/api/v1/alerts/run-detection`| Manually trigger anomaly detection run. |

### 6. API Quotas & WebSocket Status
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/pipeline/quota` | Returns detailed usage of platform API limits (e.g., YouTube's Daily Budget). |
| `GET` | `/api/v1/ws/status` | Returns WebSocket connection count and subscription stats. |

---

## WebSockets

Real-time anomalies and metrics are pushed to connected clients.

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
    } else if (data.topic === 'alerts') {
        console.log("New Alert!", data.payload);
    }
};
// Subscribe to alerts only
ws.send(JSON.stringify({action: 'subscribe', topic: 'alerts'}));
```

---

## Rate Limiting

> [!IMPORTANT]
> The API employs Token-Bucket rate limiting by IP to prevent abuse (default 120 requests/minute).

Endpoints return the following standard headers:
- `X-RateLimit-Limit`: Maximum requests allowed in the window.
- `X-RateLimit-Remaining`: Requests left in the current window.
- `Retry-After`: Seconds to wait before retrying (only present if 429 Too Many Requests is triggered).

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

When mentions data is older than `MENTIONS_REFRESH_STALE_MINUTES` (default 30), the API triggers a background refresh on the first request.
