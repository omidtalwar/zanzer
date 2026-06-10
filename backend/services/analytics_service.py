"""Performance analytics — internationally-standard trading metric formulas.

Pure functions only (no DB / Telegram / MT5), so the metrics are testable and
identical everywhere they're shown (/today, /weekly, /performance).

The formulas below are the same definitions used by MyFxBook, FX Blue,
Tradervue, Edgewonk and TradingView's strategy tester. Each is documented with
its standard formula so the numbers are auditable and match what traders see in
other industry tools.

Trade classification (standard):
  win        : profit > 0
  loss       : profit < 0
  breakeven  : profit == 0   (a.k.a. "scratch" / "draw" — NOT counted as a loss)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PerformanceMetrics:
    total_trades: int          # closed trades only
    wins: int
    losses: int
    breakeven: int

    win_rate: float            # wins / total × 100
    loss_rate: float           # losses / total × 100

    gross_profit: float        # Σ winning profits
    gross_loss: float          # |Σ losing profits|  (positive number)
    net_pnl: float             # gross_profit − gross_loss

    profit_factor: float | None   # gross_profit / gross_loss  (None = no losses → ∞)
    avg_win: float             # gross_profit / wins
    avg_loss: float            # gross_loss / losses  (positive number)
    payoff_ratio: float | None    # avg_win / avg_loss  (a.k.a. "Average RR" / Win/Loss ratio)
    expectancy: float          # (win% × avg_win) − (loss% × avg_loss)  → expected $ per trade
    expectancy_r: float | None    # expectancy / avg_loss  → expected R-multiple per trade

    largest_win: float
    largest_loss: float        # negative or 0
    avg_hold_s: int | None     # mean trade duration in seconds

    best_symbol: str | None
    best_symbol_pnl: float
    worst_symbol: str | None
    worst_symbol_pnl: float


def compute_metrics(trades: list[dict]) -> PerformanceMetrics | None:
    """Compute standard performance metrics from closed trades.

    `trades` is a list of dicts with keys: ``profit`` (float), ``symbol`` (str),
    ``duration_s`` (int|None). Only trades with a non-None profit are counted.
    Returns None if there are no closed trades.
    """
    closed = [t for t in trades if t.get("profit") is not None]
    if not closed:
        return None

    wins = [t for t in closed if t["profit"] > 0]
    losses = [t for t in closed if t["profit"] < 0]
    breakeven = [t for t in closed if t["profit"] == 0]

    total = len(closed)
    n_wins = len(wins)
    n_losses = len(losses)

    gross_profit = round(sum(t["profit"] for t in wins), 2)
    gross_loss = round(abs(sum(t["profit"] for t in losses)), 2)  # positive
    net_pnl = round(gross_profit - gross_loss, 2)

    # Win Rate = winning trades / total trades × 100  (breakeven counts in denominator).
    win_rate = round(n_wins / total * 100, 1)
    loss_rate = round(n_losses / total * 100, 1)

    # Profit Factor = Gross Profit / Gross Loss.  No losses → undefined (∞).
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None

    # Average win / average loss (avg_loss reported as a positive number).
    avg_win = round(gross_profit / n_wins, 2) if n_wins else 0.0
    avg_loss = round(gross_loss / n_losses, 2) if n_losses else 0.0

    # Payoff Ratio (a.k.a. "Average RR" / Win-Loss ratio) = Avg Win / Avg Loss.
    payoff_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else None

    # Expectancy (Van Tharp) = (Win% × Avg Win) − (Loss% × Avg Loss).
    # This is the expected profit/loss per trade in account currency.
    win_p = n_wins / total
    loss_p = n_losses / total
    expectancy = round((win_p * avg_win) - (loss_p * avg_loss), 2)

    # Expectancy in R-multiples = expectancy / avg_loss (risk unit).
    expectancy_r = round(expectancy / avg_loss, 2) if avg_loss > 0 else None

    largest_win = round(max((t["profit"] for t in closed), default=0.0), 2)
    largest_loss = round(min((t["profit"] for t in closed), default=0.0), 2)

    durations = [t["duration_s"] for t in closed if t.get("duration_s")]
    avg_hold_s = int(sum(durations) / len(durations)) if durations else None

    # Best / worst symbol by net profit.
    by_symbol: dict[str, float] = {}
    for t in closed:
        by_symbol[t["symbol"]] = round(by_symbol.get(t["symbol"], 0.0) + t["profit"], 2)
    best_symbol = max(by_symbol, key=by_symbol.get) if by_symbol else None
    worst_symbol = min(by_symbol, key=by_symbol.get) if by_symbol else None

    return PerformanceMetrics(
        total_trades=total, wins=n_wins, losses=n_losses, breakeven=len(breakeven),
        win_rate=win_rate, loss_rate=loss_rate,
        gross_profit=gross_profit, gross_loss=gross_loss, net_pnl=net_pnl,
        profit_factor=profit_factor, avg_win=avg_win, avg_loss=avg_loss,
        payoff_ratio=payoff_ratio, expectancy=expectancy, expectancy_r=expectancy_r,
        largest_win=largest_win, largest_loss=largest_loss, avg_hold_s=avg_hold_s,
        best_symbol=best_symbol, best_symbol_pnl=by_symbol.get(best_symbol, 0.0) if best_symbol else 0.0,
        worst_symbol=worst_symbol, worst_symbol_pnl=by_symbol.get(worst_symbol, 0.0) if worst_symbol else 0.0,
    )


def fmt_pf(pf: float | None) -> str:
    """Profit factor display — ∞ when there are no losses (standard convention)."""
    return "∞" if pf is None else f"{pf:.2f}"


def fmt_rr(rr: float | None) -> str:
    return "—" if rr is None else f"{rr:.2f}"
