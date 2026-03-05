# Features

Social Attention Monitor (SAM) includes a robust suite of data collection, processing, and alerting features.

## 1. Data Collectors

The pipeline collects data asynchronously from various platforms to form a complete picture of engagement.

- **Reddit (`RedditCollector`)**: Monitors specified subreddits (e.g., `movies`, `television`) for submissions and comments matching trending titles.
- **YouTube (`YouTubeCollector`)**: Fetches video metadata and (optionally) top user comments relevant to tracked media.
- **TMDB (`TMDBCollector`)**: The source of truth for "Tracking". Fetches daily trending Movies and TV Shows, including metadata like Release Date, Popularity, and Overview.
- **Bluesky (`BlueskyCollector`)**: Integrates with the AT Protocol to fetch recent posts mentioning the titles.

> [!TIP]
> YouTube and Bluesky collectors can be dynamically toggled ON/OFF during runtime via the API or Dashboard. **Reddit** requires setting `REDDIT_ENABLED=true` in `.env` with valid API keys—it cannot be toggled from the dashboard.

---

## 2. Sentiment & NLP Engine

SAM doesn't just count mentions; it attempts to understand them through a multi-stage NLP pipeline.

### Core Sentiment Processors
- **VADER (Rules-Based)**: Extremely fast, dictionary-based sentiment analysis tuned for social media (handles emojis, capitalization, and punctuation well).
- **RoBERTa (Transformer-Based)**: A deep-learning model from Hugging Face that understands complex context and nuance better than VADER. SAM can use either, or *both* simultaneously, falling back to VADER if RoBERTa fails to initialize.

### Advanced Capabilities
- **Translation (built-in client)**: Optional pipeline step that detects non-English text and translates it to English *before* scoring, ensuring RoBERTa and VADER maintain high accuracy globally. Includes per-language locks, a configurable timeout (`_TRANSLATE_TIMEOUT_SECONDS`), and `ThreadPoolExecutor`-based concurrency for throughput.
- **Emotion Classification**: Maps text into discrete categories (e.g., Joy, Anger, Sadness, Surprise).
- **Sarcasm Detection**: Identifies potentially sarcastic comments that might otherwise skew the core sentiment score.
- **Aspect-Based Sentiment** *(optional)*: When `SAM_ENABLE_ASPECT_SENTIMENT=true`, long-form content (>100 chars) is analyzed for per-aspect sentiment (e.g., "acting", "plot", "visuals"), stored in the sentiment JSONB payload.
- **Keyword Extraction**: Identifies the most prominent terms used alongside the tracked entity to highlight trending topics. Input is sampled to a max of 500 texts for performance.
- **Content Deduplication**: `detect_duplicate_content` runs in the pipeline before persistence, removing near-duplicate mentions across collection cycles to improve data quality.
- **Text Cleaning & Spam Filtering**: Strips URLs and HTML. Evaluates text against heuristic patterns (like excessive repetition or known spam phrases) to drop low-quality data early.

---

## 3. Metrics & Scoring

Raw mentions are aggregated into Time-Series Windows (e.g., Hourly snapshots) for each tracked title.

### 3.1. Core Composite Scores
Raw mentions are aggregated into Time-Series Windows (e.g., Hourly snapshots) for each tracked title.

#### Attention Index
The primary ranking metric (scaled 0-100), combining:
1. **Volume**: Total number of mentions.
2. **Velocity**: The rate at which mentions are arriving.
3. **Reach**: The count of unique authors discussing the topic (normalized by platform).
4. **Sentiment Momentum**: The intensity and rate of change of the sentiment.

#### Hype Acceleration
The derivative of Mention Velocity. It tracks the second derivative of mentions over time, serving as an early indicator of virality by measuring whether the conversation is *speeding up* or *slowing down*.

---

### 3.2. Alpha Analytical Metrics
SAM computes several advanced heuristic scores for deep socio-behavioral analysis:

- **Engagement-Weighted Sentiment**: Adjusts sentiment scores by multiplying the compound polarity by the physical engagement (likes, retweets, views) of the post. Highly visible opinions carry more weight.
- **Sentiment Divergence**: Computes pairwise absolute sentiment differences across platforms. Detects when Reddit loves a movie, but YouTube hates it.
- **Audience Fatigue Index (0.0 - 1.0)**: A proprietary composite measuring declining engagement-per-mention and declining sentiment. Detects when a topic is burning out.
- **Viral Coefficient**: The ratio of reposts/shares to total posts, measuring organic amplification.
- **Author Diversity Score (HHI)**: Utilizes the Herfindahl-Hirschman Index to measure whether a trend is driven by a decentralized crowd or a few highly active vocal authors.
- **Creator vs. Audience Sentiment**: Splits aggregate sentiment arrays, comparing the original poster's sentiment against the commenters' sentiment.


---

## 4. Anomaly Detection & Alerts

SAM continuously monitors the calculated metrics against historical baselines to detect statistical anomalies.

**Alert Types:**
- `MENTION_SPIKE`: Sudden, statistically significant increase in raw mention volume.
- `VELOCITY_SURGE`: A rapid acceleration in how fast new mentions are appearing.
- `SENTIMENT_SHIFT`: A sudden drop or spike in the average sentiment score.
- `VIRAL_BREAKOUT`: A composite alert triggered when both Volume and Velocity cross critical thresholds simultaneously.

**Real-time Delivery**: When an alert is triggered, it is instantly published via Redis Pub/Sub directly to the FastAPI layer, which broadcasts it over WebSockets to all connected dashboard clients.

---

## 5. Interactive Analytical Dashboard

A dedicated `Streamlit` application provides a window into the pipeline's operational state and unlocks powerful data storytelling.

**Analytical Views:**
- **Trending Now**: High-level overview of tracking counts, pipeline health, and top trending titles ranked by the dynamic **Attention Index**.
- **Time Series**: Deep-dive into specific titles. Renders rich timeseries graphs comparing Mention Velocity against Average Sentiment over custom time windows.
- **Sentiment Comparison**: A side-by-side analysis of how different platforms (e.g., Reddit vs. YouTube) compare in their sentiment for a specific title, bringing the **Sentiment Divergence** metric to life.
- **Alpha Metrics**: Dashboard for advanced analytical scores like Audience Fatigue, Viral Coefficient, and Author Diversity.
- **Anomaly Alerts**: A live feed of triggered anomalies (Spikes, Shifts, Breakouts), allowing operators to trace exactly *when* public opinion turned.
- **API Quota**: Visualizations of external API usage (especially YouTube's daily budget) to ensure the system stays within limits.
- **Pipeline Observability**: Per-run timing breakdown, per-title processing times (`per_title_ms`), collector stats, and translation metrics.
- **System Config**: UI for dynamically toggling collectors on or off (sends API key automatically when `SAM_API_KEY` is configured).

---

## 6. Security & Authentication

SAM supports optional API key authentication to protect sensitive and expensive endpoints.

- **API Key Middleware**: When `SAM_API_KEY` is set, mutation endpoints (`PUT`, `DELETE`, `POST` for alerts) and the sentiment analysis endpoint require an `Authorization: Bearer <key>` or `X-API-Key: <key>` header.
- **Rate Limiting**: Sliding-window rate limiter (default 120 req/min per IP). Health and metrics endpoints are excluded from rate limiting to support monitoring integrations.
- **CORS**: Configurable allowlist via `CORS_ALLOW_ORIGINS`; defaults to `*` in development and `[]` in production.

> [!TIP]
> See the [API Reference](api_reference.md#authentication) for the full list of protected endpoints.

---

## 7. Observability & Metrics

SAM is built for operational visibility in production.

- **Prometheus `/metrics` endpoint**: Exposes counters (`collection_cycles_total`, `mentions_inserted_total`, `quota_units_used`) and histograms (`sentiment_analysis_duration_seconds`) in Prometheus text format.
- **Pipeline Self-Health Alerts**: `AlertManager.check_system_health()` monitors for stalled ingestion, collector failures, quota thresholds, and Redis degradation — exposed at `GET /api/v1/alerts/system-health`.
- **Log Correlation IDs**: Runner uses `logger.contextualize(run_id=..., title=...)` so every log line within a collection cycle carries structured context for easy debugging.
- **Pipeline Run History**: `GET /api/v1/pipeline/runs` returns paginated run history with rich stats (timing, quota, spam, translation, per-title breakdown).
- **Redis Degraded Warning**: Rate-limited warning logs when Redis becomes unreachable, without blocking the pipeline.

---

## 8. Pipeline Resilience

The pipeline is designed to degrade gracefully under failure conditions.

- **Circuit Breaker**: Per-platform failure counter; after consecutive failures, the platform is skipped for remaining titles in the cycle to avoid cascading timeouts.
- **Title Quarantine**: Titles that fail repeatedly are temporarily excluded via a Redis-backed dead-letter mechanism, preventing a single bad title from degrading the entire run.
- **SIGHUP Hot Reload**: Sending `SIGHUP` to the process clears the settings cache, allowing environment variable changes without a restart.
- **Collector Fault Tolerance**: Each collector's `close()` is wrapped in `contextlib.suppress(Exception)` during shutdown to prevent one failure from blocking cleanup.
- **Translation Fault Tolerance**: Per-language locks, configurable timeout, and `ThreadPoolExecutor`-based execution prevent translation from blocking the sentiment pipeline.
- **NLP Concurrency Safety**: Inference locks (`_inference_lock`, `_roberta_lock`) protect transformer models from concurrent thread access. VADER uses `ProcessPoolExecutor` for batches ≥64 for true CPU parallelism.

---

## 9. Developer CLI Utilities

SAM ships with a powerful command-line interface (`sam`) to manage the pipeline and test NLP models locally.

- `sam benchmark-sentiment`: Runs a performance benchmark testing VADER's speed against RoBERTa's speed on a 100-text sample batch.
- `sam compare-sentiment`: Pulls the 20 most recent mentions from the local database and runs them through *both* VADER and RoBERTa, printing a side-by-side terminal comparison of the sentiment scores.
- `sam recompute-metrics --from <date> --to <date>`: Backfills and recalculates all historical time-series snapshots if the ranking algorithms are updated. Optional flags: `--windows 1,24` (snapshot windows in hours), `--bucket-hours 1` (bucket size).
- `sam demo`: Runs a quick standard-out fetch from all enabled collectors using mock data to test connectivity.
