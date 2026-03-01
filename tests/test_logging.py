"""Tests for sam.logging — loguru configuration helpers."""

from __future__ import annotations

from unittest.mock import patch

from sam.logging import _format_extra, setup_logging


class TestFormatExtra:
    """Tests for _format_extra helper."""

    def test_empty_extra(self) -> None:
        record: dict = {"extra": {}}
        assert _format_extra(record) == ""

    def test_no_extra_key(self) -> None:
        record: dict = {}
        assert _format_extra(record) == ""

    def test_single_key(self) -> None:
        record: dict = {"extra": {"run_id": "abc"}}
        assert _format_extra(record) == " [run_id=abc]"

    def test_multiple_keys(self) -> None:
        record: dict = {"extra": {"run_id": "abc", "title": "Movie"}}
        result = _format_extra(record)
        assert "run_id=abc" in result
        assert "title=Movie" in result
        assert result.startswith(" [") and result.endswith("]")

    def test_none_values_filtered(self) -> None:
        record: dict = {"extra": {"run_id": "abc", "title": None}}
        result = _format_extra(record)
        assert "title" not in result
        assert "run_id=abc" in result

    def test_all_none_returns_empty(self) -> None:
        record: dict = {"extra": {"a": None, "b": None}}
        assert _format_extra(record) == ""


class TestSetupLogging:
    """Tests for setup_logging configuration function."""

    @patch("sam.logging.logger")
    @patch("sam.logging.get_settings")
    def test_setup_removes_default_and_adds_handler(
        self, mock_settings: object, mock_logger: object
    ) -> None:
        from types import SimpleNamespace

        mock_settings.return_value = SimpleNamespace(  # type: ignore[attr-defined]
            log_level="DEBUG", log_json=False
        )
        setup_logging()
        mock_logger.remove.assert_called_once()  # type: ignore[attr-defined]
        mock_logger.add.assert_called_once()  # type: ignore[attr-defined]
        mock_logger.configure.assert_called_once()  # type: ignore[attr-defined]

    @patch("sam.logging.logger")
    @patch("sam.logging.get_settings")
    def test_setup_passes_json_flag(self, mock_settings: object, mock_logger: object) -> None:
        from types import SimpleNamespace

        mock_settings.return_value = SimpleNamespace(  # type: ignore[attr-defined]
            log_level="INFO", log_json=True
        )
        setup_logging()
        call_kwargs = mock_logger.add.call_args  # type: ignore[attr-defined]
        assert call_kwargs[1]["serialize"] is True
