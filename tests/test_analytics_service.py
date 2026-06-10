"""Tests for the standard performance formulas. Run with:
    python -m tests.test_analytics_service

No pytest dependency — plain asserts. Numbers are hand-verified against the
international standard formulas (MyFxBook / Tradervue / Van Tharp expectancy).
"""
from __future__ import annotations

from backend.services.analytics_service import compute_metrics, fmt_pf, fmt_rr


def _t(profit, symbol="EURUSD", duration_s=3600):
    return {"profit": profit, "symbol": symbol, "duration_s": duration_s}


def test_empty_returns_none():
    assert compute_metrics([]) is None
    assert compute_metrics([{"profit": None, "symbol": "X", "duration_s": None}]) is None


def test_worked_example():
    # 3 wins (+100, +50, +30), 2 losses (-40, -20).  Total 5 trades.
    trades = [_t(100), _t(50), _t(30), _t(-40), _t(-20)]
    m = compute_metrics(trades)
    assert m is not None

    assert m.total_trades == 5
    assert m.wins == 3
    assert m.losses == 2
    assert m.breakeven == 0

    # Win rate = 3/5 = 60%
    assert m.win_rate == 60.0
    assert m.loss_rate == 40.0

    # Gross profit = 180, gross loss = 60 (positive), net = 120
    assert m.gross_profit == 180.0
    assert m.gross_loss == 60.0
    assert m.net_pnl == 120.0

    # Profit factor = 180 / 60 = 3.0
    assert m.profit_factor == 3.0

    # Avg win = 180/3 = 60, avg loss = 60/2 = 30
    assert m.avg_win == 60.0
    assert m.avg_loss == 30.0

    # Payoff / Avg RR = avg_win / avg_loss = 60/30 = 2.0
    assert m.payoff_ratio == 2.0

    # Expectancy = (0.6 × 60) − (0.4 × 30) = 36 − 12 = 24.0 per trade
    assert m.expectancy == 24.0
    # Expectancy in R = 24 / 30 = 0.8R
    assert m.expectancy_r == 0.8

    assert m.largest_win == 100.0
    assert m.largest_loss == -40.0
    assert m.avg_hold_s == 3600


def test_breakeven_not_counted_as_loss():
    # 1 win, 1 breakeven, 1 loss
    trades = [_t(50), _t(0), _t(-25)]
    m = compute_metrics(trades)
    assert m.wins == 1
    assert m.losses == 1
    assert m.breakeven == 1
    # Win rate = 1/3 (breakeven counts in denominator, not as win/loss)
    assert m.win_rate == 33.3
    # Gross loss only includes the real loss
    assert m.gross_loss == 25.0


def test_no_losses_profit_factor_infinite():
    trades = [_t(10), _t(20)]
    m = compute_metrics(trades)
    assert m.profit_factor is None       # undefined → ∞
    assert fmt_pf(m.profit_factor) == "∞"
    assert m.payoff_ratio is None
    assert fmt_rr(m.payoff_ratio) == "—"
    # Expectancy with no losses = just the avg win
    assert m.expectancy == 15.0


def test_best_worst_symbol():
    trades = [_t(100, "XAUUSD"), _t(-30, "EURUSD"), _t(-10, "EURUSD"), _t(20, "GBPUSD")]
    m = compute_metrics(trades)
    assert m.best_symbol == "XAUUSD"
    assert m.best_symbol_pnl == 100.0
    assert m.worst_symbol == "EURUSD"   # -30 + -10 = -40
    assert m.worst_symbol_pnl == -40.0


def _run():
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} analytics tests passed.")


if __name__ == "__main__":
    _run()
