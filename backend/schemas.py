"""Pydantic request/response schemas for the multi-user API.

Separate from the ORM models (backend/db/models.py) and from the MT5 data
models (backend/models.py).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RegisterRequest(BaseModel):
    telegram_id: int
    username: str | None = None


class RiskSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    max_trades_per_day: int
    max_daily_loss_pct: float
    max_daily_loss_usd: float = 0.0
    max_risk_per_trade_pct: float
    max_consecutive_losses: int
    max_account_exposure_pct: float


class RiskSettingsUpdate(BaseModel):
    # All optional; only provided fields are updated. Bounded to sane ranges.
    # 0 is allowed for the loss caps to mean "disabled".
    max_trades_per_day: int | None = Field(default=None, ge=1, le=100)
    max_daily_loss_pct: float | None = Field(default=None, ge=0, le=100)
    max_daily_loss_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    max_risk_per_trade_pct: float | None = Field(default=None, ge=0, le=100)
    max_consecutive_losses: int | None = Field(default=None, ge=1, le=100)
    max_account_exposure_pct: float | None = Field(default=None, ge=0, le=100)


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    plan: str
    status: str
    started_at: datetime
    expires_at: datetime | None


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    login: int
    server: str
    broker: str | None
    account_type: str
    status: str


class AddAccountRequest(BaseModel):
    login: int
    server: str
    password: str
    broker: str | None = None
    account_type: str = "trading"  # trading | investor


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    telegram_id: int
    username: str | None
    role: str
    status: str
    created_at: datetime
    subscription: SubscriptionOut | None = None
    risk_settings: RiskSettingsOut | None = None
    is_active: bool = False  # subscription currently valid?


class BroadcastRequest(BaseModel):
    message: str
    audience: str = "all"  # all | active | inactive


class PaymentSubmitRequest(BaseModel):
    tx_hash: str
    amount: float | None = None
    currency: str | None = "USDT"
    note: str | None = None


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    method: str
    amount: float | None
    currency: str | None
    tx_hash: str | None
    status: str
    note: str | None
    created_at: datetime
