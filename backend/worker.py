"""Per-account risk worker (terminal-farm model).

One worker drives ONE account through a BrokerClient, using that user's own
risk limits and a DB-backed lock. It reuses the exact same pure logic as the
personal app:
  - risk_service.compute_risk_status  (now per-user limits)
  - enforcement_service.decide_actions (pure policy)

Execution per cycle:
  WARN      -> notify callback (Telegram), deduped per day, + risk_event
  LOCK      -> set DB lock (daily), + risk_event
  CLOSE_ALL -> broker.close_all_positions() in LIVE mode; logged-only in dry_run

In production each worker runs in its OWN process (the MetaTrader5 package is
one-terminal-per-process). The Supervisor launches one per active account.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from backend import repositories as repo
from backend.bot import client as bot_client
from backend.broker.base import BrokerClient
from backend.config import settings
from backend.db.session import SessionLocal
from backend.logging_config import get_logger
from backend.models import (
    EnforcementAction,
    EnforcementResult,
    LockState,
    RiskLimits,
    StatusResponse,
)
from backend.services import telegram_service
from backend.services.enforcement_service import decide_actions
from backend.services.risk_service import compute_risk_status

log = get_logger("worker")

# notify(text) -> awaitable[bool]; default sends to the central Telegram chat.
NotifyFn = Callable[[str], Awaitable[bool]]


def _trading_session(hour: int) -> str:
    """Forex session name from UTC hour of trade entry."""
    sessions = []
    if hour < 9 or hour >= 23:
        sessions.append("Asian")
    if 8 <= hour < 17:
        sessions.append("London")
    if 13 <= hour < 22:
        sessions.append("New York")
    return "/".join(sessions) if sessions else "Off-hours"


def _fmt_duration(secs: int | None) -> str:
    if secs is None or secs < 0:
        return "open"
    if secs < 60:
        return f"{secs}s"
    m = secs // 60
    if m < 60:
        return f"{m}m"
    h, rem = divmod(m, 60)
    return f"{h}h {rem}m" if rem else f"{h}h"


def _build_yesterday_trades(deals, server_offset: timedelta) -> list[dict]:
    """Pair IN/OUT deals by position_id and return one dict per completed trade."""
    by_pos: dict[int, list] = {}
    for d in deals:
        by_pos.setdefault(d.position_id, []).append(d)

    trades = []
    for pos_deals in by_pos.values():
        in_deals = [d for d in pos_deals if d.entry == "IN"]
        out_deals = [d for d in pos_deals if d.entry in ("OUT", "INOUT")]
        if not in_deals:
            continue
        in_d = min(in_deals, key=lambda d: d.time)
        out_d = max(out_deals, key=lambda d: d.time) if out_deals else None
        profit = round(sum(d.profit for d in out_deals), 2)
        duration_s = int((out_d.time - in_d.time).total_seconds()) if out_d else None
        # Convert server-time-as-UTC back to actual UTC for session detection.
        actual_utc = in_d.time - server_offset
        trades.append({
            "symbol": in_d.symbol,
            "direction": in_d.direction or "BUY",
            "profit": profit,
            "duration_s": duration_s,
            "session": _trading_session(actual_utc.hour),
            "entry_time": actual_utc.isoformat(),
        })
    return trades


class AccountWorker:
    def __init__(
        self,
        broker: BrokerClient,
        *,
        user_id: int,
        telegram_id: int,
        limits: RiskLimits,
        account_id: int | None = None,
        notify: NotifyFn | None = None,
        session_factory=SessionLocal,
    ) -> None:
        self.broker = broker
        self.user_id = user_id
        self.telegram_id = telegram_id
        self.limits = limits
        self.account_id = account_id
        # Default: send to THIS user's Telegram chat (multi-user correct).
        self.notify = notify or self._notify_user
        self._session_factory = session_factory
        self._alerted: set[tuple[str, str]] = set()  # (date, reason) dedup

    async def _notify_user(self, text: str) -> bool:
        return await bot_client.send_message(self.telegram_id, text)

    def _build_status(self) -> StatusResponse:
        account = self.broker.get_account_info()
        positions = self.broker.get_open_positions()
        deals = self.broker.get_today_deals()
        risk = compute_risk_status(account, deals, self.limits)
        return StatusResponse(
            account=account, open_positions=positions, risk=risk,
            generated_at=datetime.now(tz=timezone.utc),
        )

    def _dedup(self, reason: str) -> bool:
        key = (datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"), reason)
        if key in self._alerted:
            return True
        self._alerted.add(key)
        return False

    async def _account_status(self) -> str | None:
        if self.account_id is None:
            return None
        async with self._session_factory() as session:
            acct = await repo.get_account_by_id(session, self.account_id)
            return acct.status if acct else None

    async def _refresh_limits(self) -> None:
        """Reload this user's risk settings so rule changes apply without a
        worker restart (within one cycle)."""
        try:
            async with self._session_factory() as session:
                user = await repo.get_user(session, self.telegram_id)
                if user and user.risk_settings:
                    self.limits = RiskLimits.from_orm(user.risk_settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not refresh limits for user %s: %s", self.user_id, exc)

    async def run_once(self) -> EnforcementResult:
        await self._refresh_limits()
        prev_status = await self._account_status()
        try:
            status = self._build_status()
        except Exception:
            # Connection/read failed — mark error and tell the user (once).
            if self.account_id is not None:
                async with self._session_factory() as session:
                    await repo.set_account_status(session, self.account_id, "error")
                if prev_status != "error":
                    await self.notify(
                        "❌ I couldn't log in to your MT5 account. Please check it's "
                        "correct and send /link again with the right login, server, and password."
                    )
            raise

        # Compute yesterday's trade performance for /yesterday command.
        yesterday_json: str | None = None
        try:
            offset = self.broker.get_server_offset()
            yesterday_deals = self.broker.get_yesterday_deals()
            trades = _build_yesterday_trades(yesterday_deals, offset)
            yesterday_json = json.dumps(trades)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not fetch yesterday deals for user %s: %s", self.user_id, exc)

        # Persist the latest live data so the bot's /status can show it.
        async with self._session_factory() as session:
            await repo.upsert_snapshot(session, self.user_id, status, yesterday_json=yesterday_json)

        # Successful read → the account is live/monitored.
        if self.account_id is not None:
            async with self._session_factory() as session:
                await repo.set_account_status(session, self.account_id, "active")
            if prev_status != "active":
                await self.notify(
                    "✅ Your MT5 account is connected. Zanzer is now protecting it "
                    "and watching your risk limits."
                )

        live = settings.is_live_enforcement

        async with self._session_factory() as session:
            lock_row = await repo.get_lock(session, self.user_id)
            lock_state = LockState(
                locked=lock_row.locked, reason=lock_row.reason,
                locked_at=lock_row.locked_at, day=lock_row.day,
            )
            planned = decide_actions(
                status.risk, lock_state, has_open_positions=bool(status.open_positions)
            )
            executed: list[EnforcementAction] = []

            for action in planned:
                executed.append(await self._execute(session, action, status, live))

            if planned:
                log.info(
                    "user=%s cycle (mode=%s): %s",
                    self.user_id, settings.enforcement_mode,
                    ", ".join(f"{a.type}[{'x' if a.executed else '-'}]" for a in executed),
                )

        return EnforcementResult(
            mode=settings.enforcement_mode,
            any_limit_hit=status.risk.any_limit_hit,
            actions=executed,
            generated_at=datetime.now(tz=timezone.utc),
        )

    async def _execute(self, session, action: EnforcementAction,
                       status: StatusResponse, live: bool) -> EnforcementAction:
        if action.type == "WARN":
            if self._dedup(f"WARN:{action.reason}"):
                return action.model_copy(update={"executed": False, "detail": "deduped"})
            await repo.add_risk_event(session, self.user_id, "WARN", action.reason)
            sent = await self.notify(telegram_service.format_risk_alert(status))
            return action.model_copy(
                update={"executed": sent, "detail": "alert sent" if sent else "telegram off"}
            )

        if action.type == "LOCK":
            await repo.set_lock(session, self.user_id, action.reason, daily=True)
            await repo.add_risk_event(session, self.user_id, "LOCK", action.reason)
            return action.model_copy(update={"executed": True, "detail": "trading locked (daily)"})

        if action.type == "CLOSE_ALL":
            if not live:
                n = len(status.open_positions)
                await repo.add_risk_event(
                    session, self.user_id, "CLOSE_ALL_DRYRUN",
                    f"would close {n} position(s): {action.reason}",
                )
                return action.model_copy(
                    update={"executed": False, "detail": f"dry_run: would close {n} position(s)"}
                )
            results = self.broker.close_all_positions()
            await repo.add_risk_event(
                session, self.user_id, "CLOSE_ALL", "; ".join(results) or "no positions"
            )
            return action.model_copy(
                update={"executed": True, "detail": "; ".join(results) or "no positions"}
            )

        return action.model_copy(update={"executed": False, "detail": "unknown action"})
