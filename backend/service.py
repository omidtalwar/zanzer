"""All-in-one SaaS service — runs the Telegram bot AND the worker supervisor
in a single always-on process.

Start ONCE (on your VPS) and everything is automatic:
  - the bot handles /start, /link, /subscribe, etc. from users
  - the supervisor launches a worker process for every active account, and
    relaunches any that die — no manual steps per user

    python -m backend.service

On a server, run this under a process manager so it survives reboots:
  - Linux: a systemd unit
  - Windows: NSSM (Non-Sucking Service Manager) or Task Scheduler

NOTE: run this OR the personal `uvicorn backend.main:app` — not both against the
same account, or they'd both enforce on it.
"""
from __future__ import annotations

import asyncio

import uvicorn

from backend import channel, expiry, recommendations
from backend.admin_app import app as admin_app
from backend.bot.app import run as run_bot
from backend.config import settings
from backend.db.session import init_db
from backend.logging_config import get_logger, setup_logging
from backend.payments.flow import run_poller as run_payment_poller
from backend.supervisor import Supervisor

log = get_logger("service")


async def main() -> None:
    setup_logging()
    await init_db()
    log.info(
        "Zanzer service starting (enforcement=%s). Bot + Supervisor + Dashboard(:%s).",
        settings.enforcement_mode, settings.dashboard_port,
    )
    supervisor = Supervisor()
    dashboard = uvicorn.Server(uvicorn.Config(
        admin_app, host="0.0.0.0", port=settings.dashboard_port, log_level="warning",
    ))
    # Run bot + supervisor + expiry notifier + admin dashboard forever.
    await asyncio.gather(
        run_bot(),
        supervisor.run_forever(),
        expiry.run_forever(),
        run_payment_poller(),
        channel.run_forever(),
        recommendations.run_forever(),
        dashboard.serve(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
