# Zanzer — AI Trading Guardian

> Source: PRD v1.0 (Owner: Omid Talwar). This file captures the project spec; the original `prd.docx` may be deleted.

## Goal
An AI-powered trading assistant that **protects trading capital, enforces discipline, controls emotions, journals trades, analyzes performance, and assists decision-making**, integrating with **MT5** and **Telegram**.

The system is NOT primarily for *finding* trades. It acts as a personal **risk manager, trading psychologist, performance coach, and research assistant**. Its job is to prevent overtrading, revenge trading, risk-rule violations, and emotional decisions, and to enforce discipline automatically.

## Core Principles (non-negotiable)
1. **Risk management always overrides AI decisions.**
2. No trade may violate predefined risk rules.
3. The system should protect the trader from themselves.
4. Every action must be logged.
5. Human approval is required for trade execution in initial versions.

## System Architecture
```
User → Telegram → Trading Guardian API → Risk Engine → MT5 EA → Broker
Additional: GPT-5.5 → Hermes → Research & Analysis
Data: Database → Trade Journal → Performance Analytics
```
**Safety:** The Risk Engine always has final authority. AI never bypasses risk controls and never executes trades — it only assists.

## Technology Stack
- **Backend:** FastAPI, Python 3.12
- **Database:** PostgreSQL
- **Queue:** Redis
- **AI:** GPT-5.5
- **Agent framework:** Hermes
- **Trading:** MetaTrader 5 + MT5 Expert Advisor (EA)
- **Messaging:** Telegram Bot
- **Hosting:** Contabo VPS or Hostinger VPS
- **Monitoring:** Grafana, Prometheus
- **Version control:** Git / GitHub

## Risk Rules (defaults)
| Rule | Default |
|---|---|
| Max trades per day | 2 |
| Max daily loss | 5% |
| Max risk per trade | 4% |
| Max consecutive losses | 2 |
| Max account exposure | 5% |
| No trading after emotional lock | Enabled |

**Enforcement actions:** block trades, close trades, send warnings, lock account.

## Psychology / Emotion Scoring
Detects: overtrading, revenge trading, increasing lot size, breaking rules, consecutive losses.

Scoring (starts at **100**/day):
- Loss: −10
- Second loss: −15
- Third loss: −20
- Manual rule break: −25
- Revenge trade: −30
- **Threshold: below 50** → disable trading, send alert, require explanation.

## Telegram Commands
`/status` `/trades` `/journal` `/risk` `/today` `/weekly` `/lock` `/unlock` `/performance`

`/status` returns: Balance, Equity, Open Trades, Today's PnL, Emotion Score, Risk Status.

## Database Design (entities)
- **Users**: id, name, telegram_id
- **Accounts**: id, broker, account_number, balance
- **Trades**: id, symbol, direction, entry, exit, sl, tp, profit
- **TradeJournal**: id, trade_id, notes, emotion, mistakes
- **RiskEvents**: id, type, message, created_at
- **EmotionScores**: id, score, reason, created_at

Trade journal also stores: screenshot, emotion score, notes (e.g. "I entered because of FOMO").

## MT5 EA Responsibilities
Must control: new orders, existing orders, daily drawdown, daily trade count.
Must be able to: block trading, close positions, send data to API.

## Performance Analytics
- **Daily:** Win Rate, Profit Factor, Average RR, Average Hold Time
- **Weekly:** Best Setup, Worst Setup, Most Profitable Pair
- **Monthly:** Equity Curve, Drawdown, Emotional Score Trend

## AI Features (Hermes)
- Daily Briefing: Market Overview, Major News, Open Positions, Risk Assessment, Watchlist
- Trade Research (e.g. "Analyze EURUSD" → Trend, Catalysts, Bull Case, Bear Case, Risk Factors)
- Performance Coach: weekly review of what worked/failed, repeated mistakes, improvement suggestions
- Also: News Analysis, Economic Calendar Monitoring, Sector Analysis, Earnings Research

## Planned Folder Structure
```
backend/
  api/
  services/
    risk_engine/
    journal/
    analytics/
    telegram/
    hermes/
  database/
mt5/
  ea/
docs/
frontend/
  dashboard/
```

## Development Phases / Roadmap
1. **V1 — Trading Protection System:** MT5 connection, read account/trades/history, calc daily loss, count daily trades, Telegram alerts. *(Protect account before adding AI.)*
2. **V2 — Risk Enforcement Engine:** automated enforcement of all risk rules.
3. **V3 — Trading Journal:** full trade history + emotion/notes/screenshots.
4. **V4 — Psychology Engine:** emotion detection + scoring system.
5. *(Phase 5)* **Telegram Command Center:** full mobile control.
6. **V4/Phase 6 — Hermes AI Integration:** research assistant (no execution).
7. **V5 — Performance Analytics dashboard.**
8. **V6 — Semi-Automated Trading.**
9. **V7 — Advanced Multi-Agent Trading System.**

## VPS Requirements (recommended)
4 vCPU, 8 GB RAM, 100 GB SSD, Ubuntu 24.04.
Services: FastAPI, PostgreSQL, Redis, Hermes, Telegram Bot.

## Success Criteria
No revenge trading; daily risk limits enforced; discipline improves; every trade documented; risk management followed consistently; account survival improves significantly.

---
## Notes for Claude
- This is a **financial risk-control** system — correctness of risk math and the "risk overrides AI" invariant is critical. Never write code paths where AI can bypass the Risk Engine.
- Always log actions (Principle 4).
- Project lives under XAMPP htdocs but the stack is **Python/FastAPI**, not PHP. (XAMPP is just the local folder location.)
- Machine runs **Python 3.14** — pin deps with `>=`, not `==` (older pydantic-core has no 3.14 wheel). Don't redirect pip stderr in PowerShell (`2>&1 | Tee`) — it aborts pip.

## Implementation status (as of 2026-06-07)
- **V1 — DONE & verified live.** FastAPI app reads MT5 (account/positions/history), computes risk, sends Telegram alerts. Read-only. Connected to live account 63056687 (OctaFX).
- **V2 — DONE.** Risk Enforcement Engine: background loop (`backend/scheduler.py`) runs `enforcement_service.run_cycle()` every `RISK_CHECK_INTERVAL_SECONDS`. Decision policy is a **pure** function `decide_actions()`; execution is gated by `ENFORCEMENT_MODE`.
  - `dry_run` (default & current): detect + alert + lock state + log intended closes; **no real trades**.
  - `live`: actually closes positions via `mt5_service.close_all_positions()` on daily-loss breach.
  - Lock state persisted at `data/lock_state.json` (daily locks auto-expire next UTC day).
- **MT5 EA — DONE.** `mt5/ea/ZanzerGuardian.mq5` polls `GET /lock` via WebRequest; when locked it closes any position opened *after* the lock (grandfathers pre-existing ones). Compiles clean (0 errors). Caveat: MT5 has no pre-trade hook for manual orders, so it closes-immediately rather than pre-rejecting. Setup + WebRequest whitelist steps in `mt5/ea/README.md`. Source also copied into the terminal's `MQL5\Experts\`.
- **Phase A (multi-user DB) — DONE.** SQLAlchemy async + SQLite (dev) / PostgreSQL (prod via `DATABASE_URL`). Tables: users, subscriptions, mt5_accounts (password Fernet-encrypted), risk_settings, locks, risk_events, payments. Repositories in `backend/repositories.py`; schemas in `backend/schemas.py`; routes `backend/api/user_routes.py` (`/users/*`) and `backend/api/admin_routes.py` (`/admin/*`, guarded by `X-Admin-Token`). `init_db()` runs in lifespan (create_all for dev; Alembic is the planned prod migration tool). Tests: `tests/test_repositories.py`. Verified live: register→trial, risk update, payment submit→verify, admin activate, admin 403 guard. **Direction = paid multi-user SaaS, terminal-farm broker model, crypto/manual payments — see `docs/SAAS_PLAN.md`.**

- **Phase B (terminal-farm workers) — DONE.** Per-account risk worker driving the SaaS path:
  - `backend/broker/base.py` `BrokerClient` Protocol; `MockBrokerClient` (tests); `LocalMT5Client`/`make_local_client` (prod). `MT5Service` now takes per-account login/password/server/path overrides and structurally satisfies `BrokerClient`.
  - `risk_service.compute_risk_status(account, deals, limits=None)` now accepts per-user `RiskLimits` (defaults to settings for the personal path — backward compatible).
  - `backend/worker.py` `AccountWorker`: one account's cycle — build status (per-user limits) → `decide_actions` → execute WARN (notify+dedup+event) / LOCK (DB, daily) / CLOSE_ALL (broker in live; logged in dry_run). DB-backed lock + risk_events via repositories.
  - `backend/run_account.py` = one worker process per account (`python -m backend.run_account <id>`). `backend/supervisor.py` = orchestrator: one process per active account, reconcile loop, relaunch dead, stop inactive (injectable launcher for tests). Run via `python -m backend.supervisor`.
  - Added `mt5_accounts.terminal_path`. Tests: `tests/test_worker.py` (dry_run vs live), `tests/test_supervisor.py`. **Personal V1/V2 app untouched and still verified live.** Supervisor is NOT auto-started in the personal app (would double-act on the owner's account).

- **Phase C (two-way Telegram bot) — DONE.** Built on httpx long-polling (no aiogram/aiohttp — avoids Python-3.14 wheel risk). `backend/bot/`: `client.py` (getUpdates/sendMessage/deleteMessage), `dispatcher.py` (`BotDispatcher` — commands + `/link` FSM, injectable `send`/`delete` for tests), `app.py` polling loop, run via `python -m backend.bot`. Commands: `/start /help /status /link /risk /lock /unlock /subscribe /paid /cancel`; admin (by telegram id in `BOT_ADMIN_IDS`): `/pending /verify /activate /users`. `/link` is a guided flow (login→server→password→type) that **deletes the password message** and stores it Fernet-encrypted via `repo.add_account`. Config added: `BOT_ADMIN_IDS`, `CRYPTO_WALLET_ADDRESS`, `CRYPTO_CURRENCY`, `PRICE_MONTHLY`, `PRICE_QUARTERLY`. Tests: `tests/test_bot_dispatcher.py`. Owner (5625070857) is admin. **Run the bot as its own process** (separate from the API and supervisor). NOTE: only one getUpdates consumer per bot token at a time.

- **Live enforcement + key fixes (2026-06-08).** Verified `ENFORCEMENT_MODE=live` actually closes positions on a real account. (1) **Timezone:** MT5 deal times are broker SERVER time (GMT+3 for OctaFX), not UTC — `get_today_deals` now uses `_server_offset()` (config `BROKER_UTC_OFFSET_HOURS`, else tick auto-detect), widens the window (±2d), and counts the server-day (fixed "trades today = 0"). (2) **Close-while-locked:** `decide_actions(risk, lock, has_open_positions)` flattens a locked account (closes trades opened while locked) — enforces "no trading while locked" without the EA. (3) **Filling mode:** `close_position` tries the symbol's advertised filling modes then RETURN (was hardcoded IOC → retcode 10030). (4) Worker writes `AccountSnapshot` each cycle so `/status` shows live balance/PnL. Closing requires the TRADING (master) password, not investor.

- **Instant /link validation + terminal provisioning (2026-06-08).** (1) `/link` now validates credentials immediately via an isolated subprocess `backend/validate_account.py` (sets account active/error, replies ✅/❌); wired through `BotDispatcher(validator=...)` (`bot/validation.py`). (2) **Provisioning** `backend/provisioning.py`: clones `BASE_TERMINAL_DIR` → `TERMINALS_ROOT/<login>/` (skips logs/history), records `mt5_accounts.terminal_path`, launches portable; `AUTO_PROVISION=true` makes `run_account` provision on startup; manual via `python -m backend.provisioning <id>` or admin `/provision <id>` (`BotDispatcher(provisioner=...)`). Idempotent; gracefully skips when base not set (falls back to shared terminal). Tested with fake base install (`tests/test_provisioning.py`). 42 tests total. CAVEAT: MT5 terminal login automation is finicky — validate with 2 accounts on a real VPS.

- **Admin web dashboard + subscription expiry (2026-06-08).** (1) **Dashboard:** real web UI at `/dashboard` (served by `backend/admin_app.py`, a DB-only FastAPI app hosted inside `backend.service` on `DASHBOARD_PORT` 8090 — no MT5/scheduler, safe alongside workers). Single self-contained HTML page (`backend/api/dashboard_routes.py`); admin signs in with `ADMIN_TOKEN`; tables for summary/pending-payments/users/accounts with verify+activate buttons; calls `/admin/*` (added `/admin/summary`, `/admin/accounts`). (2) **Expiry:** `backend/expiry.py` pure `decide_notice()` + loop in the service sends renewal reminders (`EXPIRY_REMINDER_DAYS` before) and an expired notice, each once, to the user's chat; `Subscription.notice_state` tracks it (reset on activate). Supervisor already stops expired users' workers. (3) Dev SQLite auto-adds new columns in `init_db` (`notice_state`, `terminal_path`) since create_all won't (prod = Alembic). 51 tests. Service now runs bot+supervisor+expiry+dashboard via one `python -m backend.service`. CRYPTO_WALLET_ADDRESS still empty (owner must set for /subscribe).

- **CryptoBot/CryptoPay auto-payments (2026-06-08).** Chosen Telegram-easy method. `backend/payments/cryptopay.py` (API client: createInvoice/getInvoices/getMe), `backend/payments/flow.py` (`create_subscription_invoice` wired as the bot's `invoicer`; `poll_once`/`run_poller` background loop in the service). `/subscribe [quarterly]` → creates a CryptoPay invoice, replies with a one-tap pay link; the **poller auto-detects payment and auto-activates** the subscription (no manual verify). Falls back to the manual wallet/tx flow if `CRYPTOPAY_TOKEN` unset. Payment table gained `provider/invoice_id/plan/days` (dev SQLite auto-add). Config: `CRYPTOPAY_TOKEN`, `CRYPTOPAY_TESTNET`, `CRYPTOPAY_ASSET`. Polling (not webhook) so no public URL needed. 52 tests. **Owner must set `CRYPTOPAY_TOKEN`** (from @CryptoBot → Crypto Pay → Create App) to enable.

- **Telegram Stars + CryptoPay live (2026-06-08).** CryptoPay token set (mainnet, app "Dickey Octopus App") → `/subscribe` issues real auto-confirming invoices; poller active. **Telegram Stars** added as a 2nd native method: `backend/payments/stars.py` (`send_star_invoice` wired as dispatcher `star_invoicer`; `handle_successful_payment`), client gained `send_invoice`/`answer_pre_checkout_query` (currency XTR, empty provider_token). `/stars [quarterly]` sends a Stars invoice; `app.py` loop now answers `pre_checkout_query` and processes `message.successful_payment` → activates subscription (plan/days carried in invoice payload). Config: `PRICE_MONTHLY_STARS`/`PRICE_QUARTERLY_STARS`. 53 tests. NOTE: both are REAL money on mainnet (no Stars testnet); CryptoPay testnet needs a separate testnet token.

- **Production hardening + ToS (2026-06-08).** (1) **Free trial removed** — `trial_days` default 0 → new users start `inactive`/pending until paid or admin `/activate` (works for friends/self). (2) **ToS gate:** `users.tos_accepted_at`; `/start` shows the strong safety disclaimer (does NOT trade/execute/signal/advise — you stay in control) and requires `/agree`; `/link` blocked until accepted. (3) **ENCRYPTION_KEY set** in .env (real Fernet key; DB was wiped so no re-encrypt needed). (4) **Alembic** configured (`alembic.ini`, async `migrations/env.py`, initial migration `migrations/versions/*_initial_schema.py` covering all tables) — prod: `alembic upgrade head`; dev still uses create_all + SQLite column auto-add. After /link, unpaid users get "monitoring paused — /subscribe or /stars". 55 tests. DB was wiped for fresh testing (0 users).

- **User-customizable risk rules (2026-06-08).** Users set their own rules from the bot: `/setrisk <rule> <value>` (rules: trades, dailyloss_pct, dailyloss_usd, riskpertrade, losses, exposure); `/risk` shows them + how to change. Added **dollar daily-loss cap** `RiskSettings.max_daily_loss_usd` (alongside %); `risk_service` breaches daily loss when EITHER the % or $ cap is exceeded, and a cap of **0 = disabled** (so a user can pick $ or %). The **worker reloads each user's limits every cycle** (`_refresh_limits`) so changes apply within ~30s without restart. RiskLimits/RiskStatus/schemas/config updated; bounds validated in `/setrisk`. Alembic migration `e42e0b52e4cf_add_max_daily_loss_usd` added. 59 tests.

## SaaS direction (decided 2026-06-07)
Going paid multi-user. **Broker model = self-hosted terminal farm** (one MT5 terminal + worker process per account on a Windows VPS — free, no MetaApi fees; you hold encrypted credentials). **Payments = crypto / manual** (user submits tx hash → admin verifies → `/admin/users/{id}/activate`). Phases done: A (DB), B (workers), C (two-way bot), and the **all-in-one service** (`backend/service.py` = bot + supervisor in one process; run `python -m backend.service`, deploy as always-on systemd/NSSM on the VPS). Workers now auto-launch per active account, **notify each user on their own chat** (connect ✅ / login-fail ❌), and **exit-on-failed-connect so the supervisor relaunches with fresh creds** (a user's /link fix applies with no manual step). SQLite uses WAL+busy_timeout so bot/workers share the file. `/accounts` admin cmd; account status pending→active(worker connects)/error(bad creds); re-link updates the existing account. Remaining: D = per-account MT5 terminal provisioning (each account needs its own terminal install + `mt5_accounts.terminal_path`), E = payments UX/auto-verify, F = scale, G = launch. Reuse pure `risk_service` + `enforcement_service`.
- venv at `.venv` (Python 3.14). Tests: `python -m tests.test_risk_service` and `python -m tests.test_enforcement_service` (no pytest needed).

### Enforcement policy (V2)
| Breach | Actions |
|---|---|
| Daily loss limit | WARN + LOCK + CLOSE_ALL |
| Daily trade limit | WARN + LOCK |
| Consecutive loss limit | WARN + LOCK |
| Exposure limit | WARN |

CLOSE_ALL is the only broker-touching action and only fires in `live` mode.
