"""Supervisor — orchestrates one worker process per active account.

The terminal farm runs many MT5 terminals; since the MetaTrader5 package is
one-terminal-per-process, each account needs its own OS process. The supervisor
periodically reconciles desired vs running:
  - active account with no process  -> launch `python -m backend.run_account <id>`
  - running process for an inactive  -> terminate
  - dead process for an active acct  -> relaunch

The launcher is injectable so the reconcile logic is unit-testable without
spawning real processes.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from typing import Callable, Protocol

from backend import repositories as repo
from backend.config import settings
from backend.db.session import SessionLocal
from backend.logging_config import get_logger

log = get_logger("supervisor")


class Handle(Protocol):
    def poll(self) -> int | None: ...      # None if still running
    def terminate(self) -> None: ...


def _default_launcher(account_id: int) -> Handle:
    return subprocess.Popen([sys.executable, "-m", "backend.run_account", str(account_id)])


class Supervisor:
    def __init__(self, launcher: Callable[[int], Handle] = _default_launcher,
                 session_factory=SessionLocal) -> None:
        self._launcher = launcher
        self._session_factory = session_factory
        self._procs: dict[int, Handle] = {}  # account_id -> process handle

    async def _active_account_ids(self) -> set[int]:
        """Accounts that can safely run right now.

        Two accounts must NOT share one MT5 terminal (the package is
        one-terminal-per-process), so we run at most one account per distinct
        terminal_path. Accounts without their own terminal share the default
        terminal slot — only one of those can run. The rest are marked
        'needs_terminal' so the user/admin knows to enable provisioning.
        """
        async with self._session_factory() as session:
            accounts = await repo.list_active_accounts(session)
            seen_terminals: set[str] = set()
            runnable: set[int] = set()
            for a in accounts:
                key = a.terminal_path or "__default__"
                if key in seen_terminals:
                    log.warning(
                        "account %s can't run: terminal '%s' already used by another "
                        "account — enable AUTO_PROVISION so each gets its own terminal",
                        a.id, key,
                    )
                    await repo.set_account_status(session, a.id, "needs_terminal")
                    continue
                seen_terminals.add(key)
                runnable.add(a.id)
            return runnable

    async def reconcile(self) -> dict:
        """One reconcile pass. Returns a summary of actions taken."""
        desired = await self._active_account_ids()
        running = set(self._procs)

        # Relaunch any that died.
        relaunched = []
        for acct_id in list(self._procs):
            if self._procs[acct_id].poll() is not None:  # exited
                del self._procs[acct_id]
                running.discard(acct_id)
                if acct_id in desired:
                    relaunched.append(acct_id)

        to_start = (desired - running) | set(relaunched)
        to_stop = running - desired

        for acct_id in to_stop:
            log.info("Stopping worker for account %s (no longer active)", acct_id)
            try:
                self._procs[acct_id].terminate()
            finally:
                self._procs.pop(acct_id, None)

        for acct_id in to_start:
            log.info("Launching worker for account %s", acct_id)
            self._procs[acct_id] = self._launcher(acct_id)

        return {
            "running": sorted(self._procs),
            "started": sorted(to_start),
            "stopped": sorted(to_stop),
        }

    async def run_forever(self, interval: int | None = None) -> None:
        interval = interval or max(5, settings.risk_check_interval_seconds)
        log.info("Supervisor started (reconcile every %ss, mode=%s)",
                 interval, settings.enforcement_mode)
        try:
            while True:
                try:
                    await self.reconcile()
                except Exception as exc:  # noqa: BLE001
                    log.error("reconcile failed: %s", exc)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            for h in self._procs.values():
                h.terminate()
            raise


async def _main() -> None:
    from backend.db.session import init_db
    from backend.logging_config import setup_logging
    setup_logging()
    await init_db()
    await Supervisor().run_forever()


if __name__ == "__main__":
    asyncio.run(_main())
