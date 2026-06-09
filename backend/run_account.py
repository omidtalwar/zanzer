"""Single-account worker process (one per account in the terminal farm).

The Supervisor launches one of these per active account:
    python -m backend.run_account <account_id>

It connects to that account's MT5 terminal, then loops the risk/enforcement
cycle using the user's own limits, writing locks/events to the shared DB.
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.broker.local_mt5 import make_local_client
from backend.config import settings
from backend.db.models import MT5Account, User
from backend.db.session import SessionLocal, init_db
from backend.logging_config import get_logger, setup_logging
from backend.models import RiskLimits
from backend.security import decrypt
from backend.worker import AccountWorker

log = get_logger("run_account")


async def _load(account_id: int):
    async with SessionLocal() as session:
        result = await session.execute(
            select(MT5Account)
            .where(MT5Account.id == account_id)
            .options(
                selectinload(MT5Account.user).selectinload(User.risk_settings),
            )
        )
        return result.scalar_one_or_none()


async def main(account_id: int) -> None:
    setup_logging()
    await init_db()
    account = await _load(account_id)
    if account is None:
        log.error("account %s not found", account_id)
        return

    # Terminal-farm: ensure this account has its own terminal, then launch it.
    terminal_path = account.terminal_path
    if settings.auto_provision:
        from backend import provisioning
        async with SessionLocal() as session:
            acct = await session.get(type(account), account_id)
            terminal_path = await provisioning.ensure_provisioned(session, acct) or terminal_path
        if terminal_path:
            provisioning.launch_terminal(terminal_path)

    broker = make_local_client(
        login=account.login,
        password=decrypt(account.password_encrypted),
        server=account.server,
        terminal_path=terminal_path,
    )
    limits = RiskLimits.from_orm(account.user.risk_settings)
    worker = AccountWorker(
        broker,
        user_id=account.user.id,
        telegram_id=account.user.telegram_id,
        limits=limits,
        account_id=account.id,
    )

    interval = max(1, settings.risk_check_interval_seconds)
    log.info("Worker started for account_id=%s (user=%s, every %ss, mode=%s)",
             account_id, account.user.id, interval, settings.enforcement_mode)
    connected_once = False
    while True:
        try:
            await worker.run_once()
            connected_once = True
        except Exception as exc:  # noqa: BLE001
            log.error("account %s cycle failed: %s", account_id, exc)
            # If we never connected (e.g. bad/old credentials), EXIT so the
            # supervisor relaunches us with fresh credentials from the DB
            # (this is how a user's /link fix takes effect without manual steps).
            if not connected_once:
                log.error("account %s: initial connect failed; exiting for relaunch", account_id)
                return
        await asyncio.sleep(interval)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m backend.run_account <account_id>")
    asyncio.run(main(int(sys.argv[1])))
