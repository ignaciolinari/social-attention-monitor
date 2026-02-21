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
- **Translation (`deep-translator`)**: Optional pipeline step that detects non-English text and translates it to English *before* scoring, ensuring RoBERTa and VADER maintain high accuracy globally.
- **Emotion Classification**: Maps text into discrete categories (e.g., Joy, Anger, Sadness, Surprise).
- **Sarcasm Detection**: Identifies potentially sarcastic comments that might otherwise skew the core sentiment score.
- **Keyword Extraction**: Identifies the most prominent terms used alongside the tracked entity to highlight trending topics.
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
- **System Metrics**: High-level overview of tracking counts, pipeline health, and top trending titles ranked by the dynamic **Attention Index**.
- **Search & Analyze**: Deep-dive into specific titles. Renders rich timeseries graphs comparing Mention Velocity against Average Sentiment over custom time windows.
- **Sentiment Comparison**: A side-by-side analysis of how different platforms (e.g., Reddit vs. YouTube) compare in their sentiment for a specific title, bringing the **Sentiment Divergence** metric to life.
- **Anomaly Alerts**: A live feed of triggered anomalies (Spikes, Shifts, Breakouts), allowing operators to trace exactly *when* public opinion turned.
- **API Quota**: Visualizations of external API usage (especially YouTube's daily budget) to ensure the system stays within limits. YouTube quota resets at midnight Pacific Time (PT).
- **System Config**: UI for dynamically toggling collectors on or off.

---

## 6. Developer CLI Utilities

SAM ships with a powerful command-line interface (`sam`) to manage the pipeline and test NLP models locally.

- `sam benchmark-sentiment`: Runs a performance benchmark testing VADER's speed against RoBERTa's speed on a 100-text sample batch.
- `sam compare-sentiment`: Pulls the 20 most recent mentions from the local database and runs them through *both* VADER and RoBERTa, printing a side-by-side terminal comparison of the sentiment scores.
- `sam recompute-metrics --from <date> --to <date>`: Backfills and recalculates all historical time-series snapshots if the ranking algorithms are updated. Optional flags: `--windows 1,24` (snapshot windows in hours), `--bucket-hours 1` (bucket size).
- `sam demo`: Runs a quick standard-out fetch from all enabled collectors using mock data to test connectivity.
