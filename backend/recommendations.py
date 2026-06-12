"""AI recommendation digest — sends each active user a personalised, data-driven
recommendation twice a day (configurable hours), based on their OWN journal data.

Privacy: each user only ever receives their own recommendation. No signals, no
advice on what to trade — only behavioural recommendations from their patterns
(see hermes_service.RECO_SYSTEM).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from backend import repositories as repo
from backend.bot import client as bot_client
from backend.config import settings
from backend.db.session import SessionLocal
from backend.logging_config import get_logger
from backend.services import hermes_service

log = get_logger("reco")


def _reco_hours() -> list[int]:
    out = []
    for part in (settings.ai_reco_hours_utc or "").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 23:
            out.append(int(part))
    return out


async def build_for_user(session, user) -> str | None:
    """Build the recommendation text for one user, or None if not enough data."""
    ai_config = await repo.get_ai_config(session)
    if not ai_config["available"]:
        return None
    until = datetime.now(tz=timezone.utc)
    since = until - timedelta(days=settings.ai_reco_lookback_days)
    trades = await repo.get_trades_in_range(session, user.id, since=since, until=until)
    journals = await repo.get_journals_in_range(session, user.id, since=since, until=until)
    closed = [t for t in trades if t.profit is not None]
    if len(closed) < 1 and not journals:
        return None  # nothing to recommend on yet

    trade_dicts = [
        {"symbol": t.symbol, "profit": t.profit, "session": t.session,
         "exit_reason": t.exit_reason, "entry_timeframe": t.entry_timeframe,
         "duration_s": t.duration_s, "status": t.status}
        for t in trades
    ]
    journal_dicts = [
        {"type": j.type, "setup_reason": j.setup_reason, "mistakes": j.mistakes,
         "lesson": j.lesson, "skipped": j.skipped}
        for j in journals
    ]
    context = hermes_service.build_reco_context(
        trade_dicts, journal_dicts,
        period_label=f"Last {settings.ai_reco_lookback_days} days",
    )
    review = await hermes_service.generate_recommendation(context, ai_config)
    return (
        "🤖 <b>Your ZanZer recommendation</b>\n\n" + review +
        "\n\n<i>Based on your own journal — not financial advice, never a signal.</i>"
    )


async def send_batch() -> int:
    """Send a recommendation to every active subscriber with data. Returns count sent."""
    sent = 0
    async with SessionLocal() as session:
        users = await repo.list_users(session)
    for u in users:
        async with SessionLocal() as session:
            full = await repo.get_user(session, u.telegram_id)
            if full is None or not repo.subscription_is_active(full.subscription):
                continue
            try:
                text = await build_for_user(session, full)
            except Exception as exc:  # noqa: BLE001
                log.warning("reco build failed for %s: %s", u.telegram_id, exc)
                text = None
        if text:
            try:
                await bot_client.send_message(u.telegram_id, text)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("reco send failed for %s: %s", u.telegram_id, exc)
        await asyncio.sleep(0.1)  # stay under Telegram limits
    return sent


async def run_forever(interval: int = 300) -> None:
    hours = _reco_hours()
    if not hours:
        log.info("AI recommendations disabled (AI_RECO_HOURS_UTC empty).")
        return
    log.info("Recommendation digest started (UTC hours=%s).", hours)
    while True:
        try:
            now = datetime.now(tz=timezone.utc)
            if now.hour in hours:
                key = f"reco_sent_{now.hour}"
                async with SessionLocal() as session:
                    last = await repo.get_app_setting(session, key)
                if last != now.strftime("%Y-%m-%d"):
                    count = await send_batch()
                    async with SessionLocal() as session:
                        await repo.set_app_setting(session, key, now.strftime("%Y-%m-%d"))
                    log.info("Sent %s recommendation(s) for the %02d:00 UTC slot", count, now.hour)
        except Exception as exc:  # noqa: BLE001
            log.error("reco loop error: %s", exc)
        await asyncio.sleep(interval)
