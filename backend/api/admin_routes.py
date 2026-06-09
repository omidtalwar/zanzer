"""Admin routes — payment verification & subscription activation.

Guarded by a shared secret (X-Admin-Token header == settings.admin_token).
If ADMIN_TOKEN is unset, all admin routes are disabled (403) for safety.
"""
from __future__ import annotations

import asyncio
from html import escape

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend import repositories as repo
from backend.bot import client as bot_client
from backend.config import settings
from backend.db.session import get_session
from backend.schemas import BroadcastRequest, PaymentOut, SubscriptionOut, UserOut
from backend.api.user_routes import _to_user_out

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=403, detail="admin disabled (ADMIN_TOKEN not set)")
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=403, detail="invalid admin token")


@router.get("/payments/pending", response_model=list[PaymentOut], dependencies=[Depends(require_admin)])
async def pending_payments(session: AsyncSession = Depends(get_session)):
    return [PaymentOut.model_validate(p) for p in await repo.list_pending_payments(session)]


@router.post("/payments/{payment_id}/verify", response_model=PaymentOut, dependencies=[Depends(require_admin)])
async def verify_payment(payment_id: int, session: AsyncSession = Depends(get_session)):
    payment = await repo.get_payment(session, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="payment not found")
    return PaymentOut.model_validate(await repo.set_payment_status(session, payment, "verified"))


@router.post("/payments/{payment_id}/reject", response_model=PaymentOut, dependencies=[Depends(require_admin)])
async def reject_payment(payment_id: int, session: AsyncSession = Depends(get_session)):
    payment = await repo.get_payment(session, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="payment not found")
    return PaymentOut.model_validate(await repo.set_payment_status(session, payment, "rejected"))


@router.post("/users/{telegram_id}/activate", response_model=SubscriptionOut, dependencies=[Depends(require_admin)])
async def activate(telegram_id: int, days: int = 30, plan: str = "monthly",
                   session: AsyncSession = Depends(get_session)):
    user = await repo.get_user(session, telegram_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    sub = await repo.activate_subscription(session, user, days, plan=plan)
    return SubscriptionOut.model_validate(sub)


@router.get("/accounts", dependencies=[Depends(require_admin)])
async def list_accounts(session: AsyncSession = Depends(get_session)):
    accounts = await repo.list_all_accounts(session)
    return [
        {
            "id": a.id, "user_telegram_id": a.user.telegram_id,
            "login": a.login, "server": a.server, "status": a.status,
            "account_type": a.account_type,
        }
        for a in accounts
    ]


@router.post("/broadcast", dependencies=[Depends(require_admin)])
async def broadcast(body: BroadcastRequest, session: AsyncSession = Depends(get_session)):
    msg = body.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="message is required")
    users = await repo.list_users(session)
    aud = (body.audience or "all").lower()
    if aud == "active":
        users = [u for u in users if repo.subscription_is_active(u.subscription)]
    elif aud == "inactive":
        users = [u for u in users if not repo.subscription_is_active(u.subscription)]
    text = f"📢 <b>Announcement</b>\n\n{escape(msg)}"
    sent = failed = 0
    for u in users:
        try:
            ok = await bot_client.send_message(u.telegram_id, text)
            sent += 1 if ok else 0
            failed += 0 if ok else 1
        except Exception:  # noqa: BLE001
            failed += 1
        await asyncio.sleep(0.05)  # stay under Telegram's ~30 msg/s limit
    return {"audience": aud, "total": len(users), "sent": sent, "failed": failed}


@router.get("/summary", dependencies=[Depends(require_admin)])
async def summary(session: AsyncSession = Depends(get_session)):
    users = await repo.list_users(session)
    pending = await repo.list_pending_payments(session)
    accounts = await repo.list_all_accounts(session)
    active = sum(1 for u in users if repo.subscription_is_active(u.subscription))
    return {
        "users": len(users),
        "active_subscriptions": active,
        "pending_payments": len(pending),
        "accounts": len(accounts),
    }


@router.get("/users", response_model=list[UserOut], dependencies=[Depends(require_admin)])
async def list_users(session: AsyncSession = Depends(get_session)):
    users = await repo.list_users(session)
    # subscription is eager-loaded; risk_settings not needed for the list view
    out = []
    for u in users:
        out.append(UserOut(
            telegram_id=u.telegram_id, username=u.username, role=u.role,
            status=u.status, created_at=u.created_at,
            subscription=SubscriptionOut.model_validate(u.subscription) if u.subscription else None,
            is_active=repo.subscription_is_active(u.subscription),
        ))
    return out
