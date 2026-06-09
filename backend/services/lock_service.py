"""File-based lock state (no DB until V3).

The lock represents "trading is disabled". In V2 the Python engine sets/clears
it and reports it; the MT5 EA (later) will read it to actually reject new orders.

Two kinds of lock:
- daily lock: tied to a UTC date; auto-clears when the day rolls over.
- manual lock: day=None; persists until /unlock is called.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from backend.config import settings
from backend.logging_config import get_logger
from backend.models import LockState

log = get_logger("lock")

_PATH = Path(settings.lock_state_path)


def _today() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def get_lock() -> LockState:
    """Read lock state. A daily lock from a previous day is treated as cleared."""
    if not _PATH.exists():
        return LockState()
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        state = LockState.model_validate(data)
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Corrupt lock file %s (%s); treating as unlocked", _PATH, exc)
        return LockState()

    # Auto-expire a daily lock once the UTC day has changed.
    if state.locked and state.day is not None and state.day != _today():
        log.info("Daily lock from %s expired; auto-clearing", state.day)
        clear_lock()
        return LockState()
    return state


def _write(state: LockState) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(state.model_dump_json(indent=2), encoding="utf-8")


def lock(reason: str, *, daily: bool = True) -> LockState:
    """Engage the lock. `daily=True` ties it to today (auto-clears tomorrow);
    `daily=False` is a manual lock that persists until cleared."""
    state = LockState(
        locked=True,
        reason=reason,
        locked_at=datetime.now(tz=timezone.utc),
        day=_today() if daily else None,
    )
    _write(state)
    log.warning("LOCK engaged: %s (daily=%s)", reason, daily)
    return state


def clear_lock() -> LockState:
    state = LockState()
    _write(state)
    log.info("LOCK cleared")
    return state
