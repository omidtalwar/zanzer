"""V4 — Psychology / Emotion Scoring Engine.

Pure functions only — no DB, no Telegram, no MT5. The worker calls these and
then persists the result. This makes the logic unit-testable in isolation.

Score starts at 100 each trading day and is deducted based on behaviour:
  Loss #1           −10
  Loss #2           −15
  Loss #3+          −20
  Off-plan trade    −25  (journal entry: plan_followed = "no")
  Revenge trade     −30  (new position opened within REVENGE_WINDOW_SECS of a loss)
  Skipped entry     −10
  Skipped exit      −10

Score < 50 → auto-lock + user must explain (lock held until next trading day).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

REVENGE_WINDOW_SECS = 600  # 10 minutes
LOCK_THRESHOLD = 50


@dataclass
class ScoreEvent:
    reason: str
    delta: int          # always negative
    ts: str             # ISO timestamp


@dataclass
class DayScore:
    date: str           # YYYY-MM-DD server day
    score: int          # 0–100
    events: list[ScoreEvent] = field(default_factory=list)
    locked_by_score: bool = False


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def compute_day_score(
    *,
    today_trades: list[dict],       # dicts from repo Trade objects
    today_journals: list[dict],     # dicts from repo TradeJournal objects
    last_loss_closed_at: datetime | None,
    new_position_opened_at: datetime | None,
    date: str,
) -> DayScore:
    """Compute today's emotion score from all available signals.

    Parameters
    ----------
    today_trades:
        List of Trade dicts for today. Each must have:
        ``profit`` (float|None), ``status`` (str),
        ``entry_journal_id`` (int|None), ``exit_journal_id`` (int|None),
        ``opened_at`` (datetime).
    today_journals:
        List of TradeJournal dicts. Each must have:
        ``type`` (str), ``plan_followed`` (str|None), ``skipped`` (bool).
    last_loss_closed_at:
        Timestamp of the most-recently closed losing trade (from previous cycle).
        Used for revenge detection.
    new_position_opened_at:
        Timestamp of a newly detected open position (if any this cycle).
    date:
        Server-day string (YYYY-MM-DD) for this score row.
    """
    score = 100
    events: list[ScoreEvent] = []

    def deduct(reason: str, delta: int) -> None:
        nonlocal score
        score = max(0, score + delta)
        events.append(ScoreEvent(reason=reason, delta=delta, ts=_now_iso()))

    # --- Loss deductions --------------------------------------------------
    losses = [t for t in today_trades if (t.get("profit") or 0) < 0]
    for i, _ in enumerate(losses):
        if i == 0:
            deduct("Loss #1", -10)
        elif i == 1:
            deduct("Loss #2 (consecutive)", -15)
        else:
            deduct(f"Loss #{i + 1}", -20)

    # --- Off-plan entries (journal says plan_followed = "no") -------------
    for j in today_journals:
        if j.get("type") == "entry" and j.get("plan_followed") == "no" and not j.get("skipped"):
            deduct("Off-plan trade (journal: plan not followed)", -25)

    # --- Skipped journals -------------------------------------------------
    for t in today_trades:
        if t.get("entry_journal_id") is None and t.get("entry_prompted_at") is not None:
            # Only penalise if the trade status shows it was actually skipped.
            if t.get("status") in ("entry_skipped", "exit_skipped", "fully_journaled"):
                pass  # entry_skipped is handled by skip repo call which already alerted
        for j in today_journals:
            if j.get("trade_id") == t.get("id") and j.get("skipped"):
                if j.get("type") == "entry":
                    deduct("Skipped entry journal", -10)
                elif j.get("type") == "exit":
                    deduct("Skipped exit journal", -10)

    # --- Revenge trade detection ------------------------------------------
    if (
        last_loss_closed_at is not None
        and new_position_opened_at is not None
    ):
        last = last_loss_closed_at
        opened = new_position_opened_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        gap = (opened - last).total_seconds()
        if 0 <= gap <= REVENGE_WINDOW_SECS:
            deduct(
                f"Revenge trade — new position opened {int(gap)}s after a loss",
                -30,
            )

    locked_by_score = score < LOCK_THRESHOLD
    return DayScore(date=date, score=score, events=events, locked_by_score=locked_by_score)


def score_emoji(score: int) -> str:
    if score >= 80:
        return "🟢"
    if score >= 60:
        return "🟡"
    if score >= 50:
        return "🟠"
    return "🔴"


def score_label(score: int) -> str:
    if score >= 80:
        return "Disciplined"
    if score >= 60:
        return "Acceptable"
    if score >= 50:
        return "At risk"
    return "LOCKED — score too low"
