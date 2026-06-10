"""Marketing channel — posts a daily, ANONYMIZED community summary.

Privacy rule (hard): only aggregate numbers are ever posted. No individual
user's earnings, P&L, emotion score, or identity is ever exposed.

The bot must be an ADMIN of the channel set in MARKETING_CHANNEL_ID. Posting is
disabled (no-op) when that's unset. The daily post fires once per UTC day at
CHANNEL_POST_HOUR_UTC; the last-post date is stored in app_settings so it
survives restarts (no double-posting).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from backend import repositories as repo
from backend.bot import client as bot_client
from backend.config import settings
from backend.db.session import SessionLocal
from backend.logging_config import get_logger

log = get_logger("channel")

_LAST_POST_KEY = "channel_last_post_date"


def format_post(stats: dict) -> str:
    """Build the anonymized community post from aggregate stats."""
    lines = [
        "🛡️ <b>Zanzer — Daily Discipline Report</b>",
        f"<i>{stats['date']}</i>",
        "",
        f"👥 Traders protected today: <b>{stats['protected']}</b>",
        f"📊 Trades monitored: <b>{stats['trades_today']}</b>",
    ]
    if stats["trades_today"]:
        lines.append(f"📓 Trades journaled: <b>{stats['journaled_pct']}%</b>")
    if stats["accounts_locked"]:
        lines.append(f"🔒 Accounts locked before bigger losses: <b>{stats['accounts_locked']}</b>")
    if stats["revenge_blocked"]:
        lines.append(f"🚫 Revenge trades flagged: <b>{stats['revenge_blocked']}</b>")
    if stats["avg_score"] is not None:
        lines.append(f"🧠 Community discipline score: <b>{stats['avg_score']}/100</b>")
    lines += [
        "",
        "Discipline beats prediction. Zanzer guards your capital so you don't "
        "have to fight yourself. 🤝",
        "",
        "👉 Start protecting your account: @Zanzerbot",
    ]
    return "\n".join(lines)


# Anonymized real-time enforcement events. No name, no $ amount — just the rule.
_EVENT_TEXT = {
    "lock_daily_loss": "🔒 A trader just hit their <b>daily loss limit</b> — ZanZer locked their account before it got worse. Discipline &gt; hope.",
    "lock_trade_limit": "🛑 A trader reached their <b>daily trade limit</b>. No more trades today — overtrading stopped.",
    "lock_streak": "🧊 A trader hit a <b>losing streak</b>. ZanZer enforced a cool-off lock to break the tilt.",
    "lock_score": "🧠 A trader's <b>discipline score</b> dropped too low. Trading locked for the day to protect their capital.",
    "revenge": "🚫 <b>Revenge trade</b> detected and flagged. ZanZer doesn't let one bad trade become five.",
}


async def post_event(kind: str) -> bool:
    """Post an anonymized enforcement event to the channel (social proof)."""
    if not settings.marketing_channel_id or not settings.channel_post_events:
        return False
    text = _EVENT_TEXT.get(kind)
    if not text:
        return False
    text += "\n\n🛡️ Protect your account 👉 @Zanzerbot"
    return await bot_client.send_message(settings.marketing_channel_id, text)


async def build_post() -> str:
    async with SessionLocal() as session:
        stats = await repo.community_stats(session)
    return format_post(stats)


async def post_now() -> bool:
    """Post the current community summary immediately (used by /channelnow)."""
    if not settings.marketing_channel_id:
        return False
    text = await build_post()
    return await bot_client.send_message(settings.marketing_channel_id, text)


async def run_forever(interval: int = 600) -> None:
    """Post the daily summary once per UTC day at CHANNEL_POST_HOUR_UTC."""
    if not settings.marketing_channel_id:
        log.info("Marketing channel not configured (MARKETING_CHANNEL_ID unset) — posting disabled.")
        return
    log.info("Channel poster started (daily at %02d:00 UTC -> %s).",
             settings.channel_post_hour_utc, settings.marketing_channel_id)
    while True:
        try:
            now = datetime.now(tz=timezone.utc)
            today = now.strftime("%Y-%m-%d")
            async with SessionLocal() as session:
                last = await repo.get_app_setting(session, _LAST_POST_KEY)
            if now.hour >= settings.channel_post_hour_utc and last != today:
                text = await build_post()
                ok = await bot_client.send_message(settings.marketing_channel_id, text)
                if ok:
                    async with SessionLocal() as session:
                        await repo.set_app_setting(session, _LAST_POST_KEY, today)
                    log.info("Posted daily community summary to %s", settings.marketing_channel_id)
                else:
                    log.warning("Channel post failed (is the bot an admin of the channel?)")
        except Exception as exc:  # noqa: BLE001
            log.error("channel poster error: %s", exc)
        await asyncio.sleep(interval)
