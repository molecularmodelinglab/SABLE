"""Database connection and session management.

This module provides database connectivity using SQLAlchemy with support for
both synchronous and asynchronous operations.
"""

import os
from typing import Generator, AsyncGenerator
from contextlib import contextmanager, asynccontextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import Pool

# Get database URL from environment
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://sable_user:sable_password@localhost:5432/sable"
)

# Convert to async URL for asyncpg
ASYNC_DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

# Create base class for models
Base = declarative_base()

# Synchronous engine and session
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,  # Verify connections before using
    pool_size=10,
    max_overflow=20,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true"
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# Asynchronous engine and session
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true"
)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False
)


# Connection pooling event listeners
@event.listens_for(Pool, "connect")
def receive_connect(dbapi_conn, connection_record):
    """Set connection parameters on new connections."""
    # Set timezone to UTC
    cursor = dbapi_conn.cursor()
    cursor.execute("SET timezone='UTC'")
    cursor.close()


def get_db() -> Generator[Session, None, None]:
    """
    Dependency for getting a database session in FastAPI endpoints.

    Usage:
        @app.get("/items/")
        def read_items(db: Session = Depends(get_db)):
            return db.query(Item).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for getting an async database session in FastAPI endpoints.

    Usage:
        @app.get("/items/")
        async def read_items(db: AsyncSession = Depends(get_async_db)):
            result = await db.execute(select(Item))
            return result.scalars().all()
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@contextmanager
def get_db_context():
    """
    Context manager for database sessions outside of FastAPI.

    Usage:
        with get_db_context() as db:
            user = db.query(User).first()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@asynccontextmanager
async def get_async_db_context():
    """
    Async context manager for database sessions outside of FastAPI.

    Usage:
        async with get_async_db_context() as db:
            result = await db.execute(select(User))
            user = result.scalars().first()
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def init_db():
    """
    Initialize database by creating all tables.
    This is for development only - use Alembic migrations in production.
    """
    from server.models import user, session as session_model, run, experiment, conversation, audit
    Base.metadata.create_all(bind=engine)


async def init_db_async():
    """
    Initialize database asynchronously.
    This is for development only - use Alembic migrations in production.
    """
    from server.models import user, session as session_model, run, experiment, conversation, audit
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def drop_db():
    """
    Drop all database tables.
    WARNING: This will delete all data! Only use for development/testing.
    """
    Base.metadata.drop_all(bind=engine)


async def drop_db_async():
    """
    Drop all database tables asynchronously.
    WARNING: This will delete all data! Only use for development/testing.
    """
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
