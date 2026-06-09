"""Subscription payment flow via CryptoPay (auto-confirmed).

- create_subscription_invoice(): wired as the bot's /subscribe invoicer.
- poll_once()/run_poller(): background loop that watches pending invoices and
  auto-activates the subscription when CryptoPay reports them paid.
"""
from __future__ import annotations

import asyncio

from backend import repositories as repo
from backend.bot import client as bot_client
from backend.config import settings
from backend.db.session import SessionLocal
from backend.logging_config import get_logger
from backend.payments import cryptopay

log = get_logger("payments")

PROVIDER = "cryptopay"


def _plan_terms(plan: str) -> tuple[float, int]:
    """Return (amount, days) for a plan."""
    if plan == "quarterly":
        return settings.price_quarterly, 90
    return settings.price_monthly, 30


async def create_subscription_invoice(telegram_id: int, plan: str) -> tuple[bool, str]:
    """Create a CryptoPay invoice for the user; return (ok, pay_url_or_message)."""
    if not settings.cryptopay_token:
        return (False, "Crypto payments aren't configured yet. Please contact the admin.")
    amount, days = _plan_terms(plan)
    async with SessionLocal() as session:
        user = await repo.get_user(session, telegram_id)
        if user is None:
            return (False, "Send /start first.")
        try:
            invoice = await cryptopay.create_invoice(
                asset=settings.cryptopay_asset,
                amount=amount,
                description=f"Zanzer {plan} subscription ({days} days)",
                payload=str(telegram_id),
            )
        except cryptopay.CryptoPayError as exc:
            log.error("create_invoice failed: %s", exc)
            return (False, "Couldn't create the invoice. Try again later.")
        await repo.create_provider_payment(
            session, user, provider=PROVIDER, invoice_id=str(invoice["invoice_id"]),
            amount=amount, currency=settings.cryptopay_asset, plan=plan, days=days,
        )
    url = (invoice.get("bot_invoice_url") or invoice.get("mini_app_invoice_url")
           or invoice.get("pay_url"))
    return (True, url)


async def poll_once(notify=None) -> int:
    """Check pending CryptoPay invoices; activate the ones that are paid."""
    notify = notify or bot_client.send_message
    activated = 0
    async with SessionLocal() as session:
        pending = await repo.list_pending_provider_payments(session, PROVIDER)
        if not pending:
            return 0
        ids = [p.invoice_id for p in pending if p.invoice_id]
        try:
            invoices = await cryptopay.get_invoices(ids)
        except cryptopay.CryptoPayError as exc:
            log.error("getInvoices failed: %s", exc)
            return 0
        status_by_id = {str(i.get("invoice_id")): i.get("status") for i in invoices}
        for p in pending:
            if status_by_id.get(str(p.invoice_id)) != "paid":
                continue
            await repo.set_payment_status(session, p, "verified")
            sub = await repo.activate_subscription(
                session, p.user, p.days or 30, plan=p.plan or "monthly"
            )
            expires = str(sub.expires_at)[:10]
            await notify(
                p.user.telegram_id,
                f"✅ Payment received — your subscription is active until {expires}! /status",
            )
            log.info("activated user %s via paid invoice %s", p.user.telegram_id, p.invoice_id)
            activated += 1
    return activated


async def run_poller(interval_seconds: int = 60) -> None:
    if not settings.cryptopay_token:
        log.info("CryptoPay not configured — payment poller disabled.")
        return
    log.info("CryptoPay poller started (every %ss)", interval_seconds)
    try:
        while True:
            try:
                await poll_once()
            except Exception as exc:  # noqa: BLE001
                log.error("payment poll failed: %s", exc)
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        raise
