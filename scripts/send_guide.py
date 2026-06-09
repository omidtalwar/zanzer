"""Send the Zanzer setup/configuration guide to Telegram, in sections.

One-off helper so you have the config reference saved in your Telegram chat.
Run (venv active, from project root):
    python -m scripts.send_guide
"""
from __future__ import annotations

import asyncio

import httpx

from backend.config import settings

API = "https://api.telegram.org"

SECTIONS: list[str] = [
    # 1. Overview
    """<b>📦 Zanzer — AI Trading Guardian</b>
Config &amp; setup reference (save this).

<b>Status:</b> V1 + V2 + MT5 EA done.
<b>Stack:</b> FastAPI / Python 3.14, MetaTrader5, Telegram.
<b>Account:</b> 63056687 @ OctaFX-Real.

<b>Run the app</b> (from project root):
<code>cd c:\\xampp\\htdocs\\zanzer
.\\.venv\\Scripts\\Activate.ps1
uvicorn backend.main:app --reload</code>
Docs UI: http://127.0.0.1:8000/docs""",

    # 2. .env keys
    """<b>⚙️ .env configuration keys</b>

<b>MetaTrader 5</b>
MT5_LOGIN / MT5_PASSWORD / MT5_SERVER — leave blank to attach to the running terminal.
MT5_TERMINAL_PATH — optional path to terminal64.exe.

<b>Telegram</b>
TELEGRAM_BOT_TOKEN — from @BotFather.
TELEGRAM_CHAT_ID — your chat id (5625070857).

<b>Enforcement (V2)</b>
ENFORCEMENT_MODE — dry_run or live.
RISK_CHECK_INTERVAL_SECONDS — loop interval (0 disables).
LOCK_STATE_PATH — data/lock_state.json.

<b>App</b>
APP_ENV — development.""",

    # 3. Risk rules
    """<b>🛡️ Risk rules (current defaults)</b>

Max trades per day: 2
Max daily loss: 5%
Max risk per trade: 4%
Max consecutive losses: 2
Max account exposure: 5%

Override any of these in .env (MAX_TRADES_PER_DAY, MAX_DAILY_LOSS_PCT, MAX_RISK_PER_TRADE_PCT, MAX_CONSECUTIVE_LOSSES, MAX_ACCOUNT_EXPOSURE_PCT).""",

    # 4. Enforcement policy
    """<b>🚦 Enforcement policy (V2)</b>

When a limit is breached:
• Daily LOSS limit → warn + lock + close all positions
• Daily TRADE limit → warn + lock
• CONSECUTIVE loss limit → warn + lock
• EXPOSURE limit → warn

<b>Modes:</b>
• dry_run (default, SAFE) — detect + alert + set lock + log intended closes; NO real trades.
• live — actually closes positions on a loss breach.

Keep dry_run until you trust it. Switch via ENFORCEMENT_MODE in .env.""",

    # 5. API endpoints
    """<b>🔌 API endpoints</b>

GET / — app info
GET /health — liveness
GET /account — MT5 account
GET /positions — open positions
GET /status — account + positions + risk
POST /alerts/status — push status to Telegram
POST /alerts/check — warn if any limit breached
GET /lock — current lock state
POST /lock?reason=... — manual lock
POST /unlock — clear lock
POST /enforce — run one enforcement cycle now
GET /enforcement-mode — current mode""",

    # 6. EA setup
    """<b>🤖 MT5 EA — ZanzerGuardian</b>
Enforces the lock on the terminal (closes trades opened while locked).

<b>Setup:</b>
1) MT5: Tools → Options → Expert Advisors → tick "Allow WebRequest for listed URL" → add http://127.0.0.1:8000
2) MetaEditor (F4) → open ZanzerGuardian.mq5 → Compile (F7)
3) Enable Algo Trading (green) → drag EA onto any chart

<b>Note:</b> MT5 cannot pre-reject manual orders, so the EA closes a blocked trade a few seconds after it opens. Existing positions before a lock are left alone.""",

    # 7. Telegram integration status
    """<b>📲 Telegram integration status</b>

✅ Outbound alerts work: risk warnings (auto, every cycle when a limit is hit) + status push.
✅ Bot: @Zanzerbot, chat id 5625070857.

❌ Not yet: two-way commands from your phone (/status, /lock, /today) — that's Phase 5 (Command Center).
❌ Not yet: EA does not notify Telegram when it blocks a trade (planned: EA → /events endpoint).

<b>Test alert:</b> POST http://127.0.0.1:8000/alerts/status""",

    # 8. Multi-user / SaaS (Phase A)
    """<b>👥 Multi-user / SaaS — Phase A (done)</b>

Database foundation for the paid bot is built.
• Dev DB: SQLite (DATABASE_URL). Prod: PostgreSQL.
• Tables: users, subscriptions, mt5_accounts (password ENCRYPTED), risk_settings, locks, risk_events, payments.
• New .env keys: DATABASE_URL, ENCRYPTION_KEY (required in prod), ADMIN_TOKEN, TRIAL_DAYS.

<b>Broker model:</b> terminal farm — one MT5 terminal + worker per account on your Windows VPS (free, no MetaApi fees; you hold encrypted credentials).
<b>Payments:</b> crypto / manual.""",

    # 9. Multi-user API + payment flow
    """<b>🔑 Multi-user API &amp; payment flow</b>

<b>User</b>
POST /users/register {telegram_id, username}
GET /users/{id}
GET /users/{id}/subscription
PUT /users/{id}/risk
POST /users/{id}/accounts (login, server, password)
POST /users/{id}/payments (tx_hash)

<b>Admin</b> (header X-Admin-Token: ADMIN_TOKEN)
GET /admin/payments/pending
POST /admin/payments/{id}/verify
POST /admin/users/{id}/activate?days=30&amp;plan=monthly
GET /admin/users

<b>Flow:</b> register → 7-day trial → user pays crypto → POST /payments → you verify → activate.""",

    # 10. Run tests + next steps
    """<b>🧪 Tests &amp; next steps</b>

<b>Run tests</b> (venv active):
python -m tests.test_risk_service
python -m tests.test_enforcement_service
python -m tests.test_repositories

<b>Done so far:</b> V1 (monitor) + V2 (enforce) + MT5 EA + Phase A (multi-user DB).

<b>Next phases:</b>
B = terminal-farm workers (per-user broker)
C = two-way Telegram command center
D = onboarding/account linking
E = crypto payment UX
F = scale, G = launch
(See docs/SAAS_PLAN.md)""",
]


async def main() -> None:
    if not settings.telegram_enabled:
        raise SystemExit("Telegram not configured in .env (token/chat id missing).")
    url = f"{API}/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=15.0) as client:
        for i, text in enumerate(SECTIONS, 1):
            resp = await client.post(
                url,
                json={
                    "chat_id": settings.telegram_chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            ok = resp.status_code == 200
            print(f"[{i}/{len(SECTIONS)}] {'sent' if ok else 'FAILED ' + resp.text}")
            await asyncio.sleep(0.4)  # gentle pacing
    print("Done — check your Telegram.")


if __name__ == "__main__":
    asyncio.run(main())
