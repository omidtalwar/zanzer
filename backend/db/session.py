"""Async database engine, session factory, and dev-time table creation.

Production uses Alembic migrations; for local dev we create tables directly
(`init_db`) so the app runs with zero setup on SQLite.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import settings
from backend.db.base import Base

# Ensure the SQLite file's directory exists (e.g. ./data).
if settings.database_url.startswith("sqlite") and "///" in settings.database_url:
    db_path = settings.database_url.split("///", 1)[1]
    if db_path and db_path not in (":memory:",):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(settings.database_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# For SQLite, enable WAL + a busy timeout so multiple processes (API, bot, and
# account workers) can read/write the same file concurrently without "database
# is locked" errors. No-op for PostgreSQL.
if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver glue
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


# Dev-only: columns added to existing tables after first release. create_all
# won't add columns to an existing table, so for SQLite we ALTER them in if
# missing (avoids dropping the dev DB). Production uses Alembic instead.
_SQLITE_COLUMN_ADDS = [
    ("subscriptions", "notice_state", "ALTER TABLE subscriptions ADD COLUMN notice_state VARCHAR(16) DEFAULT ''"),
    ("mt5_accounts", "terminal_path", "ALTER TABLE mt5_accounts ADD COLUMN terminal_path VARCHAR(512)"),
    ("payments", "provider", "ALTER TABLE payments ADD COLUMN provider VARCHAR(16) DEFAULT 'manual'"),
    ("payments", "invoice_id", "ALTER TABLE payments ADD COLUMN invoice_id VARCHAR(64)"),
    ("payments", "plan", "ALTER TABLE payments ADD COLUMN plan VARCHAR(16)"),
    ("payments", "days", "ALTER TABLE payments ADD COLUMN days INTEGER"),
    ("users", "tos_accepted_at", "ALTER TABLE users ADD COLUMN tos_accepted_at TIMESTAMP"),
    ("risk_settings", "max_daily_loss_usd", "ALTER TABLE risk_settings ADD COLUMN max_daily_loss_usd FLOAT DEFAULT 0.0"),
    ("account_snapshots", "yesterday_json", "ALTER TABLE account_snapshots ADD COLUMN yesterday_json TEXT"),
    ("locks", "explanation", "ALTER TABLE locks ADD COLUMN explanation TEXT"),
    ("trades", "screenshot_file_id", "ALTER TABLE trades ADD COLUMN screenshot_file_id VARCHAR(256)"),
    ("risk_settings", "pending_json", "ALTER TABLE risk_settings ADD COLUMN pending_json TEXT"),
    ("risk_settings", "pending_effective", "ALTER TABLE risk_settings ADD COLUMN pending_effective VARCHAR(40)"),
    ("trades", "gate_status", "ALTER TABLE trades ADD COLUMN gate_status VARCHAR(12) DEFAULT 'passed'"),
    ("trades", "entry_timeframe", "ALTER TABLE trades ADD COLUMN entry_timeframe VARCHAR(8)"),
    ("trades", "used_tradingview", "ALTER TABLE trades ADD COLUMN used_tradingview BOOLEAN"),
    ("trades", "close_requested", "ALTER TABLE trades ADD COLUMN close_requested BOOLEAN DEFAULT 0"),
    # V3/V4 — emotion_scores and trade journal tables created by create_all on first run.
]


async def init_db() -> None:
    # Importing models registers them on Base.metadata.
    from backend.db import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if settings.database_url.startswith("sqlite"):
            for table, column, ddl in _SQLITE_COLUMN_ADDS:
                res = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
                existing = {row[1] for row in res.fetchall()}
                if column not in existing:
                    await conn.exec_driver_sql(ddl)


async def get_session():
    """FastAPI dependency yielding an AsyncSession."""
    async with SessionLocal() as session:
        yield session
