"""Risk calculations for V1 (read-only / advisory).

V1 only *measures* risk and reports it. It does NOT block, close, or lock —
those enforcement actions belong to the V2 Risk Engine. Keeping the math here
means V2 can reuse it.

Definitions used:
- trades_today      : number of positions OPENED today (DEAL_ENTRY_IN deals).
- realized_today    : sum of profit on today's CLOSING deals (DEAL_ENTRY_OUT).
- floating          : current floating P/L of open positions (account.profit).
- daily_loss        : realized_today + floating (negative = a loss).
- start_of_day_bal  : balance at midnight ≈ current_balance − realized_today.
- daily_loss_pct    : daily_loss / start_of_day_bal * 100 (sign preserved).
- exposure_pct      : used margin / equity * 100.
- consecutive_losses: trailing run of losing closed deals (most recent first).
"""
from __future__ import annotations

from backend.models import AccountInfo, HistoryDeal, RiskLimits, RiskStatus


def _count_trades_opened(deals: list[HistoryDeal]) -> int:
    return sum(1 for d in deals if d.entry == "IN")


def _realized_pnl(deals: list[HistoryDeal]) -> float:
    # Closing deals carry the realized profit; opening deals are 0.
    return sum(d.profit for d in deals if d.entry in ("OUT", "INOUT"))


def _consecutive_losses(deals: list[HistoryDeal]) -> int:
    """Trailing streak of losing closed trades, newest first.

    One position can close in multiple partial deals; we collapse by
    position_id and use the net profit of each closed position.
    """
    closed: dict[int, float] = {}
    order: list[int] = []
    for d in deals:
        if d.entry not in ("OUT", "INOUT"):
            continue
        if d.position_id not in closed:
            order.append(d.position_id)
        closed[d.position_id] = closed.get(d.position_id, 0.0) + d.profit

    streak = 0
    # `order` is chronological (deals come time-ascending); walk newest -> oldest.
    for pid in reversed(order):
        if closed[pid] < 0:
            streak += 1
        else:
            break
    return streak


def compute_risk_status(
    account: AccountInfo,
    today_deals: list[HistoryDeal],
    limits: RiskLimits | None = None,
) -> RiskStatus:
    """Compute the risk snapshot against `limits`.

    `limits` defaults to the global settings (personal/single-user path). The
    SaaS workers pass per-user limits built from each user's RiskSettings row.
    """
    if limits is None:
        limits = RiskLimits.from_settings()

    trades_today = _count_trades_opened(today_deals)
    realized = _realized_pnl(today_deals)
    floating = account.profit
    daily_loss = realized + floating

    start_of_day_balance = account.balance - realized
    if start_of_day_balance > 0:
        daily_loss_pct = daily_loss / start_of_day_balance * 100.0
    else:
        daily_loss_pct = 0.0

    exposure_pct = (account.margin / account.equity * 100.0) if account.equity > 0 else 0.0
    consecutive = _consecutive_losses(today_deals)

    # Daily loss limit: breached if EITHER the % cap or the fixed $ cap is
    # exceeded. A cap of 0 means "disabled", so a user can choose % or $ (or both).
    losing = daily_loss < 0
    pct_hit = losing and limits.max_daily_loss_pct > 0 and abs(daily_loss_pct) >= limits.max_daily_loss_pct
    usd_hit = losing and limits.max_daily_loss_usd > 0 and abs(daily_loss) >= limits.max_daily_loss_usd
    daily_loss_limit_hit = pct_hit or usd_hit

    return RiskStatus(
        trades_today=trades_today,
        max_trades_per_day=limits.max_trades_per_day,
        daily_loss=round(daily_loss, 2),
        daily_loss_pct=round(daily_loss_pct, 2),
        max_daily_loss_pct=limits.max_daily_loss_pct,
        max_daily_loss_usd=limits.max_daily_loss_usd,
        consecutive_losses=consecutive,
        max_consecutive_losses=limits.max_consecutive_losses,
        exposure_pct=round(exposure_pct, 2),
        max_account_exposure_pct=limits.max_account_exposure_pct,
        daily_trade_limit_hit=trades_today >= limits.max_trades_per_day,
        daily_loss_limit_hit=daily_loss_limit_hit,
        consecutive_loss_limit_hit=consecutive >= limits.max_consecutive_losses,
        exposure_limit_hit=exposure_pct >= limits.max_account_exposure_pct,
    )
