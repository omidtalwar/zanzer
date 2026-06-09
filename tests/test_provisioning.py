"""Tests for terminal provisioning using a FAKE base install in temp dirs.

We don't copy a real MT5 install — we create a dummy folder with a fake
terminal64.exe and verify the clone + path bookkeeping + idempotency.

Run with:  python -m tests.test_provisioning
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend import provisioning, repositories as repo
from backend.config import settings
from backend.db.base import Base
from backend.db import models  # noqa: F401


async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def _fake_base(tmp: Path) -> Path:
    base = tmp / "base_mt5"
    base.mkdir()
    (base / "terminal64.exe").write_text("fake exe")
    (base / "config").mkdir()
    (base / "config" / "common.ini").write_text("x")
    # a dir we expect to be skipped
    (base / "logs").mkdir()
    (base / "logs" / "20260101.log").write_text("noise")
    return base


async def test_provision_clones_and_records_path():
    Session = await _session_factory()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        base = _fake_base(tmp)
        settings.base_terminal_dir = str(base)
        settings.terminals_root = str(tmp / "terminals")
        try:
            async with Session() as s:
                user = await repo.register_user(s, 1, None)
                acct = await repo.add_account(
                    s, user, login=555, server="X", password="p",
                    broker=None, account_type="trading",
                )
                path = await provisioning.ensure_provisioned(s, acct)
            assert path is not None
            assert Path(path).exists()                       # cloned exe exists
            assert Path(path).name == "terminal64.exe"
            assert not (Path(path).parent / "logs").exists()  # logs skipped
            assert (Path(path).parent / "config" / "common.ini").exists()
        finally:
            settings.base_terminal_dir = None
            settings.terminals_root = "terminals"


async def test_provision_is_idempotent():
    Session = await _session_factory()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        settings.base_terminal_dir = str(_fake_base(tmp))
        settings.terminals_root = str(tmp / "terminals")
        try:
            async with Session() as s:
                user = await repo.register_user(s, 2, None)
                acct = await repo.add_account(
                    s, user, login=777, server="X", password="p",
                    broker=None, account_type="trading",
                )
                p1 = await provisioning.ensure_provisioned(s, acct)
                p2 = await provisioning.ensure_provisioned(s, acct)
            assert p1 == p2
        finally:
            settings.base_terminal_dir = None
            settings.terminals_root = "terminals"


async def test_provision_skips_when_base_missing():
    Session = await _session_factory()
    settings.base_terminal_dir = None  # not configured
    async with Session() as s:
        user = await repo.register_user(s, 3, None)
        acct = await repo.add_account(
            s, user, login=999, server="X", password="p",
            broker=None, account_type="trading",
        )
        path = await provisioning.ensure_provisioned(s, acct)
    assert path is None  # gracefully skipped → caller falls back to shared terminal


async def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        await fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} tests passed.")


if __name__ == "__main__":
    asyncio.run(_run())
