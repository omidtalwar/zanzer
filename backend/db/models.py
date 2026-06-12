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
    # Anti-gaming: loosening a rule is deferred. The looser values wait here
    # (JSON) until pending_effective (an ISO-8601 UTC timestamp).
    pending_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_effective: Mapped[str | None] = mapped_column(String(40), nullable=True)

    user: Mapped["User"] = relationship(back_populates="risk_settings")


class Lock(Base):
    __tablename__ = "locks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    day: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD (daily lock)
    # Trader's explanation when locked by psychology engine (score < 50).
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

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


class Trade(Base):
    """One row per MT5 position — written by the worker when a position opens/closes.

    The worker compares open positions each cycle to detect new entries and exits,
    then triggers the journal FSM in the bot.
    """
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # MT5 position ticket — unique per user.
    ticket: Mapped[int] = mapped_column(Integer, index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(8))     # BUY | SELL
    volume: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    sl: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Journal linkage.
    entry_journal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_journal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entry_prompted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_prompted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entry_reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    exit_reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    # open | closed | entry_skipped | exit_skipped | gate_closed
    status: Mapped[str] = mapped_column(String(24), default="open")
    # Telegram file_id of a chart screenshot the trader attached (optional).
    screenshot_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Pre-trade gate (V4.1): qualifying questions answered before the trade is
    # allowed to stand. Default 'passed' so pre-existing trades are never gated.
    gate_status: Mapped[str] = mapped_column(String(12), default="passed")  # passed | pending | failed
    entry_timeframe: Mapped[str | None] = mapped_column(String(8), nullable=True)
    used_tradingview: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Set when the gate fails/times out; the worker closes the position next cycle.
    close_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AppSetting(Base):
    """Key-value store for runtime-editable settings (admin dashboard).

    Used for AI coach config (provider, model, encrypted API keys, enabled)
    so an admin can change them live without redeploying. Secret values are
    Fernet-encrypted before storage (see repositories.set_app_setting).
    """
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class EmotionScore(Base):
    """Daily emotion/discipline score per user. One row per user per date.

    Score starts at 100 and is deducted each cycle by the psychology engine.
    Falls below 50 → auto-lock + alert. Resets next trading day.
    """
    __tablename__ = "emotion_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD server day
    score: Mapped[int] = mapped_column(Integer, default=100)
    # JSON list of deduction events: [{"reason": "...", "delta": -10, "ts": "..."}]
    events_json: Mapped[str] = mapped_column(Text, default="[]")
    locked_by_score: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class TradeJournal(Base):
    """One journal entry per trade per phase (entry or exit).

    The bot FSM writes this after the trader answers all questions.
    """
    __tablename__ = "trade_journals"

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(8))          # entry | exit

    # Entry journal fields.
    setup_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    emotion_entry: Mapped[str | None] = mapped_column(String(32), nullable=True)
    plan_followed: Mapped[str | None] = mapped_column(String(16), nullable=True)  # yes | mostly | no
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)        # 1–10

    # Exit journal fields.
    exit_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)   # tp | sl | manual | partial
    plan_followed_exit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    mistakes: Mapped[str | None] = mapped_column(Text, nullable=True)
    emotion_exit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)            # 1–5

    skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
