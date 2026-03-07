"""DB titles listing endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from sam.api import dependencies as deps
from sam.api.schemas import DbTitleResponse, TitlesResponse

router = APIRouter(prefix="/api/v1/db", tags=["titles"])


@router.get("/titles", response_model=TitlesResponse)
async def db_list_titles(
    q: str | None = Query(None, description="Substring search in DB titles"),
    include_inactive: bool = Query(
        False,
        description="Include inactive historical titles as well as the currently tracked set",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> TitlesResponse:
    async with deps.get_session() as session:
        count_stmt = select(func.count()).select_from(deps.TitleModel)
        if not include_inactive:
            count_stmt = count_stmt.where(deps.TitleModel.is_active.is_(True))
        if q:
            count_stmt = count_stmt.where(
                deps.TitleModel.title.ilike(f"%{deps.escape_like(q)}%", escape="\\")
            )
        count_result = await session.execute(count_stmt)
        total_count = int(count_result.scalar_one())

        rows = await deps.list_titles(
            session,
            query=q,
            limit=limit + 1,
            offset=offset,
            include_inactive=include_inactive,
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        next_offset = offset + limit if has_more else None

        return TitlesResponse(
            titles=[
                DbTitleResponse(
                    id=str(t.id),
                    tmdb_id=t.tmdb_id,
                    title=t.title,
                    media_type=t.media_type,
                    release_date=t.release_date.isoformat() if t.release_date else None,
                    popularity=t.popularity,
                    is_active=bool(t.is_active),
                )
                for t in page
            ],
            total_count=total_count,
            next_offset=next_offset,
        )
