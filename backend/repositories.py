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
    AppSetting,
    EmotionScore,
    Lock,
    MT5Account,
    Payment,
    RiskEvent,
    RiskSettings,
    Subscription,
    Trade,
    TradeJournal,
    User,
)
from backend.security import decrypt, encrypt


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

async def upsert_snapshot(
    session: AsyncSession, user_id: int, status,
    yesterday_json: str | None = None,
) -> AccountSnapshot:
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
    if yesterday_json is not None:
        snap.yesterday_json = yesterday_json
    await session.commit()
    await session.refresh(snap)
    return snap


async def get_snapshot(session: AsyncSession, user_id: int) -> AccountSnapshot | None:
    result = await session.execute(
        select(AccountSnapshot).where(AccountSnapshot.user_id == user_id)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# V3 — Trade Journal repositories
# ---------------------------------------------------------------------------

async def get_trade_by_ticket(
    session: AsyncSession, user_id: int, ticket: int
) -> Trade | None:
    result = await session.execute(
        select(Trade).where(Trade.user_id == user_id, Trade.ticket == ticket)
    )
    return result.scalar_one_or_none()


async def open_trade(
    session: AsyncSession, user_id: int, *,
    ticket: int, symbol: str, direction: str, volume: float,
    entry_price: float, sl: float | None, tp: float | None, opened_at: datetime,
) -> Trade:
    trade = Trade(
        user_id=user_id, ticket=ticket, symbol=symbol,
        direction=direction, volume=volume, entry_price=entry_price,
        sl=sl, tp=tp, opened_at=opened_at, status="open",
        entry_prompted_at=_utcnow(),
    )
    session.add(trade)
    await session.commit()
    await session.refresh(trade)
    return trade


async def close_trade(
    session: AsyncSession, trade: Trade, *,
    exit_price: float, profit: float, closed_at: datetime,
) -> Trade:
    trade.exit_price = exit_price
    trade.profit = profit
    trade.closed_at = closed_at
    trade.status = "closed"
    trade.exit_prompted_at = _utcnow()
    if trade.opened_at:
        opened = trade.opened_at
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        trade.duration_s = int((closed_at - opened).total_seconds())
    await session.commit()
    await session.refresh(trade)
    return trade


async def get_open_trades(session: AsyncSession, user_id: int) -> list[Trade]:
    result = await session.execute(
        select(Trade).where(Trade.user_id == user_id, Trade.status == "open")
        .order_by(Trade.opened_at.desc())
    )
    return list(result.scalars().all())


async def get_unjournaled_trades(session: AsyncSession, user_id: int) -> list[Trade]:
    """Trades that need an entry or exit journal (entry_journal_id or exit_journal_id is null
    and the trade is past its prompt time)."""
    result = await session.execute(
        select(Trade).where(
            Trade.user_id == user_id,
            Trade.status.in_(["open", "closed"]),
        ).order_by(Trade.opened_at.desc())
    )
    trades = list(result.scalars().all())
    unjournaled = []
    for t in trades:
        needs_entry = t.entry_journal_id is None and t.entry_prompted_at is not None
        needs_exit = (
            t.status == "closed"
            and t.exit_journal_id is None
            and t.exit_prompted_at is not None
        )
        if needs_entry or needs_exit:
            unjournaled.append(t)
    return unjournaled


async def bump_entry_reminder(session: AsyncSession, trade: Trade) -> Trade:
    trade.entry_reminder_count += 1
    trade.entry_prompted_at = _utcnow()
    await session.commit()
    await session.refresh(trade)
    return trade


async def bump_exit_reminder(session: AsyncSession, trade: Trade) -> Trade:
    trade.exit_reminder_count += 1
    trade.exit_prompted_at = _utcnow()
    await session.commit()
    await session.refresh(trade)
    return trade


async def skip_entry_journal(session: AsyncSession, trade: Trade) -> TradeJournal:
    journal = TradeJournal(
        trade_id=trade.id, user_id=trade.user_id, type="entry", skipped=True,
    )
    session.add(journal)
    await session.flush()
    trade.entry_journal_id = journal.id
    if trade.status == "open":
        trade.status = "entry_skipped"
    await session.commit()
    await session.refresh(journal)
    return journal


async def skip_exit_journal(session: AsyncSession, trade: Trade) -> TradeJournal:
    journal = TradeJournal(
        trade_id=trade.id, user_id=trade.user_id, type="exit", skipped=True,
    )
    session.add(journal)
    await session.flush()
    trade.exit_journal_id = journal.id
    trade.status = "exit_skipped"
    await session.commit()
    await session.refresh(journal)
    return journal


async def save_entry_journal(
    session: AsyncSession, trade: Trade, *,
    setup_reason: str, emotion: str, plan_followed: str, confidence: int,
) -> TradeJournal:
    journal = TradeJournal(
        trade_id=trade.id, user_id=trade.user_id, type="entry",
        setup_reason=setup_reason, emotion_entry=emotion,
        plan_followed=plan_followed, confidence=confidence,
    )
    session.add(journal)
    await session.flush()
    trade.entry_journal_id = journal.id
    await session.commit()
    await session.refresh(journal)
    return journal


async def save_exit_journal(
    session: AsyncSession, trade: Trade, *,
    exit_reason: str, plan_followed: str, mistakes: str,
    emotion: str, rating: int,
) -> TradeJournal:
    journal = TradeJournal(
        trade_id=trade.id, user_id=trade.user_id, type="exit",
        exit_reason=exit_reason, plan_followed_exit=plan_followed,
        mistakes=mistakes, emotion_exit=emotion, rating=rating,
    )
    session.add(journal)
    await session.flush()
    trade.exit_journal_id = journal.id
    if trade.status in ("closed", "entry_skipped"):
        trade.status = "fully_journaled"
    await session.commit()
    await session.refresh(journal)
    return journal


async def get_recent_trades(
    session: AsyncSession, user_id: int, limit: int = 10
) -> list[Trade]:
    result = await session.execute(
        select(Trade).where(Trade.user_id == user_id)
        .order_by(Trade.opened_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def get_trade_by_id(session: AsyncSession, trade_id: int) -> Trade | None:
    result = await session.execute(select(Trade).where(Trade.id == trade_id))
    return result.scalar_one_or_none()


async def set_trade_screenshot(
    session: AsyncSession, trade: Trade, file_id: str
) -> Trade:
    trade.screenshot_file_id = file_id
    await session.commit()
    await session.refresh(trade)
    return trade


async def get_latest_trade_without_screenshot(
    session: AsyncSession, user_id: int
) -> Trade | None:
    """Most recent trade (any status) with no screenshot — the default target
    when a trader sends a photo without specifying a trade id."""
    result = await session.execute(
        select(Trade).where(
            Trade.user_id == user_id,
            Trade.screenshot_file_id.is_(None),
        ).order_by(Trade.opened_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def get_journal_for_trade(
    session: AsyncSession, trade_id: int, type: str
) -> TradeJournal | None:
    result = await session.execute(
        select(TradeJournal).where(
            TradeJournal.trade_id == trade_id,
            TradeJournal.type == type,
        )
    )
    return result.scalar_one_or_none()


async def get_today_trades_with_journals(
    session: AsyncSession, user_id: int, date_str: str
) -> tuple[list[Trade], list[TradeJournal]]:
    """Return today's trades and all their journals for the psychology engine."""
    result = await session.execute(
        select(Trade).where(
            Trade.user_id == user_id,
            Trade.opened_at >= _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            - __import__("datetime").timedelta(days=1),
        ).order_by(Trade.opened_at)
    )
    trades = list(result.scalars().all())
    trade_ids = [t.id for t in trades]
    journals: list[TradeJournal] = []
    if trade_ids:
        jresult = await session.execute(
            select(TradeJournal).where(TradeJournal.trade_id.in_(trade_ids))
        )
        journals = list(jresult.scalars().all())
    return trades, journals


async def get_trades_in_range(
    session: AsyncSession, user_id: int,
    since: "datetime", until: "datetime",
) -> list[Trade]:
    result = await session.execute(
        select(Trade).where(
            Trade.user_id == user_id,
            Trade.opened_at >= since,
            Trade.opened_at <= until,
        ).order_by(Trade.opened_at.desc())
    )
    return list(result.scalars().all())


async def get_journals_in_range(
    session: AsyncSession, user_id: int,
    since: "datetime", until: "datetime",
) -> list[TradeJournal]:
    result = await session.execute(
        select(TradeJournal).where(
            TradeJournal.user_id == user_id,
            TradeJournal.created_at >= since,
            TradeJournal.created_at <= until,
        ).order_by(TradeJournal.created_at)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# V4 — Emotion score repositories
# ---------------------------------------------------------------------------

async def upsert_emotion_score(
    session: AsyncSession, user_id: int, date_str: str,
    score: int, events_json: str, locked_by_score: bool,
) -> EmotionScore:
    result = await session.execute(
        select(EmotionScore).where(
            EmotionScore.user_id == user_id,
            EmotionScore.date == date_str,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = EmotionScore(user_id=user_id, date=date_str)
        session.add(row)
    row.score = score
    row.events_json = events_json
    row.locked_by_score = locked_by_score
    row.updated_at = _utcnow()
    await session.commit()
    await session.refresh(row)
    return row


async def get_emotion_score(
    session: AsyncSession, user_id: int, date_str: str
) -> EmotionScore | None:
    result = await session.execute(
        select(EmotionScore).where(
            EmotionScore.user_id == user_id,
            EmotionScore.date == date_str,
        )
    )
    return result.scalar_one_or_none()


async def get_emotion_scores_in_range(
    session: AsyncSession, user_id: int, since_date: str, until_date: str
) -> list[EmotionScore]:
    result = await session.execute(
        select(EmotionScore).where(
            EmotionScore.user_id == user_id,
            EmotionScore.date >= since_date,
            EmotionScore.date <= until_date,
        ).order_by(EmotionScore.date.desc())
    )
    return list(result.scalars().all())


async def save_lock_explanation(
    session: AsyncSession, user_id: int, explanation: str
) -> None:
    result = await session.execute(
        select(Lock).where(Lock.user_id == user_id)
    )
    lock = result.scalar_one_or_none()
    if lock:
        lock.explanation = explanation
        await session.commit()


# ---------------------------------------------------------------------------
# App settings (admin-editable, runtime). Used for the AI coach config.
# ---------------------------------------------------------------------------

# Keys that hold secrets and must be encrypted at rest.
_SECRET_SETTING_KEYS = {"openai_api_key", "anthropic_api_key"}


async def get_app_setting(session: AsyncSession, key: str) -> str | None:
    result = await session.execute(select(AppSetting).where(AppSetting.key == key))
    row = result.scalar_one_or_none()
    if row is None or row.value is None:
        return None
    if key in _SECRET_SETTING_KEYS:
        try:
            return decrypt(row.value)
        except Exception:  # noqa: BLE001 - corrupt/again-encrypted value
            return None
    return row.value


async def set_app_setting(session: AsyncSession, key: str, value: str | None) -> None:
    result = await session.execute(select(AppSetting).where(AppSetting.key == key))
    row = result.scalar_one_or_none()
    stored = value
    if value is not None and key in _SECRET_SETTING_KEYS:
        stored = encrypt(value)
    if row is None:
        row = AppSetting(key=key, value=stored)
        session.add(row)
    else:
        row.value = stored
        row.updated_at = _utcnow()
    await session.commit()


async def get_ai_config(session: AsyncSession) -> dict:
    """Effective AI coach config: DB overrides on top of env defaults.

    Secret keys are returned in plaintext (decrypted) for use by the caller;
    never expose this dict directly over the API — use mask_ai_config for that.
    """
    async def _val(key: str, default):
        v = await get_app_setting(session, key)
        return v if v not in (None, "") else default

    enabled_raw = await _val("ai_coach_enabled", str(settings.ai_coach_enabled).lower())
    provider = await _val("ai_provider", settings.ai_provider)
    openai_key = await _val("openai_api_key", settings.openai_api_key)
    anthropic_key = await _val("anthropic_api_key", settings.anthropic_api_key)
    openai_model = await _val("openai_model", settings.openai_model)
    anthropic_model = await _val("anthropic_model", settings.anthropic_model)

    provider = (provider or "openai").strip().lower()
    enabled = str(enabled_raw).strip().lower() in ("1", "true", "yes", "on")
    if provider == "claude":
        active_key, active_model = anthropic_key, anthropic_model
    else:
        active_key, active_model = openai_key, openai_model

    return {
        "enabled": enabled,
        "provider": provider,
        "openai_api_key": openai_key,
        "anthropic_api_key": anthropic_key,
        "openai_model": openai_model,
        "anthropic_model": anthropic_model,
        "active_key": active_key,
        "active_model": active_model,
        "available": bool(enabled and active_key),
    }


def mask_ai_config(cfg: dict) -> dict:
    """Safe-for-API view: never returns raw API keys, only whether each is set."""
    def _mask(v: str | None) -> str:
        if not v:
            return ""
        return f"…{v[-4:]}" if len(v) >= 4 else "set"
    return {
        "enabled": cfg["enabled"],
        "provider": cfg["provider"],
        "openai_model": cfg["openai_model"],
        "anthropic_model": cfg["anthropic_model"],
        "openai_key_set": bool(cfg["openai_api_key"]),
        "anthropic_key_set": bool(cfg["anthropic_api_key"]),
        "openai_key_hint": _mask(cfg["openai_api_key"]),
        "anthropic_key_hint": _mask(cfg["anthropic_api_key"]),
        "available": cfg["available"],
    }
