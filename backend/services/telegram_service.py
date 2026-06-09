"""Telegram alerting via the Bot API (outbound only for V1).

V1 sends alerts/notifications. The interactive command center (/status, /lock,
etc.) is Phase 5 — this module just provides the send primitive and message
formatters it will reuse.

If TELEGRAM_* env vars are not set, sends are skipped with a warning so the
rest of the app still runs during development.
"""
from __future__ import annotations

import httpx

from backend.config import settings
from backend.logging_config import get_logger
from backend.models import StatusResponse

log = get_logger("telegram")

_API_BASE = "https://api.telegram.org"


async def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the configured chat. Returns True on success."""
    if not settings.telegram_enabled:
        log.warning("Telegram not configured; skipping message: %s", text[:80])
        return False

    url = f"{_API_BASE}/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            log.error("Telegram send failed [%s]: %s", resp.status_code, resp.text)
            return False
        return True
    except httpx.HTTPError as exc:
        log.error("Telegram send error: %s", exc)
        return False


def format_status(status: StatusResponse) -> str:
    """Build the /status message body (PRD: Balance, Equity, Open Trades,
    Today's PnL, Emotion Score, Risk Status)."""
    a = status.account
    r = status.risk
    risk_flag = "🔴 LIMIT HIT" if r.any_limit_hit else "🟢 OK"
    lines = [
        "<b>📊 Account Status</b>",
        f"Balance: <b>{a.balance:,.2f} {a.currency}</b>",
        f"Equity: <b>{a.equity:,.2f} {a.currency}</b>",
        f"Open Trades: <b>{len(status.open_positions)}</b>",
        f"Today's PnL: <b>{r.daily_loss:,.2f} {a.currency}</b> ({r.daily_loss_pct:+.2f}%)",
        f"Trades today: <b>{r.trades_today}/{r.max_trades_per_day}</b>",
        f"Consecutive losses: <b>{r.consecutive_losses}/{r.max_consecutive_losses}</b>",
        f"Exposure: <b>{r.exposure_pct:.2f}%</b> (max {r.max_account_exposure_pct:.0f}%)",
        f"Risk Status: {risk_flag}",
    ]
    return "\n".join(lines)


def format_risk_alert(status: StatusResponse) -> str:
    """Warning message listing which limits are breached."""
    r = status.risk
    breaches = []
    if r.daily_trade_limit_hit:
        breaches.append(f"• Daily trade limit reached ({r.trades_today}/{r.max_trades_per_day})")
    if r.daily_loss_limit_hit:
        breaches.append(f"• Daily loss limit reached ({r.daily_loss_pct:+.2f}% / -{r.max_daily_loss_pct:.0f}%)")
    if r.consecutive_loss_limit_hit:
        breaches.append(f"• Consecutive loss limit reached ({r.consecutive_losses}/{r.max_consecutive_losses})")
    if r.exposure_limit_hit:
        breaches.append(f"• Exposure limit reached ({r.exposure_pct:.2f}% / {r.max_account_exposure_pct:.0f}%)")
    body = "\n".join(breaches) if breaches else "• (none)"
    return f"<b>⚠️ RISK WARNING</b>\n{body}\n\n<i>Consider stopping for today.</i>"
