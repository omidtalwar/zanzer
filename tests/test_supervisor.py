"""Tests for Supervisor.reconcile using a fake launcher + in-memory DB.

Run with:  python -m tests.test_supervisor
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend import repositories as repo
from backend.db.base import Base
from backend.db import models  # noqa: F401
from backend.db.models import MT5Account
from backend.supervisor import Supervisor


class FakeProc:
    def __init__(self) -> None:
        self._exit: int | None = None

    def poll(self) -> int | None:
        return self._exit

    def terminate(self) -> None:
        self._exit = -15

    def die(self) -> None:
        self._exit = 1


async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _add_active_account(Session, telegram_id: int, terminal_path: str | None = None) -> int:
    async with Session() as s:
        user = await repo.register_user(s, telegram_id, None)
        await repo.activate_subscription(s, user, days=30)
        acct = await repo.add_account(
            s, user, login=1000 + telegram_id, server="X", password="p",
            broker="B", account_type="trading",
        )
        # Give each its own terminal by default so they can all run.
        acct.terminal_path = terminal_path or f"C:/terminals/{telegram_id}/terminal64.exe"
        await s.commit()
        return acct.id


async def _add_trial_only_account(Session, telegram_id: int) -> int:
    # Trial is active too (7 days), so to test "inactive" we expire it.
    async with Session() as s:
        user = await repo.register_user(s, telegram_id, None)
        user.subscription.expires_at = None  # no active subscription
        await s.commit()
        acct = await repo.add_account(
            s, user, login=2000 + telegram_id, server="X", password="p",
            broker="B", account_type="trading",
        )
        return acct.id


async def test_launches_for_active_accounts():
    Session = await _session_factory()
    a1 = await _add_active_account(Session, 1)
    a2 = await _add_active_account(Session, 2)
    launched: list[int] = []

    def launcher(account_id: int) -> FakeProc:
        launched.append(account_id)
        return FakeProc()

    sup = Supervisor(launcher=launcher, session_factory=Session)
    summary = await sup.reconcile()
    assert set(summary["started"]) == {a1, a2}
    assert set(launched) == {a1, a2}

    # Second reconcile: nothing new to start.
    summary2 = await sup.reconcile()
    assert summary2["started"] == []
    assert set(summary2["running"]) == {a1, a2}


async def test_does_not_launch_inactive():
    Session = await _session_factory()
    active = await _add_active_account(Session, 10)
    await _add_trial_only_account(Session, 11)  # inactive
    sup = Supervisor(launcher=lambda i: FakeProc(), session_factory=Session)
    summary = await sup.reconcile()
    assert summary["started"] == [active]


async def test_two_accounts_sharing_default_terminal_only_one_runs():
    # Two active accounts with NO terminal_path → share the default terminal →
    # only one may run; the other is skipped (marked needs_terminal).
    Session = await _session_factory()
    await _add_active_account(Session, 30, terminal_path=None)
    await _add_active_account(Session, 31, terminal_path=None)
    # Force both to share the default terminal (no per-account path).
    async with Session() as s:
        for a in (await s.execute(select(MT5Account))).scalars():
            a.terminal_path = None
        await s.commit()
    sup = Supervisor(launcher=lambda i: FakeProc(), session_factory=Session)
    summary = await sup.reconcile()
    assert len(summary["started"]) == 1  # only one of the two runs


async def test_relaunches_dead_worker():
    Session = await _session_factory()
    a1 = await _add_active_account(Session, 20)
    procs: dict[int, FakeProc] = {}

    def launcher(account_id: int) -> FakeProc:
        p = FakeProc()
        procs[account_id] = p
        return p

    sup = Supervisor(launcher=launcher, session_factory=Session)
    await sup.reconcile()
    procs[a1].die()                       # simulate crash
    summary = await sup.reconcile()
    assert a1 in summary["started"]       # relaunched


async def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        await fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} tests passed.")


if __name__ == "__main__":
    asyncio.run(_run())
