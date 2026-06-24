"""Async SQLAlchemy engine, session factory, and declarative base for GetAJob.

Usage::

    from core.database import create_engine, get_session
    from core.models import JobListing

    engine = create_engine()
    async with get_session(engine) as session:
        result = await session.execute(select(JobListing).limit(10))
        jobs = result.scalars().all()
"""

from __future__ import annotations as _annotations

import contextlib
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import structlog
from sqlalchemy import MetaData, event
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.ext.asyncio import (
    create_async_engine as _create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import Pool

from core.config import get_settings
from core.exceptions import ConfigurationError

__all__: list[str] = [
    "Base",
    "create_engine",
    "get_connection",
    "get_session",
    "run_migrations",
]

logger = structlog.get_logger(__name__)

# ── Naming convention for constraints / indexes ──────────────────────────────────

_NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=_NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base for all GetAJob ORM models."""

    metadata = metadata


# ── Engine factory ───────────────────────────────────────────────────────────────


def create_engine(**kwargs: Any) -> AsyncEngine:
    """Create and return a configured async SQLAlchemy engine.

    Reads connection parameters from the global ``GetAJobSettings`` singleton.
    Keyword arguments override individual settings.

    Args:
        **kwargs: Override any :class:`~core.config.DatabaseSettings` field
            (e.g. ``host="localhost"``, ``pool_size=5``).

    Returns:
        A ready-to-use :class:`~sqlalchemy.ext.asyncio.AsyncEngine`.

    Raises:
        ConfigurationError: If the DSN cannot be constructed.
    """
    settings = get_settings()
    db = settings.database

    # Allow per-call overrides of individual fields.
    host = kwargs.pop("host", db.host)
    port = kwargs.pop("port", db.port)
    database = kwargs.pop("database", db.database)
    user = kwargs.pop("user", db.user)
    password = kwargs.pop("password", db.password)
    min_size = kwargs.pop("min_connections", db.min_connections)
    max_size = kwargs.pop("max_connections", db.max_connections)

    if not database:
        msg = "Database name is empty — set GETAJOB_DATABASE__DATABASE or provide a ``database`` kwarg"
        raise ConfigurationError(msg)

    dsn = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"

    engine = _create_async_engine(
        dsn,
        pool_size=max_size,
        max_overflow=2,
        pool_pre_ping=True,
        echo=settings.debug,
        **kwargs,
    )

    logger.info(
        "Database engine created",
        host=host,
        port=port,
        database=database,
        min_size=min_size,
        max_size=max_size,
    )
    return engine


# ── Session factory ──────────────────────────────────────────────────────────────


def _session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return an :class:`~sqlalchemy.ext.asyncio.async_sessionmaker` for *engine*."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@contextlib.asynccontextmanager
async def get_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, Any]:
    """Async context manager that yields a database session.

    The session is automatically committed on success and rolled back on
    exception.  Always use this (or a similar context manager) rather than
    creating sessions manually to guarantee correct transactional boundaries.

    Usage::

        async with get_session(engine) as session:
            session.add(my_object)
            # auto-committed on exit
    """
    factory = _session_factory(engine)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@contextlib.asynccontextmanager
async def get_connection(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """Async context manager that yields a raw connection.

    Useful for bulk operations or raw SQL where the ORM session overhead is
    undesirable.
    """
    async with engine.begin() as conn:
        yield conn


# ── Schema helpers ───────────────────────────────────────────────────────────────


async def run_migrations(engine: AsyncEngine, *, drop_first: bool = False) -> None:
    """Create (or recreate) all tables registered on :class:`Base`.

    This is a convenience helper for **development and testing only**.
    In production, use Alembic for schema migrations.

    Args:
        engine: The async engine to run against.
        drop_first: If ``True``, drop all existing tables before creating.
    """
    if drop_first:
        logger.warning("Dropping all tables — this is destructive!")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database schema synchronized (create_all)")


# ── Connection event listeners ───────────────────────────────────────────────────


@event.listens_for(Pool, "connect", named=True)
def _on_connect(**kwargs: Any) -> None:
    """Log new database connections for observability."""
    logger.debug("Database connection opened", kwargs=kwargs)


@event.listens_for(Pool, "close", named=True)
def _on_close(**kwargs: Any) -> None:
    """Log database connection closures."""
    logger.debug("Database connection closed", kwargs=kwargs)
