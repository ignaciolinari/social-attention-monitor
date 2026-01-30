from datetime import UTC, datetime, timedelta

from sam.processors.metrics import MetricsCalculator


def test_metrics_calculate_accepts_naive_created_at() -> None:
    calc = MetricsCalculator()

    now_naive = datetime.now(UTC).replace(tzinfo=None)
    mentions = [
        {
            "created_at": now_naive,
            "author": "a",
            "platform": "reddit",
            "metrics": {"score": 1, "num_comments": 0},
            "sentiment": {"compound": 0.1},
        }
    ]

    result = calc.calculate(mentions, window_hours=24)
    assert result.mention_count == 1


def test_metrics_calculate_accepts_aware_created_at() -> None:
    calc = MetricsCalculator()
    now_aware = datetime.now(UTC)

    mentions = [
        {
            "created_at": now_aware,
            "author": "a",
            "platform": "youtube",
            "metrics": {"like_count": 1, "comment_count": 0},
            "sentiment": {"compound": -0.2},
        }
    ]

    result = calc.calculate(mentions, window_hours=24)
    assert result.mention_count == 1


def test_metrics_calculate_respects_window_end() -> None:
    calc = MetricsCalculator()

    window_end = datetime.now(UTC)
    in_window = window_end.replace(minute=0, second=0, microsecond=0)
    out_of_window = in_window - timedelta(hours=30)

    mentions = [
        {
            "created_at": in_window,
            "author": "a",
            "platform": "reddit",
            "metrics": {"score": 1, "num_comments": 0},
            "sentiment": {"compound": 0.1},
        },
        {
            "created_at": out_of_window,
            "author": "b",
            "platform": "youtube",
            "metrics": {"like_count": 1, "comment_count": 0},
            "sentiment": {"compound": -0.1},
        },
    ]

    result = calc.calculate(mentions, window_hours=24, window_end=window_end)
    assert result.mention_count == 1
