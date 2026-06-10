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


_DIV = "━━━━━━━━━━━━━━━━━━"

# Rotating one-a-day trading-psychology nuggets (on-brand: discipline, not signals).
TRADING_WISDOM = [
    "The market pays the patient. Overtrading is just impatience — with a fee. 💸",
    "Your stop loss is a seatbelt, not a suggestion. Buckle up before every trade. 🚗",
    "Risk 1% and you survive 100 mistakes. Risk 10% and you survive 10. Math doesn't care about conviction. 🧮",
    "Revenge trading is the market charging interest on your emotions. Walk away. 🔁",
    "A scratch at breakeven is a WIN for your discipline. Not every trade must print. ✂️",
    "The best traders aren't right more often — they just lose smaller. 📉",
    "FOMO is expensive. There is ALWAYS another setup. ⏳",
    "Green months are built by avoiding red disasters, not chasing home runs. 🛡️",
    "Position size kills accounts, not direction. Size like you'll be wrong. ⚖️",
    "Boredom is a position. Sitting on your hands is a skill most never learn. 🧘",
    "You don't rise to your goals — you fall to your rules. Set them when calm. 🎯",
    "Two losses in a row? That's data, not a dare. Step back. 🧊",
    "The hardest trade to take is no trade. Take it anyway. 🚫",
    "Protect capital first. Profits are what's left after you survive. 🏦",
    "Discipline is choosing what you want most over what you want now. 🔒",
]


def _wisdom_for_today() -> str:
    doy = datetime.now(tz=timezone.utc).timetuple().tm_yday
    return TRADING_WISDOM[doy % len(TRADING_WISDOM)]


def format_post(stats: dict) -> str:
    """Build the anonymized community post from aggregate stats."""
    lines = [
        "🛡️ <b>ZanZer Risk Lab — Daily Report</b>",
        f"📅 <i>{stats['date']}</i>",
        _DIV,
        f"👥 Traders protected: <b>{stats['protected']}</b>",
        f"📊 Trades today: <b>{stats['trades_today']}</b>  ·  "
        f"All-time guarded: <b>{stats.get('total_trades', 0):,}</b>",
        f"🎯 Journaling consistency: <b>{stats.get('consistency_pct', 0)}%</b>",
    ]
    if stats["avg_score"] is not None:
        lines.append(f"🧠 Community discipline score: <b>{stats['avg_score']}/100</b>")

    # "Guardian in action" — only show lines that actually happened.
    actions = []
    if stats["accounts_locked"]:
        actions.append(f"🔒 {stats['accounts_locked']} account(s) locked before bigger losses")
    if stats["revenge_blocked"]:
        actions.append(f"🚫 {stats['revenge_blocked']} revenge trade(s) stopped")
    if actions:
        lines.append("")
        lines.append("<b>🛡️ Guardian in action today</b>")
        lines += actions

    lines += [
        _DIV,
        "💡 <b>Trading Wisdom</b>",
        f"<i>{_wisdom_for_today()}</i>",
        _DIV,
        "Discipline beats prediction. 🤝",
        "👉 Protect your account: @Zanzerbot",
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
