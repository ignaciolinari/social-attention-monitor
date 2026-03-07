"""Lightweight tests for dashboard.pages.executive_overview."""

from __future__ import annotations

from contextlib import nullcontext

from dashboard.pages import PageContext
from dashboard.pages import executive_overview as overview_page


class _FakeStreamlit:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.successes: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.captions: list[str] = []
        self.metrics: list[tuple[str, object]] = []

    def header(self, *_a, **_kw):
        return None

    def markdown(self, *_a, **_kw):
        return None

    def subheader(self, *_a, **_kw):
        return None

    def columns(self, spec):
        if isinstance(spec, int):
            return tuple(nullcontext() for _ in range(spec))
        return tuple(nullcontext() for _ in spec)

    def info(self, message):
        self.infos.append(str(message))

    def success(self, message):
        self.successes.append(str(message))

    def warning(self, message):
        self.warnings.append(str(message))

    def error(self, message):
        self.errors.append(str(message))

    def caption(self, message):
        self.captions.append(str(message))

    def metric(self, label, value, **_kw):
        self.metrics.append((str(label), value))

    def divider(self):
        return None


class TestExecutiveOverviewPage:
    def test_render_shows_empty_state_when_no_trending(self, monkeypatch):
        fake_st = _FakeStreamlit()
        seen_alert_params = []

        def _get_json(path, params=None):
            if path == "/api/v1/alerts/system-health":
                return {"healthy": True, "issues": []}
            if path == "/api/v1/alerts/counts":
                return {"total": 0, "unacknowledged": 0, "counts": {}}
            if path == "/api/v1/alerts":
                seen_alert_params.append(params)
                return {"alerts": []}
            if path == "/api/v1/pipeline/health":
                return {"active_titles": 0, "total_mentions": 0, "mentions_last_24h": {}}
            raise AssertionError(f"unexpected path {path} params={params}")

        monkeypatch.setattr(overview_page, "st", fake_st)
        monkeypatch.setattr(overview_page, "get_trending_metrics", lambda **_kw: {"items": []})
        monkeypatch.setattr(overview_page, "get_json", _get_json)

        overview_page.render(PageContext(hours=24, window_hours=24, enabled_platforms=set()))

        assert any("No trending data yet" in msg for msg in fake_st.infos)
        assert "All systems operational" in fake_st.successes
        assert seen_alert_params == [{"hours": 24, "limit": 3, "unacknowledged_only": True}]

    def test_render_shows_metrics_and_unacknowledged_alerts(self, monkeypatch):
        fake_st = _FakeStreamlit()
        seen_alert_params = []

        def _get_json(path, params=None):
            if path == "/api/v1/alerts/system-health":
                return {
                    "healthy": False,
                    "issues": [
                        {"severity": "critical", "message": "Redis unreachable"},
                        {"severity": "warning", "message": "Quota nearly exhausted"},
                        {"severity": "info", "message": "Collector recovered"},
                        {"severity": "warning", "message": "Extra issue beyond preview"},
                    ],
                }
            if path == "/api/v1/alerts/counts":
                return {
                    "total": 5,
                    "unacknowledged": 3,
                    "counts": {"critical": 2, "warning": 2, "info": 1},
                }
            if path == "/api/v1/alerts":
                seen_alert_params.append(params)
                return {
                    "alerts": [
                        {"message": "Critical alert one", "acknowledged_at": None},
                        {"message": "Warning alert two", "acknowledged_at": None},
                        {"message": "Info alert three", "acknowledged_at": None},
                        {
                            "message": "Acknowledged alert",
                            "acknowledged_at": "2026-03-06T00:00:00+00:00",
                        },
                    ]
                }
            if path == "/api/v1/pipeline/health":
                return {
                    "active_titles": 7,
                    "total_mentions": 12345,
                    "mentions_last_24h": {"reddit": 10, "youtube": 20},
                    "latest_pipeline_runs": [{"status": "degraded"}],
                }
            raise AssertionError(f"unexpected path {path} params={params}")

        monkeypatch.setattr(overview_page, "st", fake_st)
        monkeypatch.setattr(
            overview_page,
            "get_trending_metrics",
            lambda **_kw: {
                "items": [
                    {
                        "title": {"title": "Dune: Part Two"},
                        "metrics": {"attention_index": 82.5, "mention_count": 420},
                    }
                ]
            },
        )
        monkeypatch.setattr(overview_page, "get_json", _get_json)

        overview_page.render(PageContext(hours=24, window_hours=24, enabled_platforms=set()))

        assert any("Dune: Part Two" in caption for caption in fake_st.captions)
        assert any("… and 1 more" in caption for caption in fake_st.captions)
        assert any(metric == ("Total", 5) for metric in fake_st.metrics)
        assert any(metric == ("Critical", 2) for metric in fake_st.metrics)
        assert any(metric == ("Unack", 3) for metric in fake_st.metrics)
        assert any(metric == ("Active Titles", 7) for metric in fake_st.metrics)
        assert any(metric == ("Total Mentions", "12,345") for metric in fake_st.metrics)
        assert any(metric == ("24h Mentions", 30) for metric in fake_st.metrics)
        assert any("Last run: ⚠️ degraded" in caption for caption in fake_st.captions)
        assert seen_alert_params == [{"hours": 24, "limit": 3, "unacknowledged_only": True}]
