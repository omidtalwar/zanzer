"""HTTP routes.

V1 routes are read-only. V2 adds enforcement routes: lock state management and
a manual enforcement trigger. The only route that can touch the broker is
/enforce, and only when ENFORCEMENT_MODE=live.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.config import settings
from backend.logging_config import get_logger
from backend.models import (
    AccountInfo,
    EnforcementResult,
    LockState,
    OpenPosition,
    StatusResponse,
)
from backend.services import enforcement_service, lock_service, telegram_service
from backend.services.mt5_service import MT5Error, mt5_service
from backend.services.status_service import build_status

log = get_logger("api")
router = APIRouter()


def _guard(fn):
    try:
        return fn()
    except MT5Error as exc:
        log.error("MT5 error: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/account", response_model=AccountInfo)
def get_account() -> AccountInfo:
    return _guard(mt5_service.get_account_info)


@router.get("/positions", response_model=list[OpenPosition])
def get_positions() -> list[OpenPosition]:
    return _guard(mt5_service.get_open_positions)


@router.get("/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    return _guard(build_status)


@router.post("/alerts/status")
async def push_status_to_telegram() -> dict:
    """Compute status and push it to Telegram (manual trigger / for testing)."""
    status = _guard(build_status)
    sent = await telegram_service.send_message(telegram_service.format_status(status))
    return {"sent": sent}


@router.post("/alerts/check")
async def check_and_alert() -> dict:
    """Check risk limits; if any are breached, send a Telegram warning."""
    status = _guard(build_status)
    if status.risk.any_limit_hit:
        sent = await telegram_service.send_message(
            telegram_service.format_risk_alert(status)
        )
        return {"limit_hit": True, "alert_sent": sent}
    return {"limit_hit": False, "alert_sent": False}


# --- V2: enforcement ---------------------------------------------------------

@router.get("/lock", response_model=LockState)
def get_lock() -> LockState:
    return lock_service.get_lock()


@router.post("/lock", response_model=LockState)
def set_lock(reason: str = "manual lock") -> LockState:
    """Manually lock trading (persists until /unlock; not auto-cleared daily)."""
    return lock_service.lock(reason, daily=False)


@router.post("/unlock", response_model=LockState)
def clear_lock() -> LockState:
    return lock_service.clear_lock()


@router.post("/enforce", response_model=EnforcementResult)
async def enforce_now() -> EnforcementResult:
    """Run one enforcement cycle immediately (same logic as the background loop).
    In dry_run this only detects/alerts/logs; in live mode it may close positions."""
    try:
        return await enforcement_service.run_cycle()
    except MT5Error as exc:
        log.error("MT5 error during enforcement: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/enforcement-mode")
def enforcement_mode() -> dict:
    return {"mode": settings.enforcement_mode, "live": settings.is_live_enforcement}
