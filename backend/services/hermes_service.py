"""Hermes — AI Performance Coach (Phase 6).

Reviews the trader's OWN past data (trades, journal answers, emotion scores)
and returns written coaching: what worked, repeated mistakes, and concrete
improvements.

SAFETY (project invariant): the AI never trades, never sends buy/sell signals,
and never gives financial advice. It only reflects on past behaviour and
discipline. The system prompt enforces this, and nothing here can touch the
broker or risk engine.

Disabled gracefully when no OpenAI key is configured (settings.ai_coach_available).
"""
from __future__ import annotations

from backend.logging_config import get_logger
from backend.services.analytics_service import PerformanceMetrics, fmt_pf, fmt_rr

log = get_logger("hermes")

SYSTEM_PROMPT = (
    "You are Hermes, the discipline & psychology coach inside Zanzer (an AI "
    "trading guardian). You are NOT a trade advisor.\n\n"
    "RULES:\n"
    "- NEVER suggest entries, exits, signals, price targets, or what to trade.\n"
    "- NEVER give financial advice. Only reflect on PAST behaviour, discipline "
    "and psychology.\n\n"
    "OUTPUT FORMAT — this is shown in Telegram, so keep it SHORT and scannable "
    "(max ~80 words total):\n"
    "- Plain text only. Do NOT use markdown (#, *, **, ###).\n"
    "- Line 1: one short summary sentence.\n"
    "- Then a line '✅ Good:' followed by 1-2 bullets starting with '• '.\n"
    "- Then '⚠️ Improve:' with 1-2 short bullets.\n"
    "- Then '🎯 Next:' with 1-2 short action items.\n"
    "Reference their real numbers. Be direct and warm. No filler, no headers."
)


def build_review_context(
    *,
    metrics: PerformanceMetrics | None,
    journals: list[dict],
    emotion_scores: list[dict],
    period_label: str,
) -> str:
    """Compose a compact, factual summary of the trader's period for the model.

    `journals` items: {type, plan_followed, emotion_entry, emotion_exit,
                       mistakes, rating, setup_reason, skipped}
    `emotion_scores` items: {date, score}
    """
    lines: list[str] = [f"PERIOD: {period_label}", ""]

    if metrics is None:
        lines.append("No completed trades this period.")
    else:
        lines += [
            "PERFORMANCE (already computed — do not recompute):",
            f"- Trades: {metrics.total_trades} (W {metrics.wins} / L {metrics.losses} / BE {metrics.breakeven})",
            f"- Win rate: {metrics.win_rate:g}%",
            f"- Net P&L: {metrics.net_pnl:+.2f}",
            f"- Profit factor: {fmt_pf(metrics.profit_factor)}",
            f"- Avg RR (payoff): {fmt_rr(metrics.payoff_ratio)}",
            f"- Expectancy: {metrics.expectancy:+.2f} per trade",
            f"- Avg win {metrics.avg_win:+.2f} / Avg loss -{metrics.avg_loss:.2f}",
            f"- Best pair {metrics.best_symbol} ({metrics.best_symbol_pnl:+.2f}), "
            f"worst {metrics.worst_symbol} ({metrics.worst_symbol_pnl:+.2f})",
        ]

    # Discipline signals from journals.
    total_j = len(journals)
    skipped = sum(1 for j in journals if j.get("skipped"))
    off_plan = sum(1 for j in journals if j.get("plan_followed") == "no")
    mostly_plan = sum(1 for j in journals if j.get("plan_followed") == "mostly")
    mistakes = [j.get("mistakes") for j in journals if j.get("mistakes")]
    emotions = [j.get("emotion_entry") or j.get("emotion_exit") for j in journals]
    emotions = [e for e in emotions if e]
    ratings = [j.get("rating") for j in journals if j.get("rating")]

    lines += [
        "",
        "DISCIPLINE & PSYCHOLOGY:",
        f"- Journals recorded: {total_j} (skipped: {skipped})",
        f"- Off-plan trades: {off_plan}; partially-on-plan: {mostly_plan}",
    ]
    if emotions:
        # Frequency of each emotion.
        freq: dict[str, int] = {}
        for e in emotions:
            freq[e] = freq.get(e, 0) + 1
        emo_str = ", ".join(f"{k}×{v}" for k, v in sorted(freq.items(), key=lambda x: -x[1]))
        lines.append(f"- Emotions logged: {emo_str}")
    if ratings:
        lines.append(f"- Avg self-rated trade quality: {sum(ratings) / len(ratings):.1f}/5")
    if mistakes:
        lines.append("- Mistakes the trader noted:")
        for mk in mistakes[:8]:
            lines.append(f"    • {mk}")

    if emotion_scores:
        scores = [e["score"] for e in emotion_scores]
        avg = sum(scores) / len(scores)
        lowest = min(scores)
        lines += [
            "",
            "EMOTION SCORE (0–100, starts at 100 daily; <50 = auto-lock):",
            f"- Average: {avg:.0f}, lowest: {lowest}, days tracked: {len(scores)}",
        ]

    return "\n".join(lines)


def build_reco_context(trades: list[dict], journals: list[dict], period_label: str) -> str:
    """Compact summary of the trader's recent trades+journals for recommendations.

    `trades` items: symbol, profit, session, exit_reason, entry_timeframe,
                    duration_s, status.
    `journals` items: type, setup_reason (strategy), mistakes, lesson, skipped.
    """
    closed = [t for t in trades if t.get("profit") is not None]
    lines = [f"PERIOD: {period_label}", f"Trades: {len(closed)}", ""]

    def _bucket(key: str):
        agg: dict[str, list[float]] = {}
        for t in closed:
            k = t.get(key) or "—"
            agg.setdefault(k, []).append(t.get("profit") or 0.0)
        out = []
        for k, pnls in sorted(agg.items(), key=lambda kv: -sum(kv[1])):
            wins = sum(1 for p in pnls if p > 0)
            out.append(f"  {k}: {len(pnls)} trades, {wins}/{len(pnls)} wins, net {sum(pnls):+.2f}")
        return out

    if closed:
        lines.append("BY SESSION:")
        lines += _bucket("session")
        lines.append("BY TIMEFRAME:")
        lines += _bucket("entry_timeframe")
        # Exit reason mix.
        reasons: dict[str, int] = {}
        for t in closed:
            reasons[t.get("exit_reason") or "—"] = reasons.get(t.get("exit_reason") or "—", 0) + 1
        lines.append("EXITS: " + ", ".join(f"{k}×{v}" for k, v in reasons.items()))

    strategies = [j.get("setup_reason") for j in journals if j.get("type") == "entry" and j.get("setup_reason")]
    mistakes = [j.get("mistakes") for j in journals if j.get("mistakes")]
    lessons = [j.get("lesson") for j in journals if j.get("lesson")]
    if strategies:
        lines.append("\nSTRATEGIES USED:")
        for s in strategies[:8]:
            lines.append(f"  • {s}")
    if mistakes:
        lines.append("MISTAKES LOGGED:")
        for m in mistakes[:8]:
            lines.append(f"  • {m}")
    if lessons:
        lines.append("LESSONS LOGGED:")
        for ln in lessons[:8]:
            lines.append(f"  • {ln}")
    return "\n".join(lines)


_USER_PREFIX = (
    "Here is my trading data for the period. Coach me on my discipline and "
    "psychology — what I did well, my repeated mistakes, and how to improve. "
    "Do NOT tell me what to trade.\n\n"
)


RECO_SYSTEM = (
    "You are Hermes, the discipline & psychology coach inside Zanzer. Based on "
    "the trader's OWN recent journal data, give a short personalised recommendation "
    "for their NEXT sessions.\n\n"
    "STRICT RULES:\n"
    "- NEVER suggest entries, exits, signals, price targets, or what instrument to trade.\n"
    "- NEVER give financial advice.\n"
    "- Recommend based on THEIR patterns: which sessions/timeframes worked, repeated "
    "mistakes to avoid, lessons to repeat, and habits to fix.\n\n"
    "OUTPUT (Telegram, SHORT, max ~90 words, plain text, no markdown):\n"
    "- One-line headline of the biggest pattern.\n"
    "- '✅ Keep doing:' 1-2 bullets.\n"
    "- '⚠️ Fix:' 1-2 bullets.\n"
    "- '🎯 Next session:' 1-2 concrete behavioural actions.\n"
    "Reference their real numbers. Be direct and warm."
)

_RECO_PREFIX = (
    "Here is my recent trading journal data. Give me a personalised recommendation "
    "for my next sessions based on my patterns. Do NOT tell me what to trade.\n\n"
)


async def generate_review(context: str, ai_config: dict) -> str:
    """Coaching review (the /coach command)."""
    return await _generate(context, ai_config, SYSTEM_PROMPT, _USER_PREFIX)


async def generate_recommendation(context: str, ai_config: dict) -> str:
    """Personalised next-session recommendation (the 2x-daily digest, /reco)."""
    return await _generate(context, ai_config, RECO_SYSTEM, _RECO_PREFIX)


async def _generate(context: str, ai_config: dict, system: str, user_prefix: str) -> str:
    """Call the admin-configured provider. Returns text or a friendly error."""
    if not ai_config.get("available"):
        return (
            "⚠️ The AI isn't configured yet. An admin can enable it from the "
            "dashboard (set a provider + API key)."
        )
    provider = ai_config.get("provider", "openai")
    try:
        if provider == "claude":
            return await _generate_claude(context, ai_config, system, user_prefix)
        return await _generate_openai(context, ai_config, system, user_prefix)
    except ModuleNotFoundError as exc:
        pkg = "anthropic" if provider == "claude" else "openai"
        log.error("AI package missing (%s): %s", pkg, exc)
        return (
            f"⚠️ The AI can't run — the <b>{pkg}</b> package isn't installed in the "
            f"Python running this service. Fix: <code>pip install {pkg}</code>, then restart."
        )
    except Exception as exc:  # noqa: BLE001
        log.error("%s AI call failed: %s", provider, exc)
        return (
            "⚠️ I couldn't reach the AI right now. Please try again later.\n"
            f"<i>({type(exc).__name__})</i>"
        )


async def _generate_openai(context: str, ai_config: dict, system: str, user_prefix: str) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=ai_config["active_key"])
    resp = await client.chat.completions.create(
        model=ai_config["active_model"],
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_prefix + context},
        ],
        temperature=0.6,
        max_tokens=300,
    )
    return resp.choices[0].message.content.strip()


async def _generate_claude(context: str, ai_config: dict, system: str, user_prefix: str) -> str:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=ai_config["active_key"])
    resp = await client.messages.create(
        model=ai_config["active_model"],
        max_tokens=300,
        temperature=0.6,
        system=system,
        messages=[{"role": "user", "content": user_prefix + context}],
    )
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "".join(parts).strip()
