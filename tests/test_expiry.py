"""Tests for the subscription expiry notice logic (pure decide_notice).

Run with:  python -m tests.test_expiry
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.expiry import decide_notice

NOW = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)


def test_none_when_no_expiry():
    assert decide_notice(None, "", NOW, 3) is None


def test_reminder_within_window():
    exp = NOW + timedelta(days=2)
    assert decide_notice(exp, "", NOW, 3) == "reminded"


def test_no_reminder_outside_window():
    exp = NOW + timedelta(days=10)
    assert decide_notice(exp, "", NOW, 3) is None


def test_no_duplicate_reminder():
    exp = NOW + timedelta(days=2)
    assert decide_notice(exp, "reminded", NOW, 3) is None


def test_expired_notice():
    exp = NOW - timedelta(hours=1)
    assert decide_notice(exp, "reminded", NOW, 3) == "expired"


def test_no_duplicate_expired():
    exp = NOW - timedelta(days=2)
    assert decide_notice(exp, "expired", NOW, 3) is None


def test_naive_datetime_handled():
    exp = (NOW + timedelta(days=1)).replace(tzinfo=None)  # naive (like SQLite)
    assert decide_notice(exp, "", NOW, 3) == "reminded"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} tests passed.")


if __name__ == "__main__":
    _run()
