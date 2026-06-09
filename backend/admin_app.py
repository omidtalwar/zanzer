"""DB-only FastAPI app for the admin dashboard + management API.

Deliberately has NO MetaTrader5 connection and NO risk scheduler, so it can run
safely alongside the SaaS service (which owns the MT5 workers). Hosted by the
all-in-one service on settings.dashboard_port; serves /dashboard and /admin/*,
/users/*.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api.admin_routes import router as admin_router
from backend.api.dashboard_routes import router as dashboard_router
from backend.api.user_routes import router as user_router
from backend.db.session import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Zanzer Admin", version="0.4.0", lifespan=lifespan)
app.include_router(dashboard_router)
app.include_router(admin_router)
app.include_router(user_router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
