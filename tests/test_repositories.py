"""Tests for the multi-user repository layer (Phase A).

Uses an isolated in-memory SQLite database — no dependency on the app's
configured DB. Run with:  python -m tests.test_repositories
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend import repositories as repo
from backend.db.base import Base
from backend.db import models  # noqa: F401  (register tables)
from backend.security import decrypt


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def test_register_creates_defaults_inactive():
    Session = await _make_session()
    async with Session() as s:
        user = await repo.register_user(s, telegram_id=111, username="omid")
        assert user.id is not None
        assert user.risk_settings.max_trades_per_day == 2
        assert user.lock.locked is False
        # No free trial: users start inactive (pending) until paid/activated.
        assert user.subscription.status == "inactive"
        assert repo.subscription_is_active(user.subscription) is False


async def test_register_with_trial_when_configured():
    from backend.config import settings
    settings.trial_days = 7
    try:
        Session = await _make_session()
        async with Session() as s:
            user = await repo.register_user(s, telegram_id=112, username="t")
            assert user.subscription.status == "trial"
            assert repo.subscription_is_active(user.subscription) is True
    finally:
        settings.trial_days = 0


async def test_register_is_idempotent():
    Session = await _make_session()
    async with Session() as s:
        u1 = await repo.register_user(s, 222, "a")
        u2 = await repo.register_user(s, 222, "a")
        assert u1.id == u2.id


async def test_update_risk_settings():
    Session = await _make_session()
    async with Session() as s:
        user = await repo.register_user(s, 333, None)
        rs = await repo.update_risk_settings(s, user, {"max_trades_per_day": 5, "max_daily_loss_pct": 3.0})
        assert rs.max_trades_per_day == 5
        assert rs.max_daily_loss_pct == 3.0


async def test_account_password_is_encrypted():
    Session = await _make_session()
    async with Session() as s:
        user = await repo.register_user(s, 444, None)
        acct = await repo.add_account(
            s, user, login=99999, server="OctaFX-Real",
            password="s3cret", broker="Octa", account_type="trading",
        )
        assert acct.password_encrypted != "s3cret"          # stored encrypted
        assert decrypt(acct.password_encrypted) == "s3cret"  # decrypts back


async def test_activate_extends_subscription():
    Session = await _make_session()
    async with Session() as s:
        user = await repo.register_user(s, 555, None)
        sub = await repo.activate_subscription(s, user, days=30, plan="monthly")
        assert sub.status == "active"
        assert sub.plan == "monthly"
        assert repo.subscription_is_active(sub)
        # expiry should be ~30 days out (trial was 7; active extends from now)
        remaining = sub.expires_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)
        assert timedelta(days=29) < remaining < timedelta(days=38)


async def test_payment_flow():
    Session = await _make_session()
    async with Session() as s:
        user = await repo.register_user(s, 666, None)
        p = await repo.submit_payment(s, user, tx_hash="0xabc", amount=20, currency="USDT", note=None)
        assert p.status == "pending"
        pending = await repo.list_pending_payments(s)
        assert len(pending) == 1
        verified = await repo.set_payment_status(s, p, "verified")
        assert verified.status == "verified"
        assert await repo.list_pending_payments(s) == []


async def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        await fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} tests passed.")


if __name__ == "__main__":
    asyncio.run(_run())
