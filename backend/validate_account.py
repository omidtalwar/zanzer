"""Validate one account's MT5 credentials in an isolated process.

Used by the bot to give users an instant ✅/❌ when they /link. Runs as its own
process (the MetaTrader5 package is one-terminal-per-process), connects with the
account's stored+decrypted credentials, updates its status, and prints a single
JSON line with the result.

    python -m backend.validate_account <account_id>
"""
from __future__ import annotations

import asyncio
import json
import sys

from backend import repositories as repo
from backend.broker.local_mt5 import make_local_client
from backend.db.models import MT5Account
from backend.db.session import SessionLocal, init_db
from backend.security import decrypt


async def main(account_id: int) -> None:
    await init_db()
    async with SessionLocal() as session:
        acct = await session.get(MT5Account, account_id)
        if acct is None:
            print(json.dumps({"ok": False, "message": "account not found"}))
            return
        login, server = acct.login, acct.server
        password = decrypt(acct.password_encrypted)
        terminal_path = acct.terminal_path

    broker = make_local_client(
        login=login, password=password, server=server, terminal_path=terminal_path
    )
    try:
        info = broker.get_account_info()
        async with SessionLocal() as session:
            await repo.set_account_status(session, account_id, "active")
        print(json.dumps({
            "ok": True,
            "message": f"{info.login} @ {info.server}",
            "balance": info.balance,
            "currency": info.currency,
        }))
    except Exception as exc:  # noqa: BLE001
        async with SessionLocal() as session:
            await repo.set_account_status(session, account_id, "error")
        print(json.dumps({"ok": False, "message": str(exc)}))
    finally:
        try:
            broker.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m backend.validate_account <account_id>")
    asyncio.run(main(int(sys.argv[1])))
