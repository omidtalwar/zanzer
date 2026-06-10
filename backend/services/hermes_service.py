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
    "You are Hermes, the performance coach inside Zanzer, an AI trading guardian. "
    "You are a trading psychologist and discipline coach — NOT a trade advisor.\n\n"
    "STRICT RULES:\n"
    "- NEVER suggest entries, exits, signals, price targets, or what to trade.\n"
    "- NEVER give financial or investment advice.\n"
    "- Only analyse the trader's PAST behaviour, discipline, and psychology.\n"
    "- Focus on: rule-following, emotional control, journaling habits, repeated "
    "mistakes, and process improvement.\n"
    "- Be direct, supportive, and specific. Reference their actual numbers.\n"
    "- Keep it concise (max ~250 words). Use short sections with clear headers.\n"
    "- End with 2–3 concrete, behavioural action items for next week."
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


_USER_PREFIX = (
    "Here is my trading data for the period. Coach me on my discipline and "
    "psychology — what I did well, my repeated mistakes, and how to improve. "
    "Do NOT tell me what to trade.\n\n"
)


async def generate_review(context: str, ai_config: dict) -> str:
    """Produce the coaching review using the admin-configured provider.

    `ai_config` is the dict from repositories.get_ai_config (provider,
    active_key, active_model, available). Returns text or a friendly error.
    """
    if not ai_config.get("available"):
        return (
            "⚠️ The AI coach isn't configured yet. An admin can enable it from "
            "the dashboard (set a provider + API key)."
        )
    provider = ai_config.get("provider", "openai")
    try:
        if provider == "claude":
            return await _generate_claude(context, ai_config)
        return await _generate_openai(context, ai_config)
    except ModuleNotFoundError as exc:
        pkg = "anthropic" if provider == "claude" else "openai"
        log.error("AI coach package missing (%s): %s", pkg, exc)
        return (
            f"⚠️ The AI coach can't run — the <b>{pkg}</b> package isn't installed "
            f"in the Python running this service.\n"
            f"Fix: <code>pip install {pkg}</code> (in the same interpreter that "
            f"runs the service), then restart."
        )
    except Exception as exc:  # noqa: BLE001
        log.error("%s coach call failed: %s", provider, exc)
        return (
            "⚠️ I couldn't reach the AI coach right now. Please try again later.\n"
            f"<i>({type(exc).__name__})</i>"
        )


async def _generate_openai(context: str, ai_config: dict) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=ai_config["active_key"])
    resp = await client.chat.completions.create(
        model=ai_config["active_model"],
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _USER_PREFIX + context},
        ],
        temperature=0.6,
        max_tokens=600,
    )
    return resp.choices[0].message.content.strip()


async def _generate_claude(context: str, ai_config: dict) -> str:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=ai_config["active_key"])
    resp = await client.messages.create(
        model=ai_config["active_model"],
        max_tokens=600,
        temperature=0.6,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _USER_PREFIX + context}],
    )
    # Concatenate any text blocks in the response.
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "".join(parts).strip()
