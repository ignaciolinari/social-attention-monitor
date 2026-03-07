"""Lightweight tests for dashboard.pages.title_comparison."""

from __future__ import annotations

from types import SimpleNamespace

from dashboard.pages import PageContext
from dashboard.pages import title_comparison as comparison_page


class _FakeFigure:
    def __init__(self) -> None:
        self.traces = []

    def add_trace(self, trace):
        self.traces.append(trace)

    def update_layout(self, *_a, **_kw):
        return None


class _FakeStreamlit:
    def __init__(self, selected) -> None:
        self.selected = selected
        self.dataframes = []
        self.downloads: list[dict[str, object]] = []
        self.plot_calls = 0
        self.infos: list[str] = []
        self.warnings: list[str] = []

    def header(self, *_a, **_kw):
        return None

    def markdown(self, *_a, **_kw):
        return None

    def info(self, message):
        self.infos.append(str(message))

    def warning(self, message):
        self.warnings.append(str(message))

    def subheader(self, *_a, **_kw):
        return None

    def multiselect(self, *_a, **_kw):
        return self.selected

    def dataframe(self, df, **_kw):
        self.dataframes.append(df.copy())

    def download_button(self, label, data, file_name, mime, key):
        self.downloads.append(
            {
                "label": label,
                "data": data,
                "file_name": file_name,
                "mime": mime,
                "key": key,
            }
        )

    def plotly_chart(self, *_a, **_kw):
        self.plot_calls += 1


class TestTitleComparisonPage:
    def test_render_adds_csv_export_for_summary(self, monkeypatch):
        selected = [
            SimpleNamespace(id="t1", title="Movie A", media_type="movie"),
            SimpleNamespace(id="t2", title="Movie B", media_type="movie"),
        ]
        fake_st = _FakeStreamlit(selected)

        monkeypatch.setattr(comparison_page, "st", fake_st)
        monkeypatch.setattr(
            comparison_page,
            "get_trending_metrics",
            lambda **_kw: {"items": [{"id": "t1"}, {"id": "t2"}]},
        )
        monkeypatch.setattr(comparison_page, "build_title_options", lambda _items: selected)
        monkeypatch.setattr(comparison_page, "title_option_label", lambda opt: opt.title)
        monkeypatch.setattr(comparison_page.go, "Figure", _FakeFigure)
        monkeypatch.setattr(
            comparison_page,
            "get_json",
            lambda *_a, **_kw: {
                "series": [
                    {
                        "title": {"title": "Movie A", "media_type": "movie"},
                        "points": [
                            {
                                "snapshot_time": "2026-03-06T00:00:00+00:00",
                                "attention_index": 90.0,
                                "hype_acceleration": 2.1,
                                "mention_count": 30,
                                "mention_velocity": 5.0,
                                "avg_sentiment": 0.3,
                                "sentiment_volatility": 0.1,
                                "negative_ratio": 0.2,
                            }
                        ],
                    },
                    {
                        "title": {"title": "Movie B", "media_type": "movie"},
                        "points": [
                            {
                                "snapshot_time": "2026-03-06T00:00:00+00:00",
                                "attention_index": 50.0,
                                "hype_acceleration": -0.5,
                                "mention_count": 12,
                                "mention_velocity": 2.0,
                                "avg_sentiment": -0.1,
                                "sentiment_volatility": 0.4,
                                "negative_ratio": 0.6,
                            }
                        ],
                    },
                ],
                "missing_ids": [],
            },
        )

        comparison_page.render(PageContext(hours=24, window_hours=24, enabled_platforms=set()))

        assert fake_st.plot_calls == 3
        assert len(fake_st.dataframes) == 1
        summary_df = fake_st.dataframes[0]
        assert list(summary_df.columns) == [
            "Title",
            "Type",
            "Attention Index",
            "Hype Accel",
            "Mentions",
            "Velocity",
            "Avg Sentiment",
            "Sentiment Vol",
            "Neg Ratio",
        ]

        assert len(fake_st.downloads) == 1
        download = fake_st.downloads[0]
        assert download["file_name"] == "title_comparison.csv"
        assert download["key"] == "compare_csv"
        csv_text = download["data"].decode("utf-8")
        assert "Hype Accel" in csv_text
        assert "Sentiment Vol" in csv_text
        assert "Movie A" in csv_text
