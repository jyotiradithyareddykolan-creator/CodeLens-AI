from typing import AsyncGenerator, Generator
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

# Base class for SQLAlchemy declarative models
class Base(DeclarativeBase):
    pass


# Check if using SQLite
is_sync_sqlite = "sqlite" in settings.DATABASE_URL.lower()
is_async_sqlite = "sqlite" in settings.ASYNC_DATABASE_URL.lower()

# Sync Engine & Session Factory (for Alembic, Celery, sync scripts)
sync_engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DATABASE_ECHO,
    pool_pre_ping=not is_sync_sqlite,
    connect_args={"check_same_thread": False} if is_sync_sqlite else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine)


# Async Engine & Session Factory (for FastAPI endpoints)
async_engine = create_async_engine(
    settings.ASYNC_DATABASE_URL,
    echo=settings.DATABASE_ECHO,
    future=True,
    pool_pre_ping=not is_async_sqlite,
    connect_args={"check_same_thread": False} if is_async_sqlite else {},
)
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Generator:
    """Dependency for synchronous database sessions (e.g. background tasks or scripts)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for FastAPI route handlers providing an asynchronous session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
