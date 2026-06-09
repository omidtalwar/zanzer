"""V2 Risk Enforcement Engine.

Design: decision is a PURE function (`decide_actions`) that maps a risk snapshot
to a list of intended actions. Execution (`run_cycle`) performs them according
to the configured mode. This split keeps the safety-critical policy testable
without MT5 or Telegram, and makes the dry_run/live boundary explicit.

Action policy (PRD "Actions": block / close / warn / lock):
- daily loss limit hit       -> WARN + LOCK + CLOSE_ALL   (protect capital)
- daily trade limit hit       -> WARN + LOCK              (no more trades today)
- consecutive loss limit hit  -> WARN + LOCK              (cool-off)
- exposure limit hit          -> WARN                     (informational)

CLOSE_ALL is the only action that touches the broker, and only executes in
live mode. In dry_run every action is recorded with executed=False.

Core invariant (PRD): risk management overrides everything. This engine never
asks the AI; it acts purely on measured rules.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.config import settings
from backend.logging_config import get_logger
from backend.models import (
    EnforcementAction,
    EnforcementResult,
    LockState,
    RiskStatus,
    StatusResponse,
)
from backend.services import lock_service, telegram_service
from backend.services.mt5_service import MT5Error, mt5_service
from backend.services.status_service import build_status

log = get_logger("enforce")


def decide_actions(
    risk: RiskStatus, lock: LockState, has_open_positions: bool = False
) -> list[EnforcementAction]:
    """Pure policy: given the risk snapshot and current lock, list intended
    actions. `executed` defaults False; the executor sets it true when done.

    `has_open_positions` lets the policy ENFORCE a lock by flattening: a locked
    account must be flat, so any open position (including newly opened trades) is
    closed. This is how "no trading while locked" is enforced on the terminal.
    """
    actions: list[EnforcementAction] = []

    def add(type_: str, reason: str) -> None:
        actions.append(EnforcementAction(type=type_, reason=reason, executed=False))

    if risk.daily_loss_limit_hit:
        cap = f"-{risk.max_daily_loss_usd:g}$" if risk.max_daily_loss_usd > 0 else f"-{risk.max_daily_loss_pct:.0f}%"
        reason = (
            f"Daily loss limit reached ({risk.daily_loss:+.2f} / "
            f"{risk.daily_loss_pct:+.2f}% vs {cap})"
        )
        add("WARN", reason)
        add("CLOSE_ALL", reason)
        if not lock.locked:
            add("LOCK", reason)

    if risk.daily_trade_limit_hit:
        reason = f"Daily trade limit reached ({risk.trades_today}/{risk.max_trades_per_day})"
        add("WARN", reason)
        if not lock.locked:
            add("LOCK", reason)

    if risk.consecutive_loss_limit_hit:
        reason = (
            f"Consecutive loss limit reached "
            f"({risk.consecutive_losses}/{risk.max_consecutive_losses})"
        )
        add("WARN", reason)
        if not lock.locked:
            add("LOCK", reason)

    if risk.exposure_limit_hit:
        add(
            "WARN",
            f"Exposure limit reached ({risk.exposure_pct:.2f}% "
            f"vs {risk.max_account_exposure_pct:.0f}%)",
        )

    # Enforce the lock: a locked account must be FLAT. If it's locked (now or
    # already) and still has open positions, close them — this is what blocks
    # trading-while-locked (closes trades opened after the lock too).
    will_be_locked = (
        lock.locked
        or risk.daily_loss_limit_hit
        or risk.daily_trade_limit_hit
        or risk.consecutive_loss_limit_hit
    )
    if will_be_locked and has_open_positions and not any(a.type == "CLOSE_ALL" for a in actions):
        add("CLOSE_ALL", "Account locked — closing open position(s)")

    return actions


# In-memory dedup so the background loop doesn't re-alert the same breach every
# tick. Keyed by (UTC date, action signature); resets implicitly each new day.
_alerted: set[tuple[str, str]] = set()


def _already_alerted(signature: str) -> bool:
    key = (datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"), signature)
    if key in _alerted:
        return True
    _alerted.add(key)
    return False


def reset_alert_dedup() -> None:
    _alerted.clear()


async def _execute(action: EnforcementAction, status: StatusResponse) -> EnforcementAction:
    live = settings.is_live_enforcement

    if action.type == "WARN":
        # Dedup warnings; always "executed" in the sense that we handled it.
        if _already_alerted(f"WARN:{action.reason}"):
            return action.model_copy(update={"executed": False, "detail": "deduped"})
        sent = await telegram_service.send_message(
            telegram_service.format_risk_alert(status)
        )
        return action.model_copy(update={"executed": sent, "detail": "alert sent" if sent else "telegram off"})

    if action.type == "LOCK":
        lock_service.lock(action.reason, daily=True)
        return action.model_copy(update={"executed": True, "detail": "trading locked (daily)"})

    if action.type == "CLOSE_ALL":
        if not live:
            n = len(status.open_positions)
            return action.model_copy(
                update={"executed": False, "detail": f"dry_run: would close {n} position(s)"}
            )
        try:
            results = mt5_service.close_all_positions()
            return action.model_copy(
                update={"executed": True, "detail": "; ".join(results) or "no positions"}
            )
        except MT5Error as exc:
            return action.model_copy(update={"executed": False, "detail": f"error: {exc}"})

    return action.model_copy(update={"executed": False, "detail": "unknown action"})


async def run_cycle() -> EnforcementResult:
    """Build status, decide actions, execute per mode, return the result."""
    status = build_status()
    lock = lock_service.get_lock()
    planned = decide_actions(
        status.risk, lock, has_open_positions=bool(status.open_positions)
    )

    executed: list[EnforcementAction] = []
    for action in planned:
        executed.append(await _execute(action, status))

    if planned:
        log.info(
            "Enforcement cycle (mode=%s): %s",
            settings.enforcement_mode,
            ", ".join(f"{a.type}[{'x' if a.executed else '-'}]" for a in executed),
        )

    return EnforcementResult(
        mode=settings.enforcement_mode,
        any_limit_hit=status.risk.any_limit_hit,
        actions=executed,
        generated_at=datetime.now(tz=timezone.utc),
    )
