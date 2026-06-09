# Zanzer SaaS Plan — Multi-User Paid Bot

> Turning the personal Trading Guardian into a paid Telegram service where each subscriber links their own MT5 account and gets automated risk protection.
> **Payments model chosen: crypto / manual activation.** Decided 2026-06-07.

---

## 1. Goal

A new user finds **@Zanzerbot**, subscribes (pays in crypto), links their MT5 account, and the bot then monitors and enforces *their* risk rules — all controlled from Telegram. You (admin) verify payments and activate access.

## 2. Architecture (target)

```
                 ┌────────────────────────────┐
   Telegram  ←→  │  Bot service (aiogram)      │
   (many users)  │  - commands, onboarding     │
                 │  - subscription gating      │
                 └─────────────┬──────────────┘
                               │
                 ┌─────────────▼──────────────┐
                 │  Core API (FastAPI)         │
                 │  - users / subs / settings  │
                 │  - risk + enforcement logic │
                 └───────┬──────────────┬──────┘
                         │              │
              ┌──────────▼───┐   ┌──────▼───────────────┐
              │ PostgreSQL   │   │ Broker layer          │
              │ (all state)  │   │  MetaApi.cloud        │
              └──────────────┘   │  (many MT5 accounts)  │
                                 └──────┬───────────────┘
                 ┌──────────────┐       │
                 │ Worker(s)    │  reads/closes per account
                 │ per-account  │◄──────┘
                 │ monitoring   │   (Redis queue / scheduler)
                 └──────────────┘
```

## 3. The critical change — broker layer

The current `MetaTrader5` package = **one local terminal per process**. It does not scale to many users. For SaaS, switch to **MetaApi.cloud** (manages many MT5 accounts, reads data, and closes trades remotely).

**Design:** hide the broker behind an interface so both worlds coexist:

```
BrokerClient (interface)
 ├─ LocalMT5Client   # current MetaTrader5 pkg — for YOUR dev/testing
 └─ MetaApiClient    # SaaS — many users' accounts via MetaApi
```

`status_service`, `risk_service`, and `enforcement_service` call the interface, not MT5 directly. This lets us migrate without rewriting the risk logic (which is already pure and tested).

> **Note:** the `.mq5` EA does **not** scale to paying users (can't install on each terminal). In SaaS, enforcement is **server-side** via MetaApi (close positions through the API). The EA stays as the self-hosted/personal option.

## 4. Data model (PostgreSQL)

| Table | Key fields |
|---|---|
| `users` | id, telegram_id (unique), username, role (user/admin), status, created_at |
| `subscriptions` | id, user_id, plan, status, started_at, **expires_at**, activated_by |
| `mt5_accounts` | id, user_id, **metaapi_account_id**, broker, login, server, account_type (investor/trading), status |
| `risk_settings` | id, user_id, max_trades_per_day, max_daily_loss_pct, max_risk_per_trade_pct, max_consecutive_losses, max_account_exposure_pct |
| `locks` | id, user_id, locked, reason, locked_at, day |
| `risk_events` | id, user_id, type, message, created_at  *(audit — PRD principle 4)* |
| `payments` | id, user_id, method (crypto), amount, currency, tx_hash, status, received_at, note |

Per-user `risk_settings` default to the PRD values; users can adjust within bounds you set.

## 5. Subscription & access (crypto / manual)

**Plans:** 7-day free trial (auto), Monthly, Quarterly (discount). Prices in a stablecoin (e.g. USDT).

**Flow:**
1. `/subscribe` → bot shows your **crypto wallet address**, the **amount**, and a **unique reference/memo** per user.
2. User pays on-chain, then submits proof: `/paid <tx_hash>`.
3. Payment row created with status `pending`.
4. **You (admin)** verify the transaction on-chain and run `/activate <user> <days>` → sets `subscriptions.expires_at`.
5. *(Optional later)* auto-verify with a blockchain API (e.g. USDT TRC-20 transfers to your address) to remove the manual step.

**Gating:** a middleware checks `expires_at > now` before serving commands or running monitoring. Expired → monitoring paused + a renewal reminder.

## 6. Telegram bot UX (two-way)

**User commands**
- `/start` — register, accept ToS/disclaimer, show intro + plans
- `/subscribe` — payment instructions (address, amount, reference)
- `/paid <tx_hash>` — submit payment proof
- `/link` — link MT5 account (login, server, password → provisioned in MetaApi)
- `/status` — account + risk snapshot
- `/risk` — view / edit risk rules
- `/lock` · `/unlock`
- `/today` · `/weekly` · `/performance`
- `/help`

**Admin commands (you)**
- `/admin_pending` — list pending payments
- `/activate <user> <days>` · `/revoke <user>`
- `/users` — list / search subscribers

## 7. Security & legal (do not skip)

- **Credentials:** prefer letting **MetaApi hold the MT5 password**; store only `metaapi_account_id` locally. If you must store passwords, **encrypt at rest** (AES/Fernet, key in a secret manager).
- **Tiers:** investor (read-only) password = monitor only (cannot close trades); trading password = full enforcement. Be explicit with users about which they provide.
- **Legal:** acting on other people's money is often **regulated**. Add **Terms of Service**, a **risk disclaimer** ("not financial advice"), and a **privacy policy**, accepted at `/start`. **Get legal advice for your jurisdiction before charging.**
- **Audit:** log every action and admin operation (`risk_events`).

## 8. Hosting / ops (PRD-aligned)

- VPS (Ubuntu 24.04): **FastAPI (API)** + **bot worker** + **monitor worker(s)** + **PostgreSQL** + **Redis**.
- Monitoring: **Grafana + Prometheus**.
- Postgres backups; secrets via env/secret manager; HTTPS.

## 9. Costs → pricing

- **MetaApi** charges per account/month — this is your main per-user cost; price subscriptions above it.
- Plus VPS + your time. Suggested: free 7-day trial → monthly USDT price set with healthy margin over MetaApi+infra per user.

## 10. Phased build plan

| Phase | Deliverable |
|---|---|
| **A. Multi-user DB (extends V3) — ✅ DONE** | SQLAlchemy async (SQLite dev / Postgres prod); `users/subscriptions/mt5_accounts/risk_settings/locks/risk_events/payments`; encrypted credentials; `/users/*` + `/admin/*` routes; trial + crypto payment + admin activate. *(Alembic migrations still to add for prod; dev uses create_all.)* |
| **B. Broker abstraction + workers — ✅ DONE** | `BrokerClient` interface (`LocalMT5Client` prod / `MockBrokerClient` tests); per-user `RiskLimits`; `AccountWorker` (DB-backed lock/events, dry_run/live); `run_account.py` (one process per account) + `Supervisor` (orchestrates active accounts). *(Terminal farm, not MetaApi — owner's choice.)* |
| **C. Telegram Command Center — ✅ DONE** | Two-way bot (httpx long-polling, no aiogram): `/start` (register+disclaimer), `/link` (guided MT5 connect, password auto-deleted + encrypted), `/status /risk /lock /unlock /subscribe /paid`, admin `/pending /verify /activate /users`. `backend/bot/`. *(Subscription-gating of the worker is enforced by the supervisor via active-subscription filter.)* |
| **D. Onboarding + account linking** | `/link` flow → provision account in MetaApi; validate connection. |
| **E. Crypto / manual payments** | `/subscribe`, `/paid`, admin `/activate`, expiry jobs, reminders. *(Auto on-chain verify optional later.)* |
| **F. Scale enforcement** | Per-account monitoring workers via Redis queue; server-side close on breach. |
| **G. Beta → launch** | Onboard a few users, harden, finalize legal, monitor, launch. |

## 11. What we keep from today

- ✅ Risk math (`risk_service`) — pure & tested, reused as-is.
- ✅ Enforcement policy (`enforcement_service.decide_actions`) — pure & tested, reused as-is.
- ✅ Telegram alert formatting — reused.
- ✅ FastAPI app shell.
- 🔄 Swap: broker layer (MetaApi), add DB, make everything user-scoped, two-way bot.

---

### Recommended next action
Start **Phase A** (multi-user PostgreSQL foundation). It’s the prerequisite for everything else and overlaps with the planned V3 Journal, so it’s not wasted work even if you delay monetizing.
