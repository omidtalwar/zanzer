"""Tests for the risk math. Run with:  python -m tests.test_risk_service

No pytest dependency — plain asserts so it runs with just the venv Python.
These guard the PRD's core invariant: risk limits must be measured correctly.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.models import AccountInfo, HistoryDeal
from backend.services.risk_service import compute_risk_status

_T = datetime(2026, 6, 7, 10, 0, tzinfo=timezone.utc)


def _account(balance=1000.0, equity=1000.0, margin=0.0, profit=0.0) -> AccountInfo:
    return AccountInfo(
        login=1, broker="Test", server="Test", currency="USD",
        balance=balance, equity=equity, margin=margin,
        margin_free=equity - margin, profit=profit,
    )


def _deal(pid, entry, profit=0.0, t=_T) -> HistoryDeal:
    return HistoryDeal(
        ticket=pid, position_id=pid, symbol="EURUSD", direction="BUY",
        entry=entry, volume=0.1, price=1.1, profit=profit, time=t,
    )


def test_no_activity_no_limits():
    r = compute_risk_status(_account(), [])
    assert r.trades_today == 0
    assert r.daily_loss == 0.0
    assert not r.any_limit_hit


def test_trade_count_counts_only_opens():
    deals = [_deal(1, "IN"), _deal(1, "OUT", profit=-5), _deal(2, "IN")]
    r = compute_risk_status(_account(), deals)
    assert r.trades_today == 2          # two opens
    assert r.daily_trade_limit_hit       # default max is 2


def test_daily_loss_realized_plus_floating():
    # realized -30 on a closed position + floating -20 on open positions
    deals = [_deal(1, "IN"), _deal(1, "OUT", profit=-30.0)]
    acct = _account(balance=970.0, equity=950.0, profit=-20.0)  # bal already reflects realized
    r = compute_risk_status(acct, deals)
    assert r.daily_loss == -50.0         # -30 realized + -20 floating
    # start-of-day balance = 970 - (-30) = 1000 -> -50/1000 = -5%
    assert r.daily_loss_pct == -5.0
    assert r.daily_loss_limit_hit        # hits the 5% default


def test_consecutive_losses_streak_from_newest():
    # win, then two losses (newest last) -> streak of 2
    deals = [
        _deal(1, "OUT", profit=10.0, t=datetime(2026, 6, 7, 9, 0, tzinfo=timezone.utc)),
        _deal(2, "OUT", profit=-4.0, t=datetime(2026, 6, 7, 10, 0, tzinfo=timezone.utc)),
        _deal(3, "OUT", profit=-6.0, t=datetime(2026, 6, 7, 11, 0, tzinfo=timezone.utc)),
    ]
    r = compute_risk_status(_account(), deals)
    assert r.consecutive_losses == 2
    assert r.consecutive_loss_limit_hit  # default max is 2


def test_breakeven_scratch_is_not_a_loss():
    # A near-zero close (-$0.40) is a breakeven scratch, not a loss: it ends the
    # streak. Here: two real losses then a BE scratch (newest) -> streak 0.
    deals = [
        _deal(1, "OUT", profit=-5.0, t=datetime(2026, 6, 7, 9, 0, tzinfo=timezone.utc)),
        _deal(2, "OUT", profit=-6.0, t=datetime(2026, 6, 7, 10, 0, tzinfo=timezone.utc)),
        _deal(3, "OUT", profit=-0.40, t=datetime(2026, 6, 7, 11, 0, tzinfo=timezone.utc)),
    ]
    r = compute_risk_status(_account(), deals)
    assert r.consecutive_losses == 0          # BE scratch (newest) ends the run
    assert not r.consecutive_loss_limit_hit


def test_breakeven_between_real_losses():
    # losses on both sides of a BE: streak only counts the trailing real loss.
    deals = [
        _deal(1, "OUT", profit=-5.0, t=datetime(2026, 6, 7, 9, 0, tzinfo=timezone.utc)),
        _deal(2, "OUT", profit=-0.50, t=datetime(2026, 6, 7, 10, 0, tzinfo=timezone.utc)),  # BE
        _deal(3, "OUT", profit=-4.0, t=datetime(2026, 6, 7, 11, 0, tzinfo=timezone.utc)),
    ]
    r = compute_risk_status(_account(), deals)
    assert r.consecutive_losses == 1          # newest is a loss, then BE stops it


def test_partial_closes_collapse_by_position():
    # one position closed in two partial deals, net positive -> not a loss
    deals = [
        _deal(5, "OUT", profit=-2.0),
        _deal(5, "OUT", profit=5.0),
    ]
    r = compute_risk_status(_account(), deals)
    assert r.consecutive_losses == 0


def test_exposure_pct():
    r = compute_risk_status(_account(equity=1000.0, margin=60.0), [])
    assert r.exposure_pct == 6.0
    assert r.exposure_limit_hit          # over the 5% default


def test_dollar_daily_loss_limit():
    from backend.models import RiskLimits
    # $40 loss; % cap disabled (0), $ cap = 30 → breach on the dollar rule.
    limits = RiskLimits(
        max_trades_per_day=2, max_daily_loss_pct=0, max_daily_loss_usd=30,
        max_risk_per_trade_pct=4, max_consecutive_losses=2, max_account_exposure_pct=5,
    )
    deals = [_deal(1, "IN"), _deal(1, "OUT", profit=-40.0)]
    acct = _account(balance=960.0, equity=960.0, profit=0.0)
    r = compute_risk_status(acct, deals, limits)
    assert r.daily_loss == -40.0
    assert r.daily_loss_limit_hit        # $40 >= $30 cap
    assert r.max_daily_loss_usd == 30


def test_disabled_limits_do_not_fire():
    from backend.models import RiskLimits
    # Both daily-loss caps off → never a daily-loss breach.
    limits = RiskLimits(
        max_trades_per_day=2, max_daily_loss_pct=0, max_daily_loss_usd=0,
        max_risk_per_trade_pct=4, max_consecutive_losses=2, max_account_exposure_pct=5,
    )
    deals = [_deal(1, "IN"), _deal(1, "OUT", profit=-500.0)]
    r = compute_risk_status(_account(balance=500.0, equity=500.0), deals, limits)
    assert r.daily_loss_limit_hit is False


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll {len(fns)} tests passed.")


if __name__ == "__main__":
    _run()
