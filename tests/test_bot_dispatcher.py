"""Tests for the Telegram BotDispatcher using a fake send + in-memory DB.

Run with:  python -m tests.test_bot_dispatcher
"""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend import repositories as repo
from backend.bot.dispatcher import BotDispatcher
from backend.config import settings
from backend.db.base import Base
from backend.db import models  # noqa: F401
from backend.security import decrypt

OWNER = 5625070857


async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def _make_dispatcher(Session):
    sent: list[tuple[int, str]] = []
    deleted: list[tuple[int, int]] = []

    async def send(chat_id, text):
        sent.append((chat_id, text))
        return True

    async def delete(chat_id, message_id):
        deleted.append((chat_id, message_id))
        return True

    d = BotDispatcher(send=send, delete=delete, session_factory=Session)
    return d, sent, deleted


async def test_start_registers_user_and_asks_agree():
    Session = await _session_factory()
    d, sent, _ = _make_dispatcher(Session)
    await d.handle(telegram_id=1, username="bob", text="/start")
    assert any("/agree" in t for _, t in sent)  # must accept ToS first
    async with Session() as s:
        assert await repo.get_user(s, 1) is not None


async def test_link_requires_tos_then_agree_unlocks():
    Session = await _session_factory()
    d, sent, _ = _make_dispatcher(Session)
    await d.handle(telegram_id=2, username=None, text="/start")
    await d.handle(telegram_id=2, username=None, text="/link")
    assert any("accept the terms first" in t for _, t in sent)
    assert 2 not in d.states  # link flow not started
    await d.handle(telegram_id=2, username=None, text="/agree")
    async with Session() as s:
        assert (await repo.get_user(s, 2)).tos_accepted_at is not None
    await d.handle(telegram_id=2, username=None, text="/link")
    assert d.states[2]["step"] == "login"  # now the flow starts


async def test_help_and_unknown():
    Session = await _session_factory()
    d, sent, _ = _make_dispatcher(Session)
    await d.handle(telegram_id=1, username=None, text="/menu")
    await d.handle(telegram_id=1, username=None, text="/wat")
    assert any("Commands" in t for _, t in sent)
    assert any("Unknown command" in t for _, t in sent)


async def test_link_flow_saves_encrypted_account_and_deletes_password():
    Session = await _session_factory()
    d, sent, deleted = _make_dispatcher(Session)
    tid = 42
    await d.handle(telegram_id=tid, username="u", text="/agree")
    await d.handle(telegram_id=tid, username="u", text="/link")
    await d.handle(telegram_id=tid, username="u", text="123456")          # login
    await d.handle(telegram_id=tid, username="u", text="OctaFX-Real")      # server
    await d.handle(telegram_id=tid, username="u", text="myp@ss", message_id=999)  # password → finalizes

    assert (tid, 999) in deleted          # password message deleted
    async with Session() as s:
        user = await repo.get_user(s, tid)
        assert len(user.accounts) == 1
        acct = user.accounts[0]
        assert acct.login == 123456
        assert acct.server == "OctaFX-Real"
        assert acct.account_type == "trading"
        assert acct.password_encrypted != "myp@ss"
        assert decrypt(acct.password_encrypted) == "myp@ss"
    assert tid not in d.states            # flow cleared


async def test_link_with_validator_success():
    Session = await _session_factory()
    sent: list[tuple[int, str]] = []

    async def send(chat_id, text):
        sent.append((chat_id, text)); return True

    async def ok_validator(account_id):
        return (True, "123456 @ OctaFX-Real")

    d = BotDispatcher(send=send, delete=None, validator=ok_validator, session_factory=Session)
    tid = 71
    await d.handle(telegram_id=tid, username="u", text="/agree")
    await d.handle(telegram_id=tid, username="u", text="/link")
    await d.handle(telegram_id=tid, username="u", text="123456")
    await d.handle(telegram_id=tid, username="u", text="OctaFX-Real")
    await d.handle(telegram_id=tid, username="u", text="pw", message_id=5)  # password → finalizes
    assert any("✅ Connected" in t for _, t in sent)


async def test_link_with_validator_failure():
    Session = await _session_factory()
    sent: list[tuple[int, str]] = []

    async def send(chat_id, text):
        sent.append((chat_id, text)); return True

    async def bad_validator(account_id):
        return (False, "Authorization failed")

    d = BotDispatcher(send=send, delete=None, validator=bad_validator, session_factory=Session)
    tid = 72
    await d.handle(telegram_id=tid, username="u", text="/agree")
    await d.handle(telegram_id=tid, username="u", text="/link")
    await d.handle(telegram_id=tid, username="u", text="123456")
    await d.handle(telegram_id=tid, username="u", text="OctaFX-Real")
    await d.handle(telegram_id=tid, username="u", text="pw", message_id=5)  # password → finalizes
    assert any("❌ Couldn't log in" in t for _, t in sent)


async def test_link_rejects_non_numeric_login():
    Session = await _session_factory()
    d, sent, _ = _make_dispatcher(Session)
    await d.handle(telegram_id=7, username=None, text="/agree")
    await d.handle(telegram_id=7, username=None, text="/link")
    await d.handle(telegram_id=7, username=None, text="abc")  # bad login
    assert any("must be a number" in t for _, t in sent)
    assert d.states[7]["step"] == "login"  # still waiting


async def test_cancel_clears_flow():
    Session = await _session_factory()
    d, sent, _ = _make_dispatcher(Session)
    await d.handle(telegram_id=8, username=None, text="/agree")
    await d.handle(telegram_id=8, username=None, text="/link")
    await d.handle(telegram_id=8, username=None, text="/cancel")
    assert 8 not in d.states


async def test_lock_and_user_cannot_self_unlock():
    Session = await _session_factory()
    d, sent, _ = _make_dispatcher(Session)
    await d.handle(telegram_id=9, username=None, text="/start")
    await d.handle(telegram_id=9, username=None, text="/lock")
    async with Session() as s:
        u = await repo.get_user(s, 9)
        assert (await repo.get_lock(s, u.id)).locked is True
    # A regular user CANNOT undo their lock (commitment device).
    await d.handle(telegram_id=9, username=None, text="/unlock")
    assert any("can't be removed on demand" in t for _, t in sent)
    async with Session() as s:
        u = await repo.get_user(s, 9)
        assert (await repo.get_lock(s, u.id)).locked is True  # still locked


async def test_admin_can_unlock_user():
    Session = await _session_factory()
    d, sent, _ = _make_dispatcher(Session)
    settings.bot_admin_ids = str(OWNER)
    try:
        await d.handle(telegram_id=33, username=None, text="/start")
        await d.handle(telegram_id=33, username=None, text="/lock")
        await d.handle(telegram_id=OWNER, username="admin", text="/unlock 33")
        async with Session() as s:
            u = await repo.get_user(s, 33)
            assert (await repo.get_lock(s, u.id)).locked is False
        assert any(cid == 33 for cid, _ in sent)  # user notified
    finally:
        settings.bot_admin_ids = None


async def test_paid_creates_payment_and_pings_admin():
    Session = await _session_factory()
    d, sent, _ = _make_dispatcher(Session)
    settings.bot_admin_ids = str(OWNER)
    try:
        await d.handle(telegram_id=50, username="payer", text="/start")
        await d.handle(telegram_id=50, username="payer", text="/paid 0xABC123")
        async with Session() as s:
            assert len(await repo.list_pending_payments(s)) == 1
        # admin got a ping
        assert any(cid == OWNER and "New payment" in t for cid, t in sent)
    finally:
        settings.bot_admin_ids = None


async def test_subscribe_uses_invoicer():
    Session = await _session_factory()
    sent: list[tuple[int, str]] = []

    async def send(chat_id, text):
        sent.append((chat_id, text)); return True

    async def invoicer(telegram_id, plan):
        return (True, f"https://t.me/CryptoBot?start=INV_{plan}")

    d = BotDispatcher(send=send, invoicer=invoicer, session_factory=Session)
    await d.handle(telegram_id=60, username="u", text="/start")
    await d.handle(telegram_id=60, username="u", text="/subscribe quarterly")
    assert any("INV_quarterly" in t for _, t in sent)


async def test_stars_uses_star_invoicer():
    Session = await _session_factory()
    sent: list[tuple[int, str]] = []
    calls: list[tuple[int, str]] = []

    async def send(chat_id, text):
        sent.append((chat_id, text)); return True

    async def star_invoicer(telegram_id, plan):
        calls.append((telegram_id, plan)); return (True, "⭐ Invoice sent for 1200 Stars")

    d = BotDispatcher(send=send, star_invoicer=star_invoicer, session_factory=Session)
    await d.handle(telegram_id=61, username="u", text="/start")
    await d.handle(telegram_id=61, username="u", text="/stars quarterly")
    assert calls == [(61, "quarterly")]
    assert any("Stars" in t for _, t in sent)


async def test_setrisk_wizard_full_flow():
    Session = await _session_factory()
    d, sent, _ = _make_dispatcher(Session)
    await d.handle(telegram_id=80, username="u", text="/start")
    await d.handle(telegram_id=80, username="u", text="/setrisk")   # starts wizard
    await d.handle(telegram_id=80, username="u", text="1")          # trades
    await d.handle(telegram_id=80, username="u", text="50$")        # daily loss in $
    await d.handle(telegram_id=80, username="u", text="2")          # risk/trade
    await d.handle(telegram_id=80, username="u", text="3")          # losses
    await d.handle(telegram_id=80, username="u", text="skip")       # exposure (keep)
    async with Session() as s:
        u = await repo.get_user(s, 80)
        assert u.risk_settings.max_trades_per_day == 1
        assert u.risk_settings.max_daily_loss_usd == 50
        assert u.risk_settings.max_daily_loss_pct == 0   # $ chosen → % off
        assert u.risk_settings.max_risk_per_trade_pct == 2
        assert u.risk_settings.max_consecutive_losses == 3
    assert 80 not in d.states  # wizard finished
    assert any("rules are saved" in t.lower() for _, t in sent)


async def test_setrisk_wizard_percent_and_invalid_retry():
    Session = await _session_factory()
    d, sent, _ = _make_dispatcher(Session)
    await d.handle(telegram_id=81, username="u", text="/start")
    await d.handle(telegram_id=81, username="u", text="/setrisk")
    await d.handle(telegram_id=81, username="u", text="abc")  # invalid trades → re-ask
    assert any("whole number" in t for _, t in sent)
    assert d.states[81]["step"] == 0  # still on first step
    await d.handle(telegram_id=81, username="u", text="2")    # trades ok
    await d.handle(telegram_id=81, username="u", text="4%")   # daily loss %
    await d.handle(telegram_id=81, username="u", text="skip") # risk/trade
    await d.handle(telegram_id=81, username="u", text="skip") # losses
    await d.handle(telegram_id=81, username="u", text="off")  # exposure off
    async with Session() as s:
        u = await repo.get_user(s, 81)
        assert u.risk_settings.max_daily_loss_pct == 4
        assert u.risk_settings.max_daily_loss_usd == 0
        assert u.risk_settings.max_account_exposure_pct == 0


async def test_admin_broadcast_reaches_all_users():
    Session = await _session_factory()
    d, sent, _ = _make_dispatcher(Session)
    settings.bot_admin_ids = str(OWNER)
    try:
        # three users register
        for tid in (101, 102, 103):
            await d.handle(telegram_id=tid, username=None, text="/start")
        sent.clear()
        await d.handle(telegram_id=OWNER, username="admin", text="/broadcast Server maintenance at 5pm")
        # each user got the announcement
        for tid in (101, 102, 103):
            assert any(cid == tid and "Server maintenance" in t for cid, t in sent)
        assert any("Broadcast done" in t for cid, t in sent if cid == OWNER)
    finally:
        settings.bot_admin_ids = None


async def test_broadcast_requires_admin():
    Session = await _session_factory()
    d, sent, _ = _make_dispatcher(Session)
    await d.handle(telegram_id=999, username=None, text="/broadcast hi")
    assert any("Not authorized" in t for _, t in sent)


async def test_admin_activate_requires_admin():
    Session = await _session_factory()
    d, sent, _ = _make_dispatcher(Session)
    settings.bot_admin_ids = str(OWNER)
    try:
        # non-admin
        await d.handle(telegram_id=123, username=None, text="/activate 123 30")
        assert any("Not authorized" in t for _, t in sent)
        # admin activates a real user
        await d.handle(telegram_id=777, username="cust", text="/start")
        sent.clear()
        await d.handle(telegram_id=OWNER, username="admin", text="/activate 777 30 monthly")
        async with Session() as s:
            u = await repo.get_user(s, 777)
            assert repo.subscription_is_active(u.subscription)
        assert any("Activated 777" in t for _, t in sent)
        assert any(cid == 777 for cid, _ in sent)  # user notified
    finally:
        settings.bot_admin_ids = None


async def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        await fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} tests passed.")


if __name__ == "__main__":
    asyncio.run(_run())
