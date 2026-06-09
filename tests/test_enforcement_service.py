"""Tests for the enforcement decision policy (pure function, no MT5/Telegram).

Run with:  python -m tests.test_enforcement_service
"""
from __future__ import annotations

from backend.models import LockState, RiskStatus
from backend.services.enforcement_service import decide_actions


def _risk(**over) -> RiskStatus:
    base = dict(
        trades_today=0, max_trades_per_day=2,
        daily_loss=0.0, daily_loss_pct=0.0, max_daily_loss_pct=5.0,
        consecutive_losses=0, max_consecutive_losses=2,
        exposure_pct=0.0, max_account_exposure_pct=5.0,
        daily_trade_limit_hit=False, daily_loss_limit_hit=False,
        consecutive_loss_limit_hit=False, exposure_limit_hit=False,
    )
    base.update(over)
    return RiskStatus(**base)


def _types(actions) -> list[str]:
    return [a.type for a in actions]


def test_no_breach_no_actions():
    assert decide_actions(_risk(), LockState()) == []


def test_daily_loss_warns_closes_and_locks():
    r = _risk(daily_loss=-50, daily_loss_pct=-5.0, daily_loss_limit_hit=True)
    types = _types(decide_actions(r, LockState()))
    assert "WARN" in types
    assert "CLOSE_ALL" in types
    assert "LOCK" in types


def test_daily_loss_does_not_relock_if_already_locked():
    r = _risk(daily_loss=-50, daily_loss_pct=-5.0, daily_loss_limit_hit=True)
    types = _types(decide_actions(r, LockState(locked=True, reason="x")))
    assert "CLOSE_ALL" in types        # still closes
    assert "LOCK" not in types          # but no duplicate lock


def test_trade_limit_warns_and_locks_no_close():
    r = _risk(trades_today=2, daily_trade_limit_hit=True)
    types = _types(decide_actions(r, LockState()))
    assert types.count("WARN") == 1
    assert "LOCK" in types
    assert "CLOSE_ALL" not in types


def test_consecutive_loss_warns_and_locks_no_close():
    r = _risk(consecutive_losses=2, consecutive_loss_limit_hit=True)
    types = _types(decide_actions(r, LockState()))
    assert "LOCK" in types
    assert "CLOSE_ALL" not in types


def test_exposure_only_warns():
    r = _risk(exposure_pct=6.0, exposure_limit_hit=True)
    types = _types(decide_actions(r, LockState()))
    assert types == ["WARN"]


def test_actions_default_not_executed():
    r = _risk(daily_loss_limit_hit=True)
    assert all(a.executed is False for a in decide_actions(r, LockState()))


def test_locked_with_open_positions_closes():
    # Already locked, no fresh breach, but a position is open → flatten it.
    r = _risk()
    actions = decide_actions(r, LockState(locked=True, reason="manual"), has_open_positions=True)
    assert "CLOSE_ALL" in _types(actions)


def test_locked_without_open_positions_no_close():
    r = _risk()
    actions = decide_actions(r, LockState(locked=True, reason="manual"), has_open_positions=False)
    assert "CLOSE_ALL" not in _types(actions)


def test_trade_limit_with_open_positions_closes():
    # Hitting the trade limit while holding a position → lock AND flatten.
    r = _risk(trades_today=3, daily_trade_limit_hit=True)
    actions = decide_actions(r, LockState(), has_open_positions=True)
    assert "LOCK" in _types(actions)
    assert "CLOSE_ALL" in _types(actions)


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} tests passed.")


if __name__ == "__main__":
    _run()
