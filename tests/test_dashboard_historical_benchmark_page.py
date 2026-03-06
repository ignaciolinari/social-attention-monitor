"""Lightweight tests for dashboard.pages.historical_benchmark."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

from dashboard.pages import PageContext
from dashboard.pages import historical_benchmark as benchmark_page


class _FakeFigure:
    def add_trace(self, *_a, **_kw):
        return None

    def update_layout(self, *_a, **_kw):
        return None


class _FakeStreamlit:
    def __init__(self) -> None:
        self.captions: list[str] = []
        self.warnings: list[str] = []

    def header(self, *_a, **_kw):
        return None

    def markdown(self, *_a, **_kw):
        return None

    def info(self, *_a, **_kw):
        return None

    def error(self, *_a, **_kw):
        return None

    def subheader(self, *_a, **_kw):
        return None

    def caption(self, msg):
        self.captions.append(str(msg))

    def warning(self, msg):
        self.warnings.append(str(msg))

    def selectbox(self, _label, options, **_kw):
        return options[0]

    def slider(self, *_a, **_kw):
        return 7

    def columns(self, spec):
        if isinstance(spec, int):
            return tuple(nullcontext() for _ in range(spec))
        return tuple(nullcontext() for _ in spec)

    def plotly_chart(self, *_a, **_kw):
        return None

    def metric(self, *_a, **_kw):
        return None

    def expander(self, *_a, **_kw):
        return nullcontext()

    def dataframe(self, *_a, **_kw):
        return None


class TestHistoricalBenchmarkPage:
    def test_caption_includes_contributor_count(self, monkeypatch):
        fake_st = _FakeStreamlit()
        selected = SimpleNamespace(id="tid-1", media_type="movie")

        monkeypatch.setattr(benchmark_page, "st", fake_st)
        monkeypatch.setattr(
            benchmark_page,
            "get_trending_metrics",
            lambda **_kw: {"items": [{"id": "tid-1", "title": "Movie", "media_type": "movie"}]},
        )
        monkeypatch.setattr(benchmark_page, "build_title_options", lambda _items: [selected])
        monkeypatch.setattr(benchmark_page, "title_option_label", lambda _v: "Movie")
        monkeypatch.setattr(
            benchmark_page,
            "get_json",
            lambda *_a, **_kw: {
                "target_points": [],
                "avg_trajectory": [{"day": 0, "avg_attention_index": 1.0, "sample_count": 1}],
                "comparison_count": 20,
                "comparison_titles_with_data": 7,
                "title": {"title": "Movie", "release_date": "2025-01-01T00:00:00+00:00"},
            },
        )
        monkeypatch.setattr(benchmark_page.go, "Figure", lambda: _FakeFigure())

        benchmark_page.render(PageContext(hours=24, window_hours=24, enabled_platforms=set()))

        assert any("20" in c and "7" in c for c in fake_st.captions)

    def test_warns_when_no_contributor_data(self, monkeypatch):
        fake_st = _FakeStreamlit()
        selected = SimpleNamespace(id="tid-1", media_type="movie")

        monkeypatch.setattr(benchmark_page, "st", fake_st)
        monkeypatch.setattr(
            benchmark_page,
            "get_trending_metrics",
            lambda **_kw: {"items": [{"id": "tid-1", "title": "Movie", "media_type": "movie"}]},
        )
        monkeypatch.setattr(benchmark_page, "build_title_options", lambda _items: [selected])
        monkeypatch.setattr(benchmark_page, "title_option_label", lambda _v: "Movie")
        monkeypatch.setattr(
            benchmark_page,
            "get_json",
            lambda *_a, **_kw: {
                "target_points": [],
                "avg_trajectory": [{"day": 0, "avg_attention_index": None, "sample_count": 0}],
                "comparison_count": 10,
                "comparison_titles_with_data": 0,
                "title": {"title": "Movie", "release_date": "2025-01-01T00:00:00+00:00"},
            },
        )
        monkeypatch.setattr(benchmark_page.go, "Figure", lambda: _FakeFigure())

        benchmark_page.render(PageContext(hours=24, window_hours=24, enabled_platforms=set()))

        assert fake_st.warnings
