"""Telegram bot runner — long-polling loop wiring the client to the dispatcher.

Run:  python -m backend.bot.app   (or python -m backend.bot)
"""
from __future__ import annotations

import asyncio

from backend.bot import client
from backend.bot.dispatcher import BotDispatcher
from backend.bot.validation import provision_via_subprocess, validate_via_subprocess
from backend.payments.flow import create_subscription_invoice
from backend.payments.stars import handle_successful_payment, send_star_invoice
from backend.config import settings
from backend.db.session import init_db
from backend.logging_config import get_logger, setup_logging

log = get_logger("bot.app")


async def run() -> None:
    setup_logging()
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set — cannot start the bot.")
    await init_db()

    dispatcher = BotDispatcher(
        send=client.send_message,
        delete=client.delete_message,
        validator=validate_via_subprocess,
        provisioner=provision_via_subprocess,
        invoicer=create_subscription_invoice,
        star_invoicer=send_star_invoice,
    )
    log.info("Bot started (admins=%s). Polling for updates...", settings.admin_ids or "none")

    offset: int | None = None
    while True:
        try:
            updates = await client.get_updates(offset, timeout=25)
        except Exception as exc:  # noqa: BLE001 - keep polling
            log.error("getUpdates failed: %s", exc)
            await asyncio.sleep(3)
            continue

        for upd in updates:
            offset = upd["update_id"] + 1

            # Telegram Stars: must approve the pre-checkout within 10s.
            if "pre_checkout_query" in upd:
                pcq = upd["pre_checkout_query"]
                try:
                    await client.answer_pre_checkout_query(pcq["id"], ok=True)
                except Exception as exc:  # noqa: BLE001
                    log.error("pre_checkout answer failed: %s", exc)
                continue

            message = upd.get("message") or upd.get("edited_message")
            if not message:
                continue
            chat = message.get("chat", {})
            frm = message.get("from", {})
            telegram_id = frm.get("id") or chat.get("id")
            if telegram_id is None:
                continue

            # Telegram Stars: a successful payment arrives as a service message.
            if "successful_payment" in message:
                try:
                    await handle_successful_payment(
                        telegram_id, message["successful_payment"], client.send_message
                    )
                except Exception as exc:  # noqa: BLE001
                    log.error("successful_payment handling failed: %s", exc)
                continue

            username = frm.get("username")
            text = message.get("text", "")
            message_id = message.get("message_id")
            try:
                await dispatcher.handle(
                    telegram_id=telegram_id, username=username,
                    text=text, message_id=message_id,
                )
            except Exception as exc:  # noqa: BLE001 - one bad update shouldn't kill the bot
                log.error("handler error for %s: %s", telegram_id, exc)
                try:
                    await client.send_message(telegram_id, "Something went wrong. Try /help.")
                except Exception:
                    pass


if __name__ == "__main__":
    asyncio.run(run())
