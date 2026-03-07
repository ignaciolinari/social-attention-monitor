from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sam.storage import database


def _result(*, scalar_one_or_none=None, rowcount: int | None = None):
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar_one_or_none
    if rowcount is not None:
        result.rowcount = rowcount
    return result


@pytest.mark.asyncio
async def test_cleanup_stale_state_uses_db_time_and_logs_counts() -> None:
    session = AsyncMock()
    db_now = datetime(2026, 3, 6, 15, 0, 0, tzinfo=UTC)
    session.execute.side_effect = [
        _result(scalar_one_or_none=db_now),
        _result(rowcount=2),
        _result(rowcount=3),
    ]

    @asynccontextmanager
    async def session_override():
        yield session

    with (
        patch("sam.storage.database.get_session", session_override),
        patch("sam.storage.database.logger.warning") as mock_warning,
    ):
        await database.cleanup_stale_state()

    assert session.execute.await_count == 3

    delete_stmt = session.execute.await_args_list[1].args[0]
    update_stmt = session.execute.await_args_list[2].args[0]
    delete_params = delete_stmt.compile().params
    update_params = update_stmt.compile().params

    assert db_now in delete_params.values()
    assert update_params["finished_at"] == db_now
    assert db_now - timedelta(minutes=10) in update_params.values()
    mock_warning.assert_called_once_with(
        "[db] Startup cleanup: expired 2 stale lease(s), marked 3 orphan run(s) as failed"
    )


@pytest.mark.asyncio
async def test_cleanup_stale_state_skips_warning_when_nothing_to_do() -> None:
    session = AsyncMock()
    session.execute.side_effect = [
        _result(scalar_one_or_none=datetime(2026, 3, 6, 15, 0, 0, tzinfo=UTC)),
        _result(rowcount=0),
        _result(rowcount=0),
    ]

    @asynccontextmanager
    async def session_override():
        yield session

    with (
        patch("sam.storage.database.get_session", session_override),
        patch("sam.storage.database.logger.warning") as mock_warning,
    ):
        await database.cleanup_stale_state()

    mock_warning.assert_not_called()
