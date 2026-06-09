"""Assembles a full StatusResponse from MT5 + risk calculations.

Shared by the HTTP API and (later) the Telegram command center.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.models import StatusResponse
from backend.services.mt5_service import mt5_service
from backend.services.risk_service import compute_risk_status


def build_status() -> StatusResponse:
    account = mt5_service.get_account_info()
    positions = mt5_service.get_open_positions()
    today_deals = mt5_service.get_today_deals()
    risk = compute_risk_status(account, today_deals)
    return StatusResponse(
        account=account,
        open_positions=positions,
        risk=risk,
        generated_at=datetime.now(tz=timezone.utc),
    )
