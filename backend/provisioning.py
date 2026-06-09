"""Per-account MT5 terminal provisioning for the terminal farm.

To run many accounts on one Windows VPS, each needs its own MT5 terminal
(one terminal = one account). Provisioning clones a base MT5 install into a
per-account folder and records its `terminal_path`; the worker then launches it
in portable mode and logs into that account.

Prerequisites on the VPS:
  - A base MT5 install whose folder is set in BASE_TERMINAL_DIR.
  - Enough disk for one clone per account (we skip logs/history to slim it).
  - AUTO_PROVISION=true (or run `python -m backend.provisioning <account_id>`).

Honest limits: MT5 terminal setup/login automation is finicky. This handles the
file cloning + path bookkeeping + portable launch. Validate with 2 accounts on a
real VPS before relying on it at scale.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

from backend import repositories as repo
from backend.config import settings
from backend.db.models import MT5Account
from backend.db.session import SessionLocal
from backend.logging_config import get_logger

log = get_logger("provisioning")

# Big/regenerable subdirectories we don't need to copy.
_IGNORE = shutil.ignore_patterns("logs", "Bases", "history", "*.log", "Tester")


def terminal_dir_for(login: int) -> Path:
    return Path(settings.terminals_root) / str(login)


def terminal_exe_for(login: int) -> Path:
    return terminal_dir_for(login) / "terminal64.exe"


def _clone_base(login: int) -> Path:
    """Clone the base install into the per-account folder (skip if present)."""
    base = settings.base_terminal_dir
    if not base or not Path(base).exists():
        raise FileNotFoundError(
            "BASE_TERMINAL_DIR is not set or doesn't exist; cannot provision a "
            "per-account terminal. Set it to a base MT5 install folder."
        )
    dest = terminal_dir_for(login)
    exe = dest / "terminal64.exe"
    if exe.exists():
        return exe
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("Cloning base terminal %s -> %s", base, dest)
    shutil.copytree(base, dest, ignore=_IGNORE, dirs_exist_ok=True)
    if not exe.exists():
        raise FileNotFoundError(f"clone finished but {exe} not found (wrong BASE_TERMINAL_DIR?)")
    return exe


async def ensure_provisioned(session, account: MT5Account) -> str | None:
    """Make sure this account has its own terminal; return its terminal_path.

    Returns the existing path if already provisioned. Returns None (and logs)
    if provisioning isn't possible, so the caller can fall back to the shared
    default terminal.
    """
    if account.terminal_path and Path(account.terminal_path).exists():
        return account.terminal_path
    try:
        exe = _clone_base(account.login)
    except FileNotFoundError as exc:
        log.warning("provisioning skipped for account %s: %s", account.id, exc)
        return None
    account.terminal_path = str(exe)
    await session.commit()
    log.info("Provisioned terminal for account %s at %s", account.id, exe)
    return str(exe)


def launch_terminal(terminal_path: str) -> None:
    """Best-effort launch of a portable terminal (so it runs isolated).

    The MetaTrader5 package can also auto-launch on initialize(), but starting it
    in /portable mode first guarantees per-account data isolation.
    """
    try:
        subprocess.Popen(
            [terminal_path, "/portable"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        log.info("Launched portable terminal: %s", terminal_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not launch terminal %s: %s", terminal_path, exc)


async def provision_account(account_id: int) -> str | None:
    """Provision a single account by id (used by the CLI / admin command)."""
    async with SessionLocal() as session:
        account = await session.get(MT5Account, account_id)
        if account is None:
            log.error("account %s not found", account_id)
            return None
        return await ensure_provisioned(session, account)


async def _main(account_id: int) -> None:
    from backend.db.session import init_db
    from backend.logging_config import setup_logging
    setup_logging()
    await init_db()
    path = await provision_account(account_id)
    print(f"terminal_path = {path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m backend.provisioning <account_id>")
    asyncio.run(_main(int(sys.argv[1])))
