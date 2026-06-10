"""ORM models — the multi-user (SaaS) data foundation.

Each subscriber is a `User` (keyed by their Telegram id). They have a
subscription (access gating), risk settings, an optional MT5 account, a lock,
and audit/payment history. The terminal-farm worker (Phase B) will read/write
these per user.

`created_at` columns default in Python (UTC) so behaviour is identical on
SQLite (dev) and PostgreSQL (prod).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="user")     # user | admin
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | blocked
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # When the user accepted the Terms / risk disclaimer (None = not yet).
    tos_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    subscription: Mapped["Subscription"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    risk_settings: Mapped["RiskSettings"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    lock: Mapped["Lock"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    accounts: Mapped[list["MT5Account"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    plan: Mapped[str] = mapped_column(String(16), default="trial")    # trial | monthly | quarterly
    status: Mapped[str] = mapped_column(String(16), default="trial")  # trial | active | expired
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)  # admin user id
    # Tracks which expiry notice was last sent: "" | "reminded" | "expired".
    notice_state: Mapped[str] = mapped_column(String(16), default="")

    user: Mapped["User"] = relationship(back_populates="subscription")


class MT5Account(Base):
    __tablename__ = "mt5_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    login: Mapped[int] = mapped_column(Integer)
    server: Mapped[str] = mapped_column(String(128))
    broker: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # MT5 password, ENCRYPTED at rest (Fernet). Never store plaintext.
    password_encrypted: Mapped[str] = mapped_column(Text)
    # Terminal install path for this account's worker (terminal-farm model).
    terminal_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    account_type: Mapped[str] = mapped_column(String(16), default="trading")  # trading | investor
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | active | error
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="accounts")


class RiskSettings(Base):
    __tablename__ = "risk_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    max_trades_per_day: Mapped[int] = mapped_column(Integer, default=2)
    max_daily_loss_pct: Mapped[float] = mapped_column(Float, default=5.0)
    # Optional fixed daily loss cap in account currency (0 = use % only).
    max_daily_loss_usd: Mapped[float] = mapped_column(Float, default=0.0)
    max_risk_per_trade_pct: Mapped[float] = mapped_column(Float, default=4.0)
    max_consecutive_losses: Mapped[int] = mapped_column(Integer, default=2)
    max_account_exposure_pct: Mapped[float] = mapped_column(Float, default=5.0)

    user: Mapped["User"] = relationship(back_populates="risk_settings")


class Lock(Base):
    __tablename__ = "locks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    day: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD (daily lock)

    user: Mapped["User"] = relationship(back_populates="lock")


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AccountSnapshot(Base):
    """Latest live data for a user's account, written by the worker each cycle.

    One row per user (upserted), so the bot's /status can show live balance/PnL
    without itself connecting to MT5.
    """
    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    equity: Mapped[float] = mapped_column(Float, default=0.0)
    margin: Mapped[float] = mapped_column(Float, default=0.0)
    floating_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    open_positions: Mapped[int] = mapped_column(Integer, default=0)
    trades_today: Mapped[int] = mapped_column(Integer, default=0)
    daily_loss: Mapped[float] = mapped_column(Float, default=0.0)
    daily_loss_pct: Mapped[float] = mapped_column(Float, default=0.0)
    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0)
    exposure_pct: Mapped[float] = mapped_column(Float, default=0.0)
    any_limit_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # JSON list of yesterday's completed trades (written by worker, read by /yesterday).
    yesterday_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    method: Mapped[str] = mapped_column(String(16), default="crypto")
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | verified | rejected
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Provider integration (CryptoPay auto-confirm). "manual" = wallet+tx flow.
    provider: Mapped[str] = mapped_column(String(16), default="manual")
    invoice_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan: Mapped[str | None] = mapped_column(String(16), nullable=True)
    days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="payments")
