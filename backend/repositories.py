"""Data-access layer (repositories) for the multi-user model.

Plain async functions that take an AsyncSession. Keeping DB logic here (not in
routes) makes it reusable by the Telegram bot and the terminal-farm workers,
and easy to unit-test with an in-memory SQLite session.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.config import settings
from backend.db.models import (
    AccountSnapshot,
    Lock,
    MT5Account,
    Payment,
    RiskEvent,
    RiskSettings,
    Subscription,
    User,
)
from backend.security import encrypt


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _today_str() -> str:
    return _utcnow().strftime("%Y-%m-%d")


def subscription_is_active(sub: Subscription | None) -> bool:
    if sub is None or sub.expires_at is None:
        return False
    expires = sub.expires_at
    if expires.tzinfo is None:  # SQLite returns naive datetimes
        expires = expires.replace(tzinfo=timezone.utc)
    return expires > _utcnow()


async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(
        select(User)
        .where(User.telegram_id == telegram_id)
        .options(
            selectinload(User.subscription),
            selectinload(User.risk_settings),
            selectinload(User.lock),
            selectinload(User.accounts),
        )
    )
    return result.scalar_one_or_none()


async def register_user(
    session: AsyncSession, telegram_id: int, username: str | None
) -> User:
    """Create a user with default risk settings, an unlocked lock, and a
    free trial subscription. Idempotent: returns the existing user if present."""
    existing = await get_user(session, telegram_id)
    if existing:
        return existing

    user = User(telegram_id=telegram_id, username=username)
    user.risk_settings = RiskSettings(
        max_trades_per_day=settings.max_trades_per_day,
        max_daily_loss_pct=settings.max_daily_loss_pct,
        max_risk_per_trade_pct=settings.max_risk_per_trade_pct,
        max_consecutive_losses=settings.max_consecutive_losses,
        max_account_exposure_pct=settings.max_account_exposure_pct,
    )
    user.lock = Lock(locked=False)
    now = _utcnow()
    if settings.trial_days and settings.trial_days > 0:
        user.subscription = Subscription(
            plan="trial", status="trial", started_at=now,
            expires_at=now + timedelta(days=settings.trial_days),
        )
    else:
        # No free trial: start inactive (pending) until paid or admin-activated.
        user.subscription = Subscription(
            plan="none", status="inactive", started_at=now, expires_at=None,
        )
    session.add(user)
    await session.commit()
    return await get_user(session, telegram_id)  # reload with relationships


async def accept_tos(session: AsyncSession, user: User) -> None:
    if user.tos_accepted_at is None:
        user.tos_accepted_at = _utcnow()
        await session.commit()


async def update_risk_settings(
    session: AsyncSession, user: User, changes: dict
) -> RiskSettings:
    rs = user.risk_settings
    for field, value in changes.items():
        if value is not None and hasattr(rs, field):
            setattr(rs, field, value)
    await session.commit()
    await session.refresh(rs)
    return rs


async def add_account(
    session: AsyncSession,
    user: User,
    *,
    login: int,
    server: str,
    password: str,
    broker: str | None,
    account_type: str,
) -> MT5Account:
    account = MT5Account(
        user_id=user.id,
        login=login,
        server=server,
        broker=broker,
        password_encrypted=encrypt(password),  # never stored in plaintext
        account_type=account_type,
        status="pending",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def update_account(
    session: AsyncSession,
    account: MT5Account,
    *,
    login: int,
    server: str,
    password: str,
    broker: str | None,
    account_type: str,
) -> MT5Account:
    account.login = login
    account.server = server
    account.password_encrypted = encrypt(password)
    account.broker = broker
    account.account_type = account_type
    account.status = "pending"  # re-validate on next worker connect
    await session.commit()
    await session.refresh(account)
    return account


async def activate_subscription(
    session: AsyncSession, user: User, days: int, *, plan: str = "active",
    activated_by: int | None = None,
) -> Subscription:
    """Extend the subscription by `days` from the later of now / current expiry."""
    sub = user.subscription
    now = _utcnow()
    base = sub.expires_at if sub.expires_at and subscription_is_active(sub) else now
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    sub.expires_at = base + timedelta(days=days)
    sub.status = "active"
    sub.plan = plan if plan in ("monthly", "quarterly", "active") else "active"
    sub.activated_by = activated_by
    sub.notice_state = ""  # reset so reminders/expiry notices fire again next cycle
    await session.commit()
    await session.refresh(sub)
    return sub


async def submit_payment(
    session: AsyncSession, user: User, *, tx_hash: str, amount: float | None,
    currency: str | None, note: str | None,
) -> Payment:
    payment = Payment(
        user_id=user.id, method="crypto", amount=amount, currency=currency,
        tx_hash=tx_hash, status="pending", note=note,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    return payment


async def list_pending_payments(session: AsyncSession) -> list[Payment]:
    result = await session.execute(
        select(Payment).where(Payment.status == "pending").order_by(Payment.created_at)
    )
    return list(result.scalars().all())


async def create_provider_payment(
    session: AsyncSession, user: User, *, provider: str, invoice_id: str,
    amount: float, currency: str, plan: str, days: int,
) -> Payment:
    payment = Payment(
        user_id=user.id, method="crypto", provider=provider, invoice_id=invoice_id,
        amount=amount, currency=currency, plan=plan, days=days, status="pending",
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    return payment


async def list_pending_provider_payments(
    session: AsyncSession, provider: str
) -> list[Payment]:
    result = await session.execute(
        select(Payment)
        .where(Payment.status == "pending", Payment.provider == provider)
        .options(selectinload(Payment.user).selectinload(User.subscription))
        .order_by(Payment.created_at)
    )
    return list(result.scalars().all())


async def get_payment(session: AsyncSession, payment_id: int) -> Payment | None:
    return await session.get(Payment, payment_id)


async def set_payment_status(
    session: AsyncSession, payment: Payment, status: str
) -> Payment:
    payment.status = status
    await session.commit()
    await session.refresh(payment)
    return payment


async def list_users(session: AsyncSession) -> list[User]:
    result = await session.execute(
        select(User).options(selectinload(User.subscription)).order_by(User.created_at)
    )
    return list(result.scalars().all())


async def list_subscriptions_with_user(session: AsyncSession) -> list[Subscription]:
    result = await session.execute(
        select(Subscription).options(selectinload(Subscription.user))
    )
    return list(result.scalars().all())


async def set_notice_state(session: AsyncSession, sub: Subscription, state: str) -> None:
    sub.notice_state = state
    await session.commit()


async def get_account_by_id(session: AsyncSession, account_id: int) -> MT5Account | None:
    return await session.get(MT5Account, account_id)


async def set_account_status(
    session: AsyncSession, account_id: int, status: str
) -> MT5Account | None:
    account = await session.get(MT5Account, account_id)
    if account is None:
        return None
    if account.status != status:
        account.status = status
        await session.commit()
        await session.refresh(account)
    return account


async def list_all_accounts(session: AsyncSession) -> list[MT5Account]:
    result = await session.execute(
        select(MT5Account).options(selectinload(MT5Account.user)).order_by(MT5Account.id)
    )
    return list(result.scalars().all())


async def list_active_accounts(session: AsyncSession) -> list[MT5Account]:
    """Accounts belonging to users with a currently-active subscription.
    The supervisor uses this to decide which workers to run."""
    result = await session.execute(
        select(MT5Account).options(
            selectinload(MT5Account.user).selectinload(User.subscription),
            selectinload(MT5Account.user).selectinload(User.risk_settings),
        ).order_by(MT5Account.id)
    )
    accounts = list(result.scalars().all())
    return [a for a in accounts if subscription_is_active(a.user.subscription)]


# --- DB-backed lock (per user) ---------------------------------------------

async def get_lock(session: AsyncSession, user_id: int) -> Lock:
    """Return the user's Lock row (creating one if missing). A daily lock from a
    previous UTC day is auto-cleared."""
    result = await session.execute(select(Lock).where(Lock.user_id == user_id))
    lock = result.scalar_one_or_none()
    if lock is None:
        lock = Lock(user_id=user_id, locked=False)
        session.add(lock)
        await session.commit()
        await session.refresh(lock)
    # Auto-expire a daily lock when the UTC day changes.
    if lock.locked and lock.day is not None and lock.day != _today_str():
        lock.locked = False
        lock.reason = None
        lock.locked_at = None
        lock.day = None
        await session.commit()
        await session.refresh(lock)
    return lock


async def set_lock(
    session: AsyncSession, user_id: int, reason: str, *, daily: bool = True
) -> Lock:
    lock = await get_lock(session, user_id)
    lock.locked = True
    lock.reason = reason
    lock.locked_at = _utcnow()
    lock.day = _today_str() if daily else None
    await session.commit()
    await session.refresh(lock)
    return lock


async def clear_lock(session: AsyncSession, user_id: int) -> Lock:
    lock = await get_lock(session, user_id)
    lock.locked = False
    lock.reason = None
    lock.locked_at = None
    lock.day = None
    await session.commit()
    await session.refresh(lock)
    return lock


async def add_risk_event(
    session: AsyncSession, user_id: int, type_: str, message: str
) -> RiskEvent:
    event = RiskEvent(user_id=user_id, type=type_, message=message)
    session.add(event)
    await session.commit()
    return event


# --- Live snapshot (written by the worker, read by the bot's /status) -------

async def upsert_snapshot(session: AsyncSession, user_id: int, status) -> AccountSnapshot:
    """Store the latest live data for a user (one row, upserted)."""
    result = await session.execute(
        select(AccountSnapshot).where(AccountSnapshot.user_id == user_id)
    )
    snap = result.scalar_one_or_none()
    if snap is None:
        snap = AccountSnapshot(user_id=user_id)
        session.add(snap)
    a, r = status.account, status.risk
    snap.currency = a.currency
    snap.balance = a.balance
    snap.equity = a.equity
    snap.margin = a.margin
    snap.floating_pnl = a.profit
    snap.open_positions = len(status.open_positions)
    snap.trades_today = r.trades_today
    snap.daily_loss = r.daily_loss
    snap.daily_loss_pct = r.daily_loss_pct
    snap.consecutive_losses = r.consecutive_losses
    snap.exposure_pct = r.exposure_pct
    snap.any_limit_hit = r.any_limit_hit
    snap.updated_at = _utcnow()
    await session.commit()
    await session.refresh(snap)
    return snap


async def get_snapshot(session: AsyncSession, user_id: int) -> AccountSnapshot | None:
    result = await session.execute(
        select(AccountSnapshot).where(AccountSnapshot.user_id == user_id)
    )
    return result.scalar_one_or_none()
