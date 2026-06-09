"""Pydantic models describing account, trades, and risk status.

These mirror the read-only data V1 needs from MT5. Persistence models
(SQLAlchemy) arrive in V3 (Trading Journal).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AccountInfo(BaseModel):
    login: int
    broker: str
    server: str
    currency: str
    balance: float
    equity: float
    margin: float
    margin_free: float
    profit: float  # floating P/L of open positions


class OpenPosition(BaseModel):
    ticket: int
    symbol: str
    direction: str  # "BUY" | "SELL"
    volume: float
    price_open: float
    price_current: float
    sl: float
    tp: float
    profit: float
    time: datetime


class HistoryDeal(BaseModel):
    ticket: int
    position_id: int
    symbol: str
    direction: str  # "BUY" | "SELL" | ""
    entry: str       # "IN" (open) | "OUT" (close) | "INOUT" | ""
    volume: float
    price: float
    profit: float    # nonzero only on closing deals
    time: datetime


class RiskStatus(BaseModel):
    """Snapshot of how today's activity compares to the risk rules."""

    trades_today: int
    max_trades_per_day: int
    daily_loss: float            # realized + floating, account currency (negative = loss)
    daily_loss_pct: float        # relative to start-of-day balance
    max_daily_loss_pct: float
    max_daily_loss_usd: float = 0.0
    consecutive_losses: int
    max_consecutive_losses: int
    exposure_pct: float          # margin / equity * 100
    max_account_exposure_pct: float

    # Derived flags
    daily_trade_limit_hit: bool
    daily_loss_limit_hit: bool
    consecutive_loss_limit_hit: bool
    exposure_limit_hit: bool

    @property
    def any_limit_hit(self) -> bool:
        return (
            self.daily_trade_limit_hit
            or self.daily_loss_limit_hit
            or self.consecutive_loss_limit_hit
            or self.exposure_limit_hit
        )


class StatusResponse(BaseModel):
    """Payload for the /status endpoint and the Telegram /status command."""

    account: AccountInfo
    open_positions: list[OpenPosition]
    risk: RiskStatus
    generated_at: datetime


# --- V2: Risk Enforcement Engine -------------------------------------------

class RiskLimits(BaseModel):
    """The risk thresholds. Per-user in the SaaS model; falls back to the
    global settings (PRD defaults) for the personal/single-user path.

    A threshold of 0 means "disabled" (not enforced), so a user can pick e.g.
    a fixed $ daily loss instead of a %."""

    max_trades_per_day: int
    max_daily_loss_pct: float
    max_daily_loss_usd: float = 0.0
    max_risk_per_trade_pct: float
    max_consecutive_losses: int
    max_account_exposure_pct: float

    @classmethod
    def from_settings(cls) -> "RiskLimits":
        from backend.config import settings
        return cls(
            max_trades_per_day=settings.max_trades_per_day,
            max_daily_loss_pct=settings.max_daily_loss_pct,
            max_daily_loss_usd=settings.max_daily_loss_usd,
            max_risk_per_trade_pct=settings.max_risk_per_trade_pct,
            max_consecutive_losses=settings.max_consecutive_losses,
            max_account_exposure_pct=settings.max_account_exposure_pct,
        )

    @classmethod
    def from_orm(cls, rs) -> "RiskLimits":
        """Build from a db.models.RiskSettings row."""
        return cls(
            max_trades_per_day=rs.max_trades_per_day,
            max_daily_loss_pct=rs.max_daily_loss_pct,
            max_daily_loss_usd=getattr(rs, "max_daily_loss_usd", 0.0) or 0.0,
            max_risk_per_trade_pct=rs.max_risk_per_trade_pct,
            max_consecutive_losses=rs.max_consecutive_losses,
            max_account_exposure_pct=rs.max_account_exposure_pct,
        )


class LockState(BaseModel):
    """Whether trading is locked, why, and since when. Persisted to disk."""

    locked: bool = False
    reason: str | None = None
    locked_at: datetime | None = None
    # Date (YYYY-MM-DD, UTC) the lock applies to. Daily limits auto-clear on a
    # new day; a manual lock has day=None and persists until /unlock.
    day: str | None = None


class EnforcementAction(BaseModel):
    """One action the engine decided on for this cycle."""

    type: str          # "WARN" | "LOCK" | "CLOSE_ALL"
    reason: str
    executed: bool      # True if actually performed; False if suppressed (dry_run)
    detail: str = ""


class EnforcementResult(BaseModel):
    mode: str           # "dry_run" | "live"
    any_limit_hit: bool
    actions: list[EnforcementAction]
    generated_at: datetime
