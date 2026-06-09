"""Zanzer — AI Trading Guardian. FastAPI application.

Run (from project root, with venv active):
    uvicorn backend.main:app --reload

V1: read MT5 account/trades/history, compute daily loss & trade count, expose a
    read-only API, send Telegram alerts.
V2: Risk Enforcement Engine — a background loop checks risk every N seconds and
    warns / locks / (in live mode) closes positions when limits are breached.
    Default ENFORCEMENT_MODE=dry_run takes no real trading action.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend import scheduler
from backend.api.admin_routes import router as admin_router
from backend.api.routes import router
from backend.api.user_routes import router as user_router
from backend.config import settings
from backend.db.session import init_db
from backend.logging_config import get_logger, setup_logging
from backend.services.mt5_service import MT5Error, mt5_service

setup_logging()
log = get_logger("app")

_scheduler_task: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "Starting Zanzer (env=%s, enforcement=%s)",
        settings.app_env,
        settings.enforcement_mode,
    )
    await init_db()
    log.info("Database ready (%s)", settings.database_url.split("://", 1)[0])
    try:
        mt5_service.connect()
    except MT5Error as exc:
        # Don't crash on startup — the API stays up and reports 503 on MT5 calls
        # until the terminal is available. Useful while MT5 isn't running yet.
        log.warning("MT5 not connected at startup: %s", exc)
    if not settings.telegram_enabled:
        log.warning("Telegram not configured — alerts will be skipped.")
    if settings.is_live_enforcement:
        log.warning("ENFORCEMENT MODE IS LIVE — the engine will close positions for real.")

    scheduler.start(_scheduler_task)
    yield
    await scheduler.stop(_scheduler_task)
    mt5_service.shutdown()
    log.info("Zanzer stopped")


app = FastAPI(title="Zanzer — AI Trading Guardian", version="0.3.0", lifespan=lifespan)
app.include_router(router)
app.include_router(user_router)
app.include_router(admin_router)


@app.get("/")
def root() -> dict:
    return {
        "app": "Zanzer — AI Trading Guardian",
        "version": "0.3.0",
        "phase": "Phase A (multi-user)",
        "enforcement_mode": settings.enforcement_mode,
    }
