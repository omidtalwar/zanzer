"""Background risk-check loop.

Runs `enforcement_service.run_cycle()` every N seconds (config). Started and
stopped by the FastAPI lifespan. Disabled when interval <= 0.

The loop is defensive: an exception in one cycle (e.g. MT5 briefly
unavailable) is logged and the loop continues, rather than dying silently.
"""
from __future__ import annotations

import asyncio

from backend.config import settings
from backend.logging_config import get_logger
from backend.services import enforcement_service

log = get_logger("scheduler")


async def _loop() -> None:
    interval = settings.risk_check_interval_seconds
    log.info("Risk-check loop started (every %ss, mode=%s)", interval, settings.enforcement_mode)
    try:
        while True:
            try:
                await enforcement_service.run_cycle()
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                log.error("Risk-check cycle failed: %s", exc)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        log.info("Risk-check loop stopped")
        raise


def start(loop_task_holder: dict) -> None:
    """Start the loop unless disabled; store the task in `loop_task_holder`."""
    if settings.risk_check_interval_seconds <= 0:
        log.info("Risk-check loop disabled (interval <= 0)")
        return
    loop_task_holder["task"] = asyncio.create_task(_loop())


async def stop(loop_task_holder: dict) -> None:
    task = loop_task_holder.get("task")
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
