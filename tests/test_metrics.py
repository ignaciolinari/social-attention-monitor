from datetime import UTC, datetime

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
