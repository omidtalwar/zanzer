"""Low-level Telegram Bot API client (httpx long-polling).

No third-party bot framework — just the raw API, which keeps dependencies to
httpx (already used) and avoids Python-3.14 wheel issues.
"""
from __future__ import annotations

import httpx

from backend.config import settings
from backend.logging_config import get_logger

log = get_logger("bot.client")
_API = "https://api.telegram.org"


def _base() -> str:
    return f"{_API}/bot{settings.telegram_bot_token}"


async def get_updates(offset: int | None, timeout: int = 25) -> list[dict]:
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    # client timeout must exceed the long-poll timeout
    async with httpx.AsyncClient(timeout=timeout + 10) as client:
        resp = await client.get(f"{_base()}/getUpdates", params=params)
    data = resp.json()
    if not data.get("ok"):
        log.error("getUpdates error: %s", data)
        return []
    return data["result"]


async def send_message(chat_id: int | str, text: str, parse_mode: str = "HTML") -> bool:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{_base()}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
        )
    if resp.status_code != 200:
        log.error("sendMessage failed [%s]: %s", resp.status_code, resp.text)
        return False
    return True


async def send_invoice(chat_id: int | str, title: str, description: str, payload: str,
                       prices: list[dict], currency: str = "XTR",
                       provider_token: str = "") -> bool:
    """Send an invoice. For Telegram Stars use currency='XTR' and provider_token=''.
    `prices` is a list of {"label": str, "amount": int} (amount in Stars for XTR)."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{_base()}/sendInvoice", json={
            "chat_id": chat_id, "title": title, "description": description,
            "payload": payload, "currency": currency, "prices": prices,
            "provider_token": provider_token,
        })
    if resp.status_code != 200:
        log.error("sendInvoice failed [%s]: %s", resp.status_code, resp.text)
        return False
    return True


async def answer_pre_checkout_query(pre_checkout_query_id: str, ok: bool = True,
                                    error_message: str | None = None) -> bool:
    body = {"pre_checkout_query_id": pre_checkout_query_id, "ok": ok}
    if error_message:
        body["error_message"] = error_message
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{_base()}/answerPreCheckoutQuery", json=body)
    return resp.status_code == 200


async def delete_message(chat_id: int | str, message_id: int) -> bool:
    """Best-effort delete (used to remove a message containing a password)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{_base()}/deleteMessage",
                json={"chat_id": chat_id, "message_id": message_id},
            )
        return resp.status_code == 200
    except httpx.HTTPError:
        return False
