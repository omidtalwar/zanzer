"""Multi-user account routes (Phase A).

These manage subscribers in the database. They do NOT touch MT5 yet — per-user
broker connection arrives in Phase B (terminal-farm workers).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend import repositories as repo
from backend.db.session import get_session
from backend.schemas import (
    AccountOut,
    AddAccountRequest,
    PaymentOut,
    PaymentSubmitRequest,
    RegisterRequest,
    RiskSettingsOut,
    RiskSettingsUpdate,
    SubscriptionOut,
    UserOut,
)

router = APIRouter(prefix="/users", tags=["users"])


def _to_user_out(user) -> UserOut:
    return UserOut(
        telegram_id=user.telegram_id,
        username=user.username,
        role=user.role,
        status=user.status,
        created_at=user.created_at,
        subscription=SubscriptionOut.model_validate(user.subscription)
        if user.subscription else None,
        risk_settings=RiskSettingsOut.model_validate(user.risk_settings)
        if user.risk_settings else None,
        is_active=repo.subscription_is_active(user.subscription),
    )


async def _require_user(session: AsyncSession, telegram_id: int):
    user = await repo.get_user(session, telegram_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return user


@router.post("/register", response_model=UserOut)
async def register(body: RegisterRequest, session: AsyncSession = Depends(get_session)):
    user = await repo.register_user(session, body.telegram_id, body.username)
    return _to_user_out(user)


@router.get("/{telegram_id}", response_model=UserOut)
async def get_user(telegram_id: int, session: AsyncSession = Depends(get_session)):
    return _to_user_out(await _require_user(session, telegram_id))


@router.get("/{telegram_id}/subscription", response_model=SubscriptionOut)
async def get_subscription(telegram_id: int, session: AsyncSession = Depends(get_session)):
    user = await _require_user(session, telegram_id)
    return SubscriptionOut.model_validate(user.subscription)


@router.put("/{telegram_id}/risk", response_model=RiskSettingsOut)
async def update_risk(
    telegram_id: int, body: RiskSettingsUpdate,
    session: AsyncSession = Depends(get_session),
):
    user = await _require_user(session, telegram_id)
    rs = await repo.update_risk_settings(session, user, body.model_dump(exclude_none=True))
    return RiskSettingsOut.model_validate(rs)


@router.post("/{telegram_id}/accounts", response_model=AccountOut)
async def add_account(
    telegram_id: int, body: AddAccountRequest,
    session: AsyncSession = Depends(get_session),
):
    user = await _require_user(session, telegram_id)
    account = await repo.add_account(
        session, user, login=body.login, server=body.server,
        password=body.password, broker=body.broker, account_type=body.account_type,
    )
    return AccountOut.model_validate(account)


@router.post("/{telegram_id}/payments", response_model=PaymentOut)
async def submit_payment(
    telegram_id: int, body: PaymentSubmitRequest,
    session: AsyncSession = Depends(get_session),
):
    user = await _require_user(session, telegram_id)
    payment = await repo.submit_payment(
        session, user, tx_hash=body.tx_hash, amount=body.amount,
        currency=body.currency, note=body.note,
    )
    return PaymentOut.model_validate(payment)
