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
from backend.services.psychology_service import (
    LOCK_THRESHOLD,
    compute_day_score,
    score_emoji,
    score_label,
)
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
        # Ticket set from the previous cycle — used to detect opens/closes.
        self._prev_tickets: set[int] = set()
        # Psychology engine: timestamp of the most-recent closing losing trade.
        self._last_loss_closed_at: datetime | None = None
        # Track the newest position opened this cycle for revenge detection.
        self._new_position_opened_at: datetime | None = None

    # Reminder windows: send a reminder after these many minutes without a journal response.
    _REMINDER_MINUTES = [5, 15, 30]
    _MAX_REMINDERS = len(_REMINDER_MINUTES)  # after this → mark skipped

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

    # --------------------------------------------------------- journal helpers
    def _entry_prompt(self, pos) -> str:
        direction = pos.direction if hasattr(pos, "direction") else "?"
        symbol = pos.symbol if hasattr(pos, "symbol") else "?"
        volume = pos.volume if hasattr(pos, "volume") else "?"
        price = pos.price_open if hasattr(pos, "price_open") else "?"
        return (
            f"🔔 <b>New trade detected</b>\n"
            f"<b>{symbol}</b> {direction} {volume} lots @ {price}\n\n"
            f"📝 <b>Entry Journal (1/4)</b>\n"
            f"What's your setup / reason for this trade?\n"
            f"<i>(e.g. London breakout, support bounce, trend continuation)</i>"
        )

    def _exit_prompt(self, trade, pos_data: dict) -> str:
        profit = pos_data.get("profit", 0.0)
        dur = _fmt_duration(pos_data.get("duration_s"))
        emoji = "✅" if profit >= 0 else "❌"
        return (
            f"{emoji} <b>Trade closed</b>\n"
            f"<b>{trade.symbol}</b> {trade.direction} | "
            f"P&L: <b>{profit:+.2f}</b> | Duration: {dur}\n\n"
            f"📝 <b>Exit Journal (1/5)</b>\n"
            f"Why did you exit?\n"
            f"Reply: <b>tp</b> · <b>sl</b> · <b>manual</b> · <b>partial</b>"
        )

    async def _handle_new_positions(self, session, current_positions) -> None:
        """Detect positions that weren't open last cycle → open_trade + entry prompt."""
        current_tickets = {p.ticket for p in current_positions}
        new_tickets = current_tickets - self._prev_tickets
        self._new_position_opened_at = None  # reset each cycle

        for pos in current_positions:
            if pos.ticket not in new_tickets:
                continue
            existing = await repo.get_trade_by_ticket(session, self.user_id, pos.ticket)
            if existing is not None:
                continue  # already recorded (e.g. worker restarted)
            trade = await repo.open_trade(
                session, self.user_id,
                ticket=pos.ticket, symbol=pos.symbol, direction=pos.direction,
                volume=pos.volume, entry_price=pos.price_open,
                sl=pos.sl if pos.sl else None,
                tp=pos.tp if pos.tp else None,
                opened_at=pos.time,
            )
            self._new_position_opened_at = pos.time  # for revenge detection
            await self.notify(self._entry_prompt(pos))
            log.info("journal: new trade %s (ticket=%s)", trade.id, pos.ticket)

    async def _handle_closed_positions(self, session, current_positions) -> None:
        """Detect positions that closed since last cycle → close_trade + exit prompt."""
        current_tickets = {p.ticket for p in current_positions}
        closed_tickets = self._prev_tickets - current_tickets
        if not closed_tickets:
            return

        # Pull from MT5 history to get exit price + profit.
        today_deals = self.broker.get_today_deals()
        deal_by_pos: dict[int, object] = {}
        for d in today_deals:
            if d.entry in ("OUT", "INOUT") and d.position_id not in deal_by_pos:
                deal_by_pos[d.position_id] = d

        for ticket in closed_tickets:
            trade = await repo.get_trade_by_ticket(session, self.user_id, ticket)
            if trade is None or trade.status not in ("open", "entry_skipped"):
                continue
            deal = deal_by_pos.get(ticket)
            exit_price = deal.price if deal else trade.entry_price
            profit = deal.profit if deal else 0.0
            closed_at = deal.time if deal else datetime.now(tz=timezone.utc)
            trade = await repo.close_trade(
                session, trade,
                exit_price=exit_price, profit=profit, closed_at=closed_at,
            )
            # Track last losing close for revenge detection next cycle.
            if profit < 0:
                self._last_loss_closed_at = closed_at
            pos_data = {
                "profit": profit,
                "duration_s": trade.duration_s,
            }
            await self.notify(self._exit_prompt(trade, pos_data))
            log.info("journal: closed trade %s (ticket=%s) P&L=%s", trade.id, ticket, profit)

    async def _process_journal_reminders(self, session) -> None:
        """Re-prompt or skip unjournaled trades based on time elapsed."""
        unjournaled = await repo.get_unjournaled_trades(session, self.user_id)
        now = datetime.now(tz=timezone.utc)

        for trade in unjournaled:
            # --- entry journal reminder ---
            if trade.entry_journal_id is None and trade.entry_prompted_at:
                prompted = trade.entry_prompted_at
                if prompted.tzinfo is None:
                    prompted = prompted.replace(tzinfo=timezone.utc)
                elapsed_m = (now - prompted).total_seconds() / 60
                count = trade.entry_reminder_count

                if count >= self._MAX_REMINDERS:
                    # Out of reminders → skip and penalise.
                    await repo.skip_entry_journal(session, trade)
                    await self.notify(
                        f"⚠️ Entry journal for <b>{trade.symbol}</b> was skipped "
                        f"(no response after {self._MAX_REMINDERS} reminders). "
                        f"Emotion score −10. Use /journal to add notes later."
                    )
                elif count < len(self._REMINDER_MINUTES) and elapsed_m >= self._REMINDER_MINUTES[count]:
                    await repo.bump_entry_reminder(session, trade)
                    await self.notify(
                        f"📝 Reminder #{count + 1}: still waiting for your <b>entry journal</b> "
                        f"on {trade.symbol} {trade.direction}.\n"
                        f"Reply with your setup reason or use /journal {trade.id} to fill it in."
                    )

            # --- exit journal reminder ---
            if (trade.status in ("closed", "entry_skipped")
                    and trade.exit_journal_id is None and trade.exit_prompted_at):
                prompted = trade.exit_prompted_at
                if prompted.tzinfo is None:
                    prompted = prompted.replace(tzinfo=timezone.utc)
                elapsed_m = (now - prompted).total_seconds() / 60
                count = trade.exit_reminder_count

                if count >= self._MAX_REMINDERS:
                    await repo.skip_exit_journal(session, trade)
                    await self.notify(
                        f"⚠️ Exit journal for <b>{trade.symbol}</b> was skipped. "
                        f"Emotion score −10. Use /journal to add notes later."
                    )
                elif count < len(self._REMINDER_MINUTES) and elapsed_m >= self._REMINDER_MINUTES[count]:
                    await repo.bump_exit_reminder(session, trade)
                    pos_data = {"profit": trade.profit or 0.0, "duration_s": trade.duration_s}
                    await self.notify(
                        f"📝 Reminder #{count + 1}: still waiting for your <b>exit journal</b> "
                        f"on {trade.symbol} {trade.direction}.\n"
                        + self._exit_prompt(trade, pos_data)
                    )

    async def _run_psychology_cycle(self, session, server_date: str) -> None:
        """Compute today's emotion score, persist it, auto-lock if < threshold."""
        trades, journals = await repo.get_today_trades_with_journals(session, self.user_id, server_date)

        trade_dicts = [
            {
                "id": t.id, "profit": t.profit, "status": t.status,
                "entry_journal_id": t.entry_journal_id,
                "exit_journal_id": t.exit_journal_id,
                "entry_prompted_at": t.entry_prompted_at,
                "opened_at": t.opened_at,
            }
            for t in trades
        ]
        journal_dicts = [
            {
                "trade_id": j.trade_id, "type": j.type,
                "plan_followed": j.plan_followed, "skipped": j.skipped,
            }
            for j in journals
        ]

        import json as _json
        day = compute_day_score(
            today_trades=trade_dicts,
            today_journals=journal_dicts,
            last_loss_closed_at=self._last_loss_closed_at,
            new_position_opened_at=self._new_position_opened_at,
            date=server_date,
        )

        events_json = _json.dumps(
            [{"reason": e.reason, "delta": e.delta, "ts": e.ts} for e in day.events]
        )
        await repo.upsert_emotion_score(
            session, self.user_id, server_date,
            score=day.score, events_json=events_json,
            locked_by_score=day.locked_by_score,
        )

        # Auto-lock if score just dropped below threshold (once per day).
        if day.locked_by_score:
            lock_row = await repo.get_lock(session, self.user_id)
            if not lock_row.locked:
                await repo.set_lock(
                    session, self.user_id,
                    f"Emotion score {day.score}/100 — below {LOCK_THRESHOLD}",
                    daily=True,
                )
                await repo.add_risk_event(
                    session, self.user_id, "PSYCH_LOCK",
                    f"Score dropped to {day.score} on {server_date}",
                )
                emoji = score_emoji(day.score)
                await self.notify(
                    f"{emoji} <b>Trading locked — emotion score too low</b>\n\n"
                    f"Your score is <b>{day.score}/100</b> ({score_label(day.score)}).\n\n"
                    f"This lock holds until the next trading day.\n"
                    f"Send /explain to record what happened — it's saved to your journal."
                )

    async def _refresh_limits(self) -> None:
        """Reload this user's risk settings so rule changes apply without a
        worker restart (within one cycle)."""
        try:
            async with self._session_factory() as session:
                user = await repo.get_user(session, self.telegram_id)
                if user and user.risk_settings:
                    # Apply any deferred (looser) rule change whose day has come.
                    await repo.promote_pending_risk(session, user.risk_settings)
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

        # V3 — journal: detect new opens, detect closes, send reminders.
        try:
            async with self._session_factory() as session:
                await self._handle_new_positions(session, status.open_positions)
                await self._handle_closed_positions(session, status.open_positions)
                await self._process_journal_reminders(session)
        except Exception as exc:  # noqa: BLE001
            log.warning("journal cycle error for user %s: %s", self.user_id, exc)
        finally:
            self._prev_tickets = {p.ticket for p in status.open_positions}

        # V4 — psychology engine: score + revenge detection + auto-lock.
        try:
            offset = self.broker.get_server_offset()
            server_date = (datetime.now(tz=timezone.utc) + offset).strftime("%Y-%m-%d")
            async with self._session_factory() as session:
                await self._run_psychology_cycle(session, server_date)
        except Exception as exc:  # noqa: BLE001
            log.warning("psychology cycle error for user %s: %s", self.user_id, exc)

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
            # Detect silent-protection-failure: terminal has Algo Trading OFF, so
            # close orders are rejected (retcode 10027). Alert user + admins.
            if any("10027" in r or "AutoTrading disabled" in r for r in results):
                await self._alert_algo_disabled(session)
            return action.model_copy(
                update={"executed": True, "detail": "; ".join(results) or "no positions"}
            )

        return action.model_copy(update={"executed": False, "detail": "unknown action"})

    async def _alert_algo_disabled(self, session) -> None:
        """Warn the user + admins that protection can't close trades because the
        MT5 terminal's Algo Trading is disabled. Deduped once per day."""
        if self._dedup("ALGO_OFF"):
            return
        await repo.add_risk_event(
            session, self.user_id, "ALGO_DISABLED",
            "Algo Trading disabled in terminal — could not close (retcode 10027)",
        )
        await self.notify(
            "🚨 <b>Protection can't close your trades!</b>\n\n"
            "Your MT5 terminal has <b>Algo Trading turned OFF</b>, so Zanzer is "
            "blocked from closing positions when a risk limit is hit.\n\n"
            "👉 In MetaTrader 5, click the <b>Algo Trading</b> button in the toolbar "
            "so it turns green (or Tools → Options → Expert Advisors → "
            "'Allow algorithmic trading'). You are <b>not protected</b> until you do."
        )
        # Notify admins too — a customer silently unprotected is an ops issue.
        for admin_id in settings.admin_ids:
            if admin_id == self.telegram_id:
                continue
            try:
                await bot_client.send_message(
                    admin_id,
                    f"⚠️ Algo Trading is OFF for user <code>{self.telegram_id}</code> "
                    f"(account {self.account_id}) — closes are failing (retcode 10027).",
                )
            except Exception:  # noqa: BLE001
                pass
