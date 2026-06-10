"""Monthly analytics charts (PRD: Equity Curve, Drawdown, Emotion Score Trend).

Renders PNG bytes with matplotlib's headless 'Agg' backend (no display needed —
safe on a Windows/Linux VPS). Pure functions: take plain numbers, return bytes.

The equity curve is built from cumulative realised P&L of closed trades
(starting balance + running total), which is the standard way trading journals
plot an equity curve from a trade list. Drawdown is the standard peak-to-trough
of that equity curve.
"""
from __future__ import annotations

import io

import matplotlib
matplotlib.use("Agg")  # headless backend — must be set before pyplot import
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

_BG = "#0e1117"
_FG = "#e6e6e6"
_GRID = "#2a2f3a"
_GREEN = "#26a69a"
_RED = "#ef5350"
_BLUE = "#42a5f5"


def _style(ax) -> None:
    ax.set_facecolor(_BG)
    ax.tick_params(colors=_FG, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(_GRID)
    ax.grid(True, color=_GRID, linewidth=0.5, alpha=0.6)
    ax.title.set_color(_FG)
    ax.yaxis.label.set_color(_FG)
    ax.xaxis.label.set_color(_FG)


def _save(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=_BG)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def equity_curve_png(
    points: list[tuple[object, float]], starting_balance: float, currency: str = ""
) -> bytes:
    """points: list of (datetime, cumulative_pnl) in chronological order."""
    fig, ax = plt.subplots(figsize=(8, 4), facecolor=_BG)
    _style(ax)
    if points:
        xs = [p[0] for p in points]
        ys = [starting_balance + p[1] for p in points]
        color = _GREEN if ys[-1] >= starting_balance else _RED
        ax.plot(xs, ys, color=color, linewidth=2)
        ax.fill_between(xs, starting_balance, ys, color=color, alpha=0.12)
        ax.axhline(starting_balance, color=_FG, linewidth=0.8, linestyle="--", alpha=0.4)
        if hasattr(xs[0], "strftime"):
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
            fig.autofmt_xdate()
    else:
        ax.text(0.5, 0.5, "No trades", color=_FG, ha="center", transform=ax.transAxes)
    ax.set_title(f"Equity Curve {('(' + currency + ')') if currency else ''}".strip())
    ax.set_ylabel("Equity")
    return _save(fig)


def drawdown_png(points: list[tuple[object, float]], starting_balance: float) -> bytes:
    """Drawdown % from the running peak of the equity curve."""
    fig, ax = plt.subplots(figsize=(8, 3), facecolor=_BG)
    _style(ax)
    if points:
        xs = [p[0] for p in points]
        equity = [starting_balance + p[1] for p in points]
        peak = equity[0]
        dd = []
        for e in equity:
            peak = max(peak, e)
            dd.append((e - peak) / peak * 100 if peak else 0.0)
        ax.plot(xs, dd, color=_RED, linewidth=1.5)
        ax.fill_between(xs, 0, dd, color=_RED, alpha=0.18)
        if hasattr(xs[0], "strftime"):
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
            fig.autofmt_xdate()
    else:
        ax.text(0.5, 0.5, "No trades", color=_FG, ha="center", transform=ax.transAxes)
    ax.set_title("Drawdown (%)")
    ax.set_ylabel("Drawdown %")
    return _save(fig)


def emotion_trend_png(dates: list[str], scores: list[int]) -> bytes:
    """Daily emotion score trend (0–100), with the 50 auto-lock threshold."""
    fig, ax = plt.subplots(figsize=(8, 3.5), facecolor=_BG)
    _style(ax)
    if scores:
        ax.plot(dates, scores, color=_BLUE, linewidth=2, marker="o", markersize=4)
        ax.axhline(50, color=_RED, linewidth=1, linestyle="--", alpha=0.7)
        ax.text(0, 52, "lock threshold (50)", color=_RED, fontsize=7)
        ax.set_ylim(0, 105)
        if len(dates) > 8:
            step = max(1, len(dates) // 8)
            ticks = list(range(0, len(dates), step))
            ax.set_xticks(ticks)
            ax.set_xticklabels([dates[i] for i in ticks], rotation=45, ha="right")
        else:
            ax.set_xticks(range(len(dates)))
            ax.set_xticklabels(dates, rotation=45, ha="right")
    else:
        ax.text(0.5, 0.5, "No emotion data", color=_FG, ha="center", transform=ax.transAxes)
    ax.set_title("Emotion Score Trend")
    ax.set_ylabel("Score")
    return _save(fig)
