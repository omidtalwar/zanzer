"""Telegram Stars (XTR) payments — fully native, no external provider.

Flow:
  1. send_star_invoice() → sends an XTR invoice (Pay button in chat).
  2. Telegram sends a pre_checkout_query → the app answers ok (in app.py loop).
  3. On payment, Telegram sends message.successful_payment →
     handle_successful_payment() activates the subscription.

The plan/days travel in the invoice payload, so activation needs no DB lookup.
"""
from __future__ import annotations

from backend.bot import client as bot_client
from backend.config import settings
from backend.db.session import SessionLocal
from backend.logging_config import get_logger
from backend import repositories as repo

log = get_logger("stars")

_PREFIX = "stars"


def _plan_terms(plan: str) -> tuple[int, int]:
    """Return (stars, days) for a plan."""
    if plan == "quarterly":
        return settings.price_quarterly_stars, 90
    return settings.price_monthly_stars, 30


async def send_star_invoice(telegram_id: int, plan: str) -> tuple[bool, str]:
    """Send a Stars invoice. Returns (ok, message). Wired as the bot star_invoicer."""
    stars, days = _plan_terms(plan)
    payload = f"{_PREFIX}:{plan}:{days}"
    ok = await bot_client.send_invoice(
        chat_id=telegram_id,
        title=f"Zanzer {plan} subscription",
        description=f"{days} days of account protection.",
        payload=payload,
        prices=[{"label": f"{plan} ({days}d)", "amount": stars}],
        currency="XTR",
        provider_token="",
    )
    if ok:
        return (True, f"⭐ Invoice sent for {stars} Stars — tap Pay above.")
    return (False, "Couldn't create the Stars invoice. Try again later.")


async def handle_successful_payment(telegram_id: int, sp: dict, notify) -> None:
    """Process a Telegram `successful_payment` for a Stars subscription."""
    payload = sp.get("invoice_payload", "")
    parts = payload.split(":")
    if len(parts) < 3 or parts[0] != _PREFIX:
        log.warning("unrecognized successful_payment payload: %s", payload)
        return
    plan, days = parts[1], int(parts[2])
    stars = sp.get("total_amount")  # in XTR
    async with SessionLocal() as session:
        user = await repo.get_user(session, telegram_id)
        if user is None:
            user = await repo.register_user(session, telegram_id, None)
        # Record the payment, then activate.
        await repo.create_provider_payment(
            session, user, provider="stars", invoice_id=sp.get("telegram_payment_charge_id", ""),
            amount=float(stars or 0), currency="XTR", plan=plan, days=days,
        )
        # mark it verified immediately (Stars payments are final on success)
        pend = await repo.list_pending_provider_payments(session, "stars")
        for p in pend:
            if p.user_id == user.id:
                await repo.set_payment_status(session, p, "verified")
        sub = await repo.activate_subscription(session, user, days, plan=plan)
        expires = str(sub.expires_at)[:10]
    await notify(telegram_id, f"⭐ Payment received — subscription active until {expires}! /status")
    log.info("activated user %s via Stars (%s)", telegram_id, payload)
