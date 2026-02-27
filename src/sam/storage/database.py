"""
Database Connection and Session Management

Provides async database connection pool and session factory.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from loguru import logger
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sam.config import get_settings
from sam.storage.models import Base

# Global engine and session factory
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Get or create the async database engine."""
    global _engine

    if _engine is None:
        settings = get_settings()

        db_url = settings.database.effective_url(demo_mode=settings.demo_mode)
        if settings.demo_mode and db_url == settings.database.url:
            logger.warning(
                "[db] DEMO_MODE=true but DATABASE_DEMO_URL not set; "
                "demo data will be written into the primary database"
            )
        _engine = create_async_engine(
            db_url,
            echo=settings.database.echo,
            pool_size=settings.database.pool_size,
            pool_pre_ping=True,
            # Guard against zombie connections.  The 120s command_timeout
            # accommodates heavier queries like 24h mention-window fetches on
            # popular titles while still catching truly stuck connections.
            connect_args={"timeout": 10, "command_timeout": 120},
        )
        logger.info(f"[db] Created async engine for {db_url.split('@')[-1]}")

    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the async session factory."""
    global _session_factory

    if _session_factory is None:
        engine = get_engine()
        _session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        logger.info("[db] Created async session factory")

    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Get an async database session.

    Usage:
        async with get_session() as session:
            result = await session.execute(...)
    """
    factory = get_session_factory()
    session = factory()

    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_db() -> None:
    """
    Initialize the database.

    Creates all tables if they don't exist.
    For production, use Alembic migrations instead.
    """
    engine = get_engine()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("[db] Database tables initialized")


async def cleanup_stale_state() -> None:
    """Clean up stale leases and orphan pipeline runs on startup.

    This handles the case where a previous process was killed (SIGKILL / OOM)
    without releasing its lease or finishing its pipeline run.  Without this,
    the next runner would block forever trying to acquire the same lease row.
    """
    from sqlalchemy import text

    async with get_session() as session:
        # Expire any lease whose TTL has passed
        result = await session.execute(text("DELETE FROM leases WHERE expires_at < now()"))
        expired = getattr(result, "rowcount", 0) or 0

        # Mark any "running" pipeline runs older than 10 minutes as failed
        result = await session.execute(
            text(
                "UPDATE pipeline_runs SET status = 'failed', "
                "error = 'process terminated abnormally (stale run cleaned up)', "
                "finished_at = now() "
                "WHERE status = 'running' "
                "AND started_at < now() - interval '10 minutes'"
            )
        )
        orphans = getattr(result, "rowcount", 0) or 0

        if expired or orphans:
            logger.warning(
                f"[db] Startup cleanup: expired {expired} stale lease(s), "
                f"marked {orphans} orphan run(s) as failed"
            )


async def close_db() -> None:
    """Close database connections."""
    global _engine, _session_factory

    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("[db] Database connections closed")
