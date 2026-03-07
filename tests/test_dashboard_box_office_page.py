"""Lightweight tests for dashboard.pages.box_office."""

from __future__ import annotations

from contextlib import nullcontext

from dashboard.pages import PageContext
from dashboard.pages import box_office as box_office_page


class _FakeFigure:
    def update_layout(self, *_a, **_kw):
        return None


class _FakeStreamlit:
    def __init__(self) -> None:
        self.metrics: list[tuple[str, object]] = []
        self.captions: list[str] = []
        self.dataframes = []
        self.plot_calls = 0
        self.infos: list[str] = []

    def header(self, *_a, **_kw):
        return None

    def markdown(self, *_a, **_kw):
        return None

    def info(self, message):
        self.infos.append(str(message))

    def subheader(self, *_a, **_kw):
        return None

    def columns(self, spec):
        if isinstance(spec, int):
            return tuple(nullcontext() for _ in range(spec))
        return tuple(nullcontext() for _ in spec)

    def metric(self, label, value, **_kw):
        self.metrics.append((str(label), value))

    def caption(self, message):
        self.captions.append(str(message))

    def plotly_chart(self, *_a, **_kw):
        self.plot_calls += 1

    def dataframe(self, df, **_kw):
        self.dataframes.append(df.copy())


class TestBoxOfficePage:
    def test_render_shows_correlation_metrics(self, monkeypatch):
        fake_st = _FakeStreamlit()

        monkeypatch.setattr(box_office_page, "st", fake_st)
        monkeypatch.setattr(box_office_page.px, "scatter", lambda *_a, **_kw: _FakeFigure())
        monkeypatch.setattr(box_office_page.px, "bar", lambda *_a, **_kw: _FakeFigure())
        monkeypatch.setattr(
            box_office_page,
            "get_json",
            lambda *_a, **_kw: {
                "items": [
                    {
                        "title": "Movie A",
                        "media_type": "movie",
                        "attention_index": 10.0,
                        "mention_count": 100,
                        "avg_sentiment": 0.2,
                        "revenue": 1000.0,
                        "budget": 500.0,
                    },
                    {
                        "title": "Movie B",
                        "media_type": "movie",
                        "attention_index": 20.0,
                        "mention_count": 200,
                        "avg_sentiment": 0.5,
                        "revenue": 3000.0,
                        "budget": 1000.0,
                    },
                ]
            },
        )

        box_office_page.render(PageContext(hours=24, window_hours=24, enabled_platforms=set()))

        assert any(metric == ("Pearson correlation", "1.000") for metric in fake_st.metrics)
        assert any(metric == ("Spearman correlation", "1.000") for metric in fake_st.metrics)
        assert any(metric == ("Sample size (titles with revenue)", 2) for metric in fake_st.metrics)
        assert any(
            "Correlation > 0.3 suggests social attention correlates" in c for c in fake_st.captions
        )
        assert fake_st.plot_calls == 2
        assert len(fake_st.dataframes) == 1
