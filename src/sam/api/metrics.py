"""Lightweight application-level metrics for observability.

Exposes counters and histograms via a ``/metrics`` endpoint that returns a
Prometheus-compatible text format.  All state is kept in-process (no external
dependencies) — suitable for single-worker deployments and development.  For
multi-worker production, replace with ``prometheus-client`` or
``prometheus-fastapi-instrumentator``.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any

# ---------------------------------------------------------------------------
# Metric primitives
# ---------------------------------------------------------------------------

_lock = threading.Lock()

_counters: dict[str, float] = defaultdict(float)
_histograms: dict[str, list[float]] = defaultdict(list)

# Maximum observations to keep per histogram to avoid unbounded memory.
_HISTOGRAM_MAX_OBSERVATIONS = 5_000


def counter_inc(name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
    """Increment a counter metric."""
    key = _make_key(name, labels)
    with _lock:
        _counters[key] += value


def histogram_observe(name: str, value: float, labels: dict[str, str] | None = None) -> None:
    """Record an observation for a histogram metric."""
    key = _make_key(name, labels)
    with _lock:
        bucket = _histograms[key]
        if len(bucket) >= _HISTOGRAM_MAX_OBSERVATIONS:
            # Evict oldest 20 % to keep memory bounded.
            del bucket[: _HISTOGRAM_MAX_OBSERVATIONS // 5]
        bucket.append(value)


def _make_key(name: str, labels: dict[str, str] | None) -> str:
    if not labels:
        return name
    parts = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return f"{name}{{{parts}}}"


# ---------------------------------------------------------------------------
# Snapshot helper
# ---------------------------------------------------------------------------


def snapshot() -> dict[str, Any]:
    """Return a JSON-friendly snapshot of all metrics."""
    with _lock:
        counters_copy = dict(_counters)
        histograms_summary: dict[str, dict[str, float]] = {}
        for key, values in _histograms.items():
            if not values:
                continue
            sorted_v = sorted(values)
            n = len(sorted_v)
            histograms_summary[key] = {
                "count": n,
                "sum": sum(sorted_v),
                "min": sorted_v[0],
                "max": sorted_v[-1],
                "p50": sorted_v[n // 2],
                "p95": sorted_v[int(n * 0.95)],
                "p99": sorted_v[int(n * 0.99)],
            }

    return {"counters": counters_copy, "histograms": histograms_summary}


def prometheus_text() -> str:
    """Render metrics in Prometheus text exposition format."""
    lines: list[str] = []
    emitted_types: set[str] = set()
    with _lock:
        for key, value in sorted(_counters.items()):
            name, _labels = _parse_key(key)
            if name not in emitted_types:
                lines.append(f"# TYPE {name} counter")
                emitted_types.add(name)
            lines.append(f"{key} {value}")
        for key, values in sorted(_histograms.items()):
            if not values:
                continue
            name, _labels = _parse_key(key)
            sorted_v = sorted(values)
            n = len(sorted_v)
            if name not in emitted_types:
                lines.append(f"# TYPE {name} summary")
                emitted_types.add(name)
            base = key.split("{")[0]
            label_part = "{" + key.split("{")[1] if "{" in key else ""
            count_key = f"{base}_count{label_part}" if label_part else f"{base}_count"
            sum_key = f"{base}_sum{label_part}" if label_part else f"{base}_sum"
            lines.append(f"{count_key} {n}")
            lines.append(f"{sum_key} {sum(sorted_v):.4f}")

    return "\n".join(lines) + "\n"


def _parse_key(key: str) -> tuple[str, str]:
    if "{" in key:
        name = key[: key.index("{")]
        labels = key[key.index("{") :]
    else:
        name = key
        labels = ""
    return name, labels


# ---------------------------------------------------------------------------
# Convenience timer context manager
# ---------------------------------------------------------------------------


class Timer:
    """Context manager that records elapsed time to a histogram metric."""

    def __init__(self, metric_name: str, labels: dict[str, str] | None = None) -> None:
        self.metric_name = metric_name
        self.labels = labels
        self._start: float = 0.0

    def __enter__(self) -> Timer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        elapsed = time.perf_counter() - self._start
        histogram_observe(self.metric_name, elapsed, self.labels)
