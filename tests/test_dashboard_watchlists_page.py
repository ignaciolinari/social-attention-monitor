"""Lightweight tests for dashboard.pages.watchlists."""

from __future__ import annotations

from contextlib import nullcontext

from dashboard.pages import PageContext
from dashboard.pages import watchlists as watchlists_page


class _FakeStreamlit:
    def __init__(self, *, text_values: dict[str, str], button_values: dict[str, bool]) -> None:
        self._text_values = text_values
        self._button_values = button_values
        self.successes: list[str] = []
        self.errors: list[str] = []
        self.rerun_called = False

    def header(self, *_a, **_kw):
        return None

    def markdown(self, *_a, **_kw):
        return None

    def expander(self, *_a, **_kw):
        return nullcontext()

    def text_input(self, _label, *, key=None, value="", **_kw):
        if key is None:
            return value
        return self._text_values.get(key, value)

    def button(self, _label, *, key=None, **_kw):
        if key is None:
            return False
        return self._button_values.get(key, False)

    def error(self, message):
        self.errors.append(str(message))

    def success(self, message):
        self.successes.append(str(message))

    def rerun(self):
        self.rerun_called = True

    def info(self, *_a, **_kw):
        return None

    def caption(self, *_a, **_kw):
        return None

    def container(self):
        return nullcontext()

    def columns(self, spec):
        return tuple(nullcontext() for _ in spec)

    def divider(self):
        return None


class TestWatchlistsPage:
    def test_parse_tmdb_ids(self):
        assert watchlists_page._parse_tmdb_ids("1396, 438631,95396") == [1396, 438631, 95396]

    def test_render_create_uses_json_body(self, monkeypatch):
        fake_st = _FakeStreamlit(
            text_values={"wl_new_name": "My Watchlist", "wl_new_ids": "1396, 438631"},
            button_values={"wl_create_btn": True},
        )
        calls = []

        monkeypatch.setattr(watchlists_page, "st", fake_st)
        monkeypatch.setattr(
            watchlists_page,
            "get_json",
            lambda *_args, **_kwargs: {"watchlists": [], "total_count": 0},
        )
        monkeypatch.setattr(
            watchlists_page,
            "post_json",
            lambda path, params=None, json_body=None: (
                calls.append((path, params, json_body)) or {"ok": True}
            ),
        )

        watchlists_page.render(PageContext(hours=24, window_hours=24, enabled_platforms=set()))

        assert calls == [
            ("/api/v1/watchlists", None, {"name": "My Watchlist", "tmdb_ids": [1396, 438631]})
        ]
        assert fake_st.rerun_called is True

    def test_render_update_uses_json_body(self, monkeypatch):
        fake_st = _FakeStreamlit(
            text_values={
                "wl_new_name": "",
                "wl_new_ids": "",
                "wl_name_abc": "Updated Watchlist",
                "wl_ids_abc": "1396, 438631",
            },
            button_values={"wl_save_abc": True},
        )
        calls = []

        monkeypatch.setattr(watchlists_page, "st", fake_st)
        monkeypatch.setattr(
            watchlists_page,
            "get_json",
            lambda *_args, **_kwargs: {
                "watchlists": [
                    {
                        "id": "abc",
                        "name": "Original",
                        "tmdb_ids": [1],
                        "created_at": "2026-03-05T00:00:00+00:00",
                    }
                ],
                "total_count": 1,
            },
        )
        monkeypatch.setattr(
            watchlists_page,
            "put_json",
            lambda path, params=None, json_body=None: (
                calls.append((path, params, json_body)) or {"ok": True}
            ),
        )

        watchlists_page.render(PageContext(hours=24, window_hours=24, enabled_platforms=set()))

        assert calls == [
            (
                "/api/v1/watchlists/abc",
                None,
                {"name": "Updated Watchlist", "tmdb_ids": [1396, 438631]},
            )
        ]
        assert fake_st.rerun_called is True
