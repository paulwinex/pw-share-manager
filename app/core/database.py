from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.settings import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    return create_async_engine(
        f"sqlite+aiosqlite:///{settings.db_path}",
        echo=False,
    )


engine = _make_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    from app.db import models  # noqa: F401  ensure models are registered

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate)


def _migrate(sync_conn) -> None:
    columns = {
        row[1]
        for row in sync_conn.execute(text("PRAGMA table_info(shares)")).fetchall()
    }
    if "comment" not in columns:
        sync_conn.execute(text("ALTER TABLE shares ADD COLUMN comment VARCHAR(255)"))
    if "wide_links" not in columns:
        # Existing rows keep Samba's default: symlinks stay inside the share.
        sync_conn.execute(
            text(
                "ALTER TABLE shares ADD COLUMN wide_links BOOLEAN NOT NULL DEFAULT 0"
            )
        )

    sync_conn.execute(text("""
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id VARCHAR(36) PRIMARY KEY,
            user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash VARCHAR(64) UNIQUE NOT NULL,
            expires_at DATETIME NOT NULL,
            created_at DATETIME NOT NULL
        )
    """))


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
