# Pipeline Performance Report

Date: 2026-03-06

## Scope

This report summarizes findings from a full live end-to-end pipeline run in non-demo mode with TMDB, YouTube, and Bluesky enabled. The goal is to preserve the main runtime findings and the highest-value optimization opportunities for future implementation work.

> [!NOTE]
> This document is a historical performance snapshot from before the audit remediation work landed. It is still useful for understanding the original bottlenecks, but current runtime behavior now differs in a few important ways: translation is provider-gated and disabled by default, detected language is reused across stages, collector health uses a freshness probe, and metrics/refresh ingestion now follow the authoritative collector path.

## Live Run Summary

- Run mode: live, non-demo
- Platforms enabled: TMDB, YouTube, Bluesky
- Reddit: disabled
- Titles processed: 10
- Title failures: 0
- Total runtime: 819 seconds
- YouTube video mentions inserted: 59
- YouTube comments inserted: 1386
- Bluesky mentions inserted: 421
- Metrics snapshots upserted: 20
- Alerts created: 13
- System health issues: 0

## Effective Runtime Configuration

The live run used the heaviest current NLP and ingestion path:

- `sentiment_model='both'`
- `translate_before_sentiment=True`
- `enable_emotion_detection=True`
- `enable_sarcasm_detection=True`
- `enable_spam_filter=True`
- `enable_keyword_extraction=True`
- `enable_aspect_sentiment=False`
- `enable_youtube_comments=True`
- `youtube_comments_per_video=30`
- `polling_interval_minutes=30`

## Main Findings

### 1. Translation is likely one of the largest avoidable runtime costs

The live run reported:

- `translate_attempted=1199`
- `translate_count=1047`
- `translate_failures=0`
- `translate_skipped_english=3606`
- `sentiment_ms_total=541060.71`

The current translation path in `src/sam/utils/translation.py` is only partially parallelized. `translate_batch_to_english_with_stats()` iterates texts serially and each call submits work to a thread pool but waits for completion immediately. That means the executor rarely delivers meaningful batch concurrency.

Implication:

- The pipeline pays a large translation cost during ingestion.
- The current implementation leaves clear performance on the table.

Recommended actions:

1. Rework translation batching to execute bounded concurrent translations across texts.
2. Reuse detected language metadata instead of re-detecting language later.
3. Consider disabling translation in the hot ingestion path and reserving it for backfills or optional enrichment.

### 2. YouTube comment collection dominates external API activity

The successful run reported this YouTube quota delta for the collector cycle:

- `total_calls=210`
- `total_units=1200`
- `search.list=10`
- `videos.list=10`
- `commentThreads.list=190`

The overwhelming majority of YouTube API work came from comment collection. The runner already fetches comments in parallel with a semaphore, but the configured volume is still high.

Implication:

- The main live cycle is heavily shaped by comment depth, not only by platform search.
- Lowering comment volume would reduce both runtime and quota consumption.

Recommended actions:

1. Reduce `youtube_comments_per_video` for the primary live cycle.
2. Split comment enrichment into a secondary background job so title/video discovery stays fast.
3. Keep current bounded concurrency unless quota behavior is revalidated under higher load.

### 3. Titles are still processed serially at the top level

The collector processes titles one by one in `src/sam/scheduler/runner.py`. Some work inside a title is parallelized, but the pipeline does not process multiple titles concurrently.

Observed per-title timings from the live run:

- `The Bluff`: 36.3s
- `Young Sherlock`: 46.0s
- `Hoppers`: 51.7s
- `War Machine`: 52.0s
- `A Knight of the Seven Kingdoms`: 75.2s
- `The Pitt`: 76.1s
- `The Bride!`: 93.3s
- `Bridgerton`: 111.0s
- `Scream 7`: 131.7s
- `Hamnet`: 144.6s

Implication:

- Slow titles directly extend whole-cycle duration.
- Total runtime scales roughly linearly with title count.

Recommended actions:

1. Introduce bounded cross-title concurrency, likely 2 to 3 titles at a time.
2. Preserve per-title DB session isolation and per-platform safety checks.
3. Guard YouTube usage carefully so concurrency does not create quota spikes.

### 4. Language is effectively processed more than once

The current flow uses translation gating based on language detection and then later runs a separate language detection pass for persistence and downstream metrics.

Implication:

- CPU time is spent repeating language work on the same text payloads.

Recommended actions:

1. Return detected language information from the translation stage.
2. Reuse that language metadata during persistence instead of running a second pass.

### 5. First-cycle model warmup adds significant startup overhead

During the first live cycle after startup, the pipeline loads:

- RoBERTa sentiment model
- sarcasm model
- emotion model

These models loaded successfully, but that cost lands inside the first collector cycle.

Implication:

- Restarted collectors have a slow first cycle.
- The initial run feels worse than steady-state behavior.

Recommended actions:

1. Warm singleton NLP models on collector startup before the first live cycle.
2. Keep this separate from steady-state optimization work, since warmup mainly improves startup latency.

### 6. The current NLP stack is configured for maximum richness, not throughput

The current ingestion path combines:

- dual sentiment models
- translation before sentiment
- sarcasm detection
- emotion detection
- YouTube comments

Implication:

- This is the most expensive operating mode available.
- Runtime cost is not only a code issue; it is also a product/configuration choice.

Recommended actions:

1. Use a single primary sentiment model for live collection.
2. Keep richer enrichment steps for targeted workflows, offline jobs, or feature-flagged runs.
3. Treat `vader` or `roberta` as operational profiles, and avoid `both` in the default live ingestion path unless comparison is explicitly required.

## Priority Order

If the goal is the shortest path to a materially faster live cycle, the recommended order is:

1. Fix translation batching or disable translation in the hot path.
2. Reduce YouTube comment depth or move comment collection to a secondary job.
3. Add bounded concurrency across titles.
4. Reuse language-detection outputs across stages.
5. Pre-warm transformer models at collector startup.
6. Simplify the default live NLP profile.

## Recommended First Implementation

The best first engineering change is to make translation truly concurrent in batch mode.

Reasoning:

- It is a clear code-level inefficiency, not merely a feature tradeoff.
- It targets a heavy path that was active throughout the live run.
- It should improve runtime without removing features.

## Operational Note

The live run was successful and the stack remained healthy from ingestion through API and dashboard availability. The main concern is runtime cost, not correctness failure.
