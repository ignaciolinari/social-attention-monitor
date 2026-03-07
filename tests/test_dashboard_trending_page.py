"""Lightweight tests for dashboard.pages.trending."""

from __future__ import annotations

from dashboard.pages import PageContext
from dashboard.pages import trending as trending_page


class _FakeStreamlit:
    def __init__(self) -> None:
        self.dataframes = []
        self.downloads: list[dict[str, object]] = []
        self.infos: list[str] = []
        self.captions: list[str] = []

    def header(self, *_a, **_kw):
        return None

    def info(self, message):
        self.infos.append(str(message))

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

    def caption(self, message):
        self.captions.append(str(message))


class TestTrendingPage:
    def test_render_adds_share_of_voice_and_csv_export(self, monkeypatch):
        fake_st = _FakeStreamlit()

        monkeypatch.setattr(trending_page, "st", fake_st)
        monkeypatch.setattr(
            trending_page,
            "get_trending_metrics",
            lambda **_kw: {
                "collected_at": "2026-03-06T00:00:00+00:00",
                "items": [
                    {
                        "title": {"id": "t1", "title": "Movie A", "media_type": "movie"},
                        "metrics": {
                            "attention_index": 90.0,
                            "hype_acceleration": 2.5,
                            "mention_count": 30,
                            "mention_velocity": 5.0,
                            "avg_sentiment": 0.4,
                            "reddit_mentions": 10,
                            "youtube_mentions": 20,
                            "bluesky_mentions": 3,
                            "snapshot_time": "2026-03-06T00:00:00+00:00",
                        },
                    },
                    {
                        "title": {"id": "t2", "title": "Movie B", "media_type": "movie"},
                        "metrics": {
                            "attention_index": 30.0,
                            "hype_acceleration": -1.0,
                            "mention_count": 10,
                            "mention_velocity": 1.0,
                            "avg_sentiment": -0.2,
                            "reddit_mentions": 2,
                            "youtube_mentions": 8,
                            "bluesky_mentions": 1,
                            "snapshot_time": "2026-03-06T00:00:00+00:00",
                        },
                    },
                ],
            },
        )

        trending_page.render(
            PageContext(hours=24, window_hours=24, enabled_platforms={"reddit", "youtube"})
        )

        assert len(fake_st.dataframes) == 1
        display_df = fake_st.dataframes[0]
        assert "share_of_voice_pct" in display_df.columns
        assert "bluesky_mentions" not in display_df.columns
        assert "title_id" not in display_df.columns
        assert display_df.iloc[0]["title"] == "Movie A"
        assert list(display_df["share_of_voice_pct"]) == [75.0, 25.0]

        assert len(fake_st.downloads) == 1
        download = fake_st.downloads[0]
        assert download["file_name"] == "trending.csv"
        assert download["key"] == "trending_csv"
        csv_text = download["data"].decode("utf-8")
        assert "share_of_voice_pct" in csv_text
        assert "Movie A" in csv_text
        assert any("Updated:" in caption for caption in fake_st.captions)
