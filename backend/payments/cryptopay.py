"""Minimal CryptoPay (@CryptoBot) API client.

Docs: https://help.crypt.bot/crypto-pay-api
Auth: header `Crypto-Pay-API-Token`. We use mainnet or testnet per config.
"""
from __future__ import annotations

import httpx

from backend.config import settings
from backend.logging_config import get_logger

log = get_logger("cryptopay")


def _base() -> str:
    return "https://testnet-pay.crypt.bot/api" if settings.cryptopay_testnet else "https://pay.crypt.bot/api"


class CryptoPayError(RuntimeError):
    pass


async def _call(method: str, params: dict | None = None) -> dict:
    if not settings.cryptopay_token:
        raise CryptoPayError("CRYPTOPAY_TOKEN not set")
    headers = {"Crypto-Pay-API-Token": settings.cryptopay_token}
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(f"{_base()}/{method}", json=params or {}, headers=headers)
    data = resp.json()
    if not data.get("ok"):
        raise CryptoPayError(f"{method} failed: {data.get('error')}")
    return data["result"]


async def get_me() -> dict:
    return await _call("getMe")


async def create_invoice(asset: str, amount: float, description: str, payload: str,
                         expires_in: int = 3600) -> dict:
    return await _call("createInvoice", {
        "asset": asset,
        "amount": str(amount),
        "description": description,
        "payload": payload,
        "expires_in": expires_in,
    })


async def get_invoices(invoice_ids: list[int | str]) -> list[dict]:
    if not invoice_ids:
        return []
    result = await _call("getInvoices", {
        "invoice_ids": ",".join(str(i) for i in invoice_ids),
    })
    # result is {"items": [...]}
    return result.get("items", []) if isinstance(result, dict) else result
