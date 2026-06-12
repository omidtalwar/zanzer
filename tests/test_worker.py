"""Tests for the per-account worker (Phase B) using the mock broker + in-memory DB.

Run with:  python -m tests.test_worker
"""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend import repositories as repo
from backend.broker.mock import MockBrokerClient, make_account, make_deal
from backend.db.base import Base
from backend.db import models  # noqa: F401
from backend.models import RiskLimits
from backend.worker import AccountWorker

from backend.config import settings as _settings
# These tests exercise enforcement/journal, not the pre-trade gate. Disable the
# gate by default so opening a position doesn't fire the gate prompt; the gate
# has its own dedicated test that re-enables it.
_settings.pretrade_gate_enabled = False

LIMITS = RiskLimits(
    max_trades_per_day=2, max_daily_loss_pct=5.0, max_risk_per_trade_pct=4.0,
    max_consecutive_losses=2, max_account_exposure_pct=5.0,
)


async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _new_user(Session):
    async with Session() as s:
        user = await repo.register_user(s, telegram_id=900, username="t")
        return user.id


async def _captured_notify(store):
    async def notify(text: str) -> bool:
        store.append(text)
        return True
    return notify


async def test_no_breach_no_actions():
    Session = await _session_factory()
    uid = await _new_user(Session)
    broker = MockBrokerClient(make_account(balance=1000, equity=1000))
    sent: list[str] = []
    worker = AccountWorker(broker, user_id=uid, telegram_id=900, limits=LIMITS,
                           notify=await _captured_notify(sent), session_factory=Session)
    result = await worker.run_once()
    assert result.actions == []
    assert sent == []
    async with Session() as s:
        lock = await repo.get_lock(s, uid)
        assert lock.locked is False


async def test_daily_loss_dryrun_locks_warns_but_does_not_close():
    from backend.config import settings
    settings.enforcement_mode = "dry_run"
    Session = await _session_factory()
    uid = await _new_user(Session)
    # realized -60 on a closed position -> -6% of 1000 start balance -> breach 5%
    deals = [make_deal(1, "IN"), make_deal(1, "OUT", profit=-60.0)]
    broker = MockBrokerClient(make_account(balance=940, equity=940), deals=deals)
    sent: list[str] = []
    worker = AccountWorker(broker, user_id=uid, telegram_id=900, limits=LIMITS,
                           notify=await _captured_notify(sent), session_factory=Session)
    result = await worker.run_once()
    types = {a.type for a in result.actions}
    assert "WARN" in types and "LOCK" in types and "CLOSE_ALL" in types
    assert broker.close_all_calls == 0          # dry_run: NOT closed
    assert len(sent) == 1                        # warned once
    async with Session() as s:
        lock = await repo.get_lock(s, uid)
        assert lock.locked is True               # locked in DB


async def test_daily_loss_live_closes_positions():
    from backend.config import settings
    settings.enforcement_mode = "live"
    try:
        Session = await _session_factory()
        uid = await _new_user(Session)
        deals = [make_deal(1, "IN"), make_deal(1, "OUT", profit=-60.0)]
        positions = [
            # one open position to be closed
        ]
        from backend.models import OpenPosition
        from datetime import datetime, timezone
        positions = [OpenPosition(ticket=7, symbol="EURUSD", direction="BUY", volume=0.1,
                                  price_open=1.1, price_current=1.09, sl=0, tp=0, profit=-10,
                                  time=datetime.now(timezone.utc))]
        broker = MockBrokerClient(make_account(balance=940, equity=930, profit=-10), deals=deals,
                                  positions=positions)
        worker = AccountWorker(broker, user_id=uid, telegram_id=900, limits=LIMITS,
                               notify=(lambda t: _true()), session_factory=Session)
        result = await worker.run_once()
        assert broker.close_all_calls == 1       # LIVE: actually closed
        close = next(a for a in result.actions if a.type == "CLOSE_ALL")
        assert close.executed is True
    finally:
        settings.enforcement_mode = "dry_run"   # restore safe default


async def _true() -> bool:
    return True


async def test_dedup_warns_once_across_cycles():
    from backend.config import settings
    settings.enforcement_mode = "dry_run"
    Session = await _session_factory()
    uid = await _new_user(Session)
    deals = [make_deal(1, "IN"), make_deal(2, "IN"), make_deal(3, "IN")]  # 3 opens > limit 2
    broker = MockBrokerClient(make_account(balance=1000, equity=1000), deals=deals)
    sent: list[str] = []
    worker = AccountWorker(broker, user_id=uid, telegram_id=900, limits=LIMITS,
                           notify=await _captured_notify(sent), session_factory=Session)
    await worker.run_once()
    await worker.run_once()   # second cycle should not re-warn the same breach
    assert len(sent) == 1


async def test_algo_disabled_alerts_user():
    """If close fails with retcode 10027 (Algo Trading off), the user is warned
    that protection can't close trades."""
    from backend.config import settings
    settings.enforcement_mode = "live"
    try:
        from datetime import datetime, timezone
        from backend.models import OpenPosition

        class AlgoOffBroker(MockBrokerClient):
            def close_all_positions(self):
                self.close_all_calls += 1
                return ["FAILED 7: close of position 7 failed: retcode=10027 AutoTrading disabled by client"]

        Session = await _session_factory()
        uid = await _new_user(Session)
        deals = [make_deal(1, "IN"), make_deal(1, "OUT", profit=-60.0)]  # daily-loss breach
        positions = [OpenPosition(ticket=7, symbol="XAUUSD", direction="BUY", volume=0.1,
                                  price_open=2400, price_current=2390, sl=0, tp=0, profit=-10,
                                  time=datetime.now(timezone.utc))]
        broker = AlgoOffBroker(make_account(balance=940, equity=930, profit=-10),
                               deals=deals, positions=positions)
        sent: list[str] = []
        worker = AccountWorker(broker, user_id=uid, telegram_id=900, limits=LIMITS,
                               notify=await _captured_notify(sent), session_factory=Session)
        await worker.run_once()
        assert any("Algo Trading" in t and "not protected" in t.lower() for t in sent), sent
    finally:
        settings.enforcement_mode = "dry_run"


async def test_pretrade_gate_times_out_and_closes():
    """A gated trade not confirmed within the window is closed by the worker."""
    _settings.pretrade_gate_enabled = True
    _settings.gate_timeout_seconds = 0   # immediate timeout for the test
    _settings.enforcement_mode = "live"
    try:
        from datetime import datetime, timezone
        from backend.models import OpenPosition
        Session = await _session_factory()
        uid = await _new_user(Session)
        positions = [OpenPosition(ticket=7, symbol="EURUSD", direction="SELL", volume=0.01,
                                  price_open=1.15, price_current=1.15, sl=0, tp=0, profit=0,
                                  time=datetime.now(timezone.utc))]
        broker = MockBrokerClient(make_account(balance=1000, equity=1000), positions=positions)
        sent: list[str] = []
        kb_sent: list = []

        async def notify(t):
            sent.append(t); return True

        async def send_kb(text, kb):
            kb_sent.append((text, kb)); return True

        worker = AccountWorker(broker, user_id=uid, telegram_id=900, limits=LIMITS,
                               notify=notify, send_kb=send_kb, session_factory=Session)
        await worker.run_once()
        # The gate prompt (with a timeframe keyboard) was sent.
        assert kb_sent and "timeframe" in kb_sent[0][0].lower()
        # It timed out (0s) and the worker closed the position.
        assert 7 in broker.closed_tickets
        async with Session() as s:
            t = await repo.get_trade_by_ticket(s, uid, 7)
            assert t.status == "gate_closed" and t.gate_status == "failed"
    finally:
        _settings.pretrade_gate_enabled = False
        _settings.gate_timeout_seconds = 120
        _settings.enforcement_mode = "dry_run"


async def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        await fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} tests passed.")


if __name__ == "__main__":
    asyncio.run(_run())
