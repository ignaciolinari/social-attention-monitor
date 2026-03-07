"""Lightweight tests for dashboard.pages.alerts."""

from __future__ import annotations

from contextlib import nullcontext

from dashboard.pages import alerts as alerts_page


class _FakeStreamlit:
    def __init__(self) -> None:
        self.successes: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.markdowns: list[str] = []
        self.json_payloads: list[object] = []
        self.expanders: list[tuple[str, bool]] = []

    def subheader(self, *_a, **_kw):
        return None

    def success(self, message):
        self.successes.append(str(message))

    def warning(self, message):
        self.warnings.append(str(message))

    def error(self, message):
        self.errors.append(str(message))

    def markdown(self, message):
        self.markdowns.append(str(message))

    def json(self, payload):
        self.json_payloads.append(payload)

    def expander(self, label, expanded=False, **_kw):
        self.expanders.append((str(label), bool(expanded)))
        return nullcontext()


class TestAlertsPageSystemHealth:
    def test_system_health_renders_healthy_state(self, monkeypatch):
        fake_st = _FakeStreamlit()

        monkeypatch.setattr(alerts_page, "st", fake_st)
        monkeypatch.setattr(
            alerts_page,
            "get_json",
            lambda *_a, **_kw: {"healthy": True, "issues": []},
        )

        alerts_page._render_system_health()

        assert fake_st.successes == ["All systems operational"]
        assert not fake_st.warnings

    def test_system_health_renders_issue_details(self, monkeypatch):
        fake_st = _FakeStreamlit()

        monkeypatch.setattr(alerts_page, "st", fake_st)
        monkeypatch.setattr(
            alerts_page,
            "get_json",
            lambda *_a, **_kw: {
                "healthy": False,
                "issues": [
                    {
                        "severity": "critical",
                        "alert_type": "collector_failure",
                        "message": "Collector failed for title batch",
                        "details": {"platform": "youtube"},
                    },
                    {
                        "severity": "warning",
                        "alert_type": "redis_degraded",
                        "message": "Redis latency elevated",
                        "details": {},
                    },
                ],
            },
        )

        alerts_page._render_system_health()

        assert any("2 system issue(s) detected" in msg for msg in fake_st.warnings)
        assert any("Collector Failure" in label for label, _ in fake_st.expanders)
        assert any(expanded for _label, expanded in fake_st.expanders)
        assert any("Collector failed for title batch" in md for md in fake_st.markdowns)
        assert fake_st.json_payloads == [{"platform": "youtube"}]
