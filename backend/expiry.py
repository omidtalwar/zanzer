"""Subscription expiry notices: renewal reminders + expired alerts.

A pure `decide_notice()` picks the notice to send for a subscription; a loop
runs it periodically and messages each user on their own Telegram chat. The
supervisor separately stops workers for inactive subscriptions, so this module
is only about *telling the user*.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from backend.bot import client as bot_client
from backend.config import settings
from backend.db.session import SessionLocal
from backend.logging_config import get_logger
from backend import repositories as repo

log = get_logger("expiry")


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def decide_notice(expires_at, notice_state: str, now: datetime, reminder_days: int) -> str | None:
    """Return "expired", "reminded", or None for this subscription.

    - expired  : past expiry and not yet told.
    - reminded : within `reminder_days` of expiry and not yet reminded/expired.
    """
    if expires_at is None:
        return None
    exp = _aware(expires_at)
    if exp <= now:
        return "expired" if notice_state != "expired" else None
    days_left = (exp - now).total_seconds() / 86400.0
    if 0 < days_left <= reminder_days and notice_state not in ("reminded", "expired"):
        return "reminded"
    return None


def _message(kind: str, expires_at: datetime) -> str:
    day = str(expires_at)[:10]
    if kind == "expired":
        return (
            "⌛ <b>Your subscription has expired.</b>\n"
            "Monitoring of your account is paused. Renew to stay protected:\n"
            "/subscribe"
        )
    return (
        f"⏳ <b>Your subscription ends on {day}.</b>\n"
        "Renew to keep your account protected without interruption:\n/subscribe"
    )


async def check_once(session_factory=SessionLocal, notify=None) -> int:
    """One pass: send due notices. Returns how many were sent."""
    notify = notify or bot_client.send_message
    now = datetime.now(tz=timezone.utc)
    sent = 0
    async with session_factory() as session:
        subs = await repo.list_subscriptions_with_user(session)
        for sub in subs:
            kind = decide_notice(sub.expires_at, sub.notice_state, now, settings.expiry_reminder_days)
            if kind is None:
                continue
            ok = await notify(sub.user.telegram_id, _message(kind, _aware(sub.expires_at)))
            await repo.set_notice_state(session, sub, kind)
            if ok:
                sent += 1
                log.info("sent %s notice to user %s", kind, sub.user.telegram_id)
    return sent


async def run_forever(interval_seconds: int = 3600) -> None:
    log.info("Expiry notifier started (every %ss)", interval_seconds)
    try:
        while True:
            try:
                await check_once()
            except Exception as exc:  # noqa: BLE001
                log.error("expiry check failed: %s", exc)
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        raise
