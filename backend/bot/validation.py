"""Run account credential validation in a subprocess and return the result.

Kept separate from the dispatcher so the dispatcher stays unit-testable (tests
inject a fake validator instead of spawning a process).
"""
from __future__ import annotations

import asyncio
import json
import sys

from backend.logging_config import get_logger

log = get_logger("bot.validation")


async def validate_via_subprocess(account_id: int, timeout: float = 30.0) -> tuple[bool, str]:
    """Spawn `python -m backend.validate_account <id>`; return (ok, message)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "backend.validate_account", str(account_id),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("failed to spawn validator: %s", exc)
        return (False, "internal error starting validation")

    try:
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return (False, "validation timed out (is MT5 reachable?)")

    for line in reversed(out.decode(errors="replace").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                return (bool(data.get("ok")), str(data.get("message", "")))
            except json.JSONDecodeError:
                continue
    return (False, "validation produced no result")


async def provision_via_subprocess(account_id: int, timeout: float = 600.0) -> str:
    """Spawn `python -m backend.provisioning <id>`; return its printed result."""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "backend.provisioning", str(account_id),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return "provisioning timed out"
    except Exception as exc:  # noqa: BLE001
        return f"provisioning error: {exc}"
    for line in reversed(out.decode(errors="replace").splitlines()):
        if line.startswith("terminal_path"):
            return line.strip()
    return "provisioning finished (no path returned)"
