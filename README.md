# Zanzer — AI Trading Guardian

An AI-powered trading assistant that **protects trading capital and enforces discipline** by integrating with MetaTrader 5 and Telegram. See [CLAUDE.md](CLAUDE.md) for the full product spec.

> **Core invariant:** Risk management always overrides AI. The system never lets AI bypass risk controls, and (in early versions) never executes trades automatically.

## Current status — V2: Risk Enforcement Engine

V1 (read-only) connects to MT5 and:
- reads account info, open positions, and deal history
- calculates daily loss (realized + floating) and counts daily trades
- tracks consecutive losses and account exposure
- exposes a read-only HTTP API
- sends Telegram alerts when a risk limit is breached

V2 (enforcement) adds:
- a **background loop** that checks risk every `RISK_CHECK_INTERVAL_SECONDS`
- a **decision policy** mapping breaches → actions (warn / lock / close):
  - daily **loss** limit → warn + lock + close all positions (protect capital)
  - daily **trade** limit → warn + lock
  - **consecutive loss** limit → warn + lock
  - **exposure** limit → warn
- a persisted **lock state** (`data/lock_state.json`); daily locks auto-clear next day
- manual `/lock`, `/unlock`, and `/enforce` endpoints

### ⚠️ Enforcement mode — safety

`ENFORCEMENT_MODE` controls whether the engine takes real action:
- **`dry_run`** (default): detects breaches, sends Telegram warnings, sets lock state, and **logs what it *would* close — but never closes a real position.**
- **`live`**: actually closes open positions via MT5 when the daily-loss limit is hit.

**Keep it on `dry_run`** until you've watched it behave correctly. Switching to `live` means the engine can close real trades on your account.

> Terminal-side enforcement is handled by the **MT5 Expert Advisor** (`mt5/ea/ZanzerGuardian.mq5`), which polls `/lock` and closes any position opened while locked. See [mt5/ea/README.md](mt5/ea/README.md) for setup (WebRequest whitelist + attach to chart). Note MT5 can't pre-reject manual orders, so the EA closes-immediately instead.

## Requirements

- **Windows** (the `MetaTrader5` package is Windows-only)
- **Python 3.14** (verified working here) or 3.12. Note: dependency pins are minimums (`>=`) so pip picks versions with prebuilt wheels for your interpreter — older exact pins of `pydantic-core` have no 3.14 wheel and would fail to compile.
- MetaTrader 5 terminal installed and logged in to a broker account

## Setup

```powershell
# from the project root: c:\xampp\htdocs\zanzer
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env            # then edit .env with your values
```

> The `.venv` is already created and dependencies are installed. The above is for recreating it from scratch.

Fill in `.env`:
- **MT5**: leave `MT5_LOGIN` blank to attach to an already-running, logged-in terminal, or set login/password/server to have the app log in.
- **Telegram**: create a bot via [@BotFather](https://t.me/BotFather) for `TELEGRAM_BOT_TOKEN`, and set `TELEGRAM_CHAT_ID` (message your bot, then check `getUpdates`, or use @userinfobot).
- **Risk rules**: defaults come from the PRD; override as needed.

## Run

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn backend.main:app --reload
```

Then open http://127.0.0.1:8000/docs for the interactive API.

## Production hardening

- **Encryption:** set `ENCRYPTION_KEY` (Fernet) before storing real credentials. Generate one:
  ```powershell
  .\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
  Keep it secret and stable — rotating it invalidates already-encrypted passwords.
- **Database migrations (Alembic):** dev uses `create_all`; production uses Alembic.
  ```powershell
  # point DATABASE_URL at PostgreSQL, then:
  .\.venv\Scripts\python.exe -m alembic upgrade head
  # after changing models, generate a new migration:
  .\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "describe change"
  ```
- **Terms of Service:** new users must reply `/agree` (records `users.tos_accepted_at`) before they can `/link`. The disclaimer makes clear the bot never trades, executes, or sends signals.

## Tests

```powershell
.\.venv\Scripts\python.exe -m tests.test_risk_service
```

Covers the risk math (daily loss, trade count, consecutive-loss streak, exposure). No pytest needed.

## API (all read-only)

| Method | Path | Description |
|---|---|---|
| GET | `/` | App info |
| GET | `/health` | Liveness check |
| GET | `/account` | MT5 account info |
| GET | `/positions` | Open positions |
| GET | `/status` | Full status: account + positions + risk |
| POST | `/alerts/status` | Push current status to Telegram |
| POST | `/alerts/check` | Send a Telegram warning if any risk limit is breached |
| GET | `/lock` | Current lock state |
| POST | `/lock?reason=...` | Manually lock trading (until `/unlock`) |
| POST | `/unlock` | Clear the lock |
| POST | `/enforce` | Run one enforcement cycle now (dry_run-safe) |
| GET | `/enforcement-mode` | Current enforcement mode |
| POST | `/users/register` | Register a subscriber (telegram_id) → trial + defaults |
| GET | `/users/{telegram_id}` | User profile (subscription, risk, is_active) |
| GET | `/users/{telegram_id}/subscription` | Subscription status |
| PUT | `/users/{telegram_id}/risk` | Update that user's risk rules |
| POST | `/users/{telegram_id}/accounts` | Link an MT5 account (password encrypted) |
| POST | `/users/{telegram_id}/payments` | Submit crypto payment proof (tx hash) |
| GET | `/admin/payments/pending` | (admin) list pending payments |
| POST | `/admin/payments/{id}/verify` | (admin) mark payment verified |
| POST | `/admin/users/{telegram_id}/activate` | (admin) activate/extend subscription |
| GET | `/admin/users` | (admin) list subscribers |

> `/admin/*` routes require the `X-Admin-Token` header matching `ADMIN_TOKEN`. If `ADMIN_TOKEN` is unset, admin routes return 403.

## Multi-user / SaaS (Phase A)

The project is evolving into a **paid multi-user Telegram bot** (see [docs/SAAS_PLAN.md](docs/SAAS_PLAN.md)). Phase A adds the database foundation:
- **Dev DB:** SQLite (zero install, default `DATABASE_URL`). **Prod:** PostgreSQL — set `DATABASE_URL=postgresql+asyncpg://...` and `pip install asyncpg`.
- Each subscriber is a user (by Telegram id) with a subscription (7-day trial on register), per-user risk settings, optional MT5 account (**password encrypted with Fernet** — set `ENCRYPTION_KEY` in prod), lock, events, and payments.
- **Payments = crypto/manual:** user submits a tx hash, admin verifies and activates.
- **Broker model = terminal farm:** one MT5 terminal + worker per account on a Windows VPS (Phase B).

### Phase B — terminal-farm workers

Each active account is driven by its own worker process (the `MetaTrader5` package is one-terminal-per-process):

```powershell
# one worker for a single account (the Supervisor launches these for you):
.\.venv\Scripts\python.exe -m backend.run_account <account_id>

# the supervisor: one worker per active account, auto-relaunch on crash:
.\.venv\Scripts\python.exe -m backend.supervisor
```

- **`BrokerClient`** interface ([backend/broker/](backend/broker/)) decouples risk logic from the data source: `LocalMT5Client` (prod) / `MockBrokerClient` (tests). `MT5Service` satisfies it.
- **`AccountWorker`** ([backend/worker.py](backend/worker.py)) runs one account's risk/enforcement cycle with that user's own limits, writing locks + events to the DB. Honors `ENFORCEMENT_MODE` (dry_run logs intended closes; live closes for real).
- **`Supervisor`** ([backend/supervisor.py](backend/supervisor.py)) reconciles desired vs running workers for all active accounts.
- The personal V1/V2 app and the SaaS supervisor are **separate** — don't run both against the same account.

### Run the whole SaaS with ONE command (recommended)

```powershell
.\.venv\Scripts\python.exe -m backend.service
```

This runs the **bot + supervisor + expiry notifier + admin dashboard** together. Start it once and everything is automatic:
- **Admin dashboard:** open `http://<host>:8090/dashboard` and sign in with `ADMIN_TOKEN`. See users, subscriptions, pending payments, and accounts; verify payments and activate subscriptions with buttons (no Telegram commands needed). Set `DASHBOARD_PORT` to change the port.
- **Subscription expiry:** users get a renewal reminder `EXPIRY_REMINDER_DAYS` before expiry and a notice when it lapses; expired users' workers stop automatically.

- the bot handles `/start`, `/link`, `/subscribe`, … from users
- the supervisor **auto-launches a worker** for every active account and **relaunches** any that die — no per-user manual steps
- a worker that can't log in **exits and is relaunched** with fresh credentials, so a user's `/link` fix takes effect by itself
- each worker notifies **its own user** on Telegram when the account connects (✅) or login fails (❌ "re-link")

**On a server, run it as an always-on service** so it survives reboots:
- Linux: a `systemd` unit running `python -m backend.service`
- Windows: NSSM or Task Scheduler running the same

### Per-account terminal provisioning (multi-account VPS)

Each account needs its own MT5 terminal (one terminal = one account). Provisioning clones a base MT5 install into a per-account portable folder and records its `terminal_path`.

Set on the VPS:
```
BASE_TERMINAL_DIR=C:\Program Files\MetaTrader 5   # a base install to clone
TERMINALS_ROOT=C:\zanzer\terminals                # where per-account copies go
AUTO_PROVISION=true                                # worker provisions on startup
```

- With `AUTO_PROVISION=true`, each worker clones + launches its account's terminal automatically on first run.
- Or provision manually: `python -m backend.provisioning <account_id>`, or admin Telegram `/provision <account_id>`.
- The clone skips `logs`/`history`/`Bases` to save space.

> **Caveats (be honest with yourself):** MT5 terminal first-run/login automation is finicky and platform-specific. This handles the file cloning, `terminal_path` bookkeeping, and portable launch — **validate with 2 accounts on a real Windows VPS** before scaling. Disk: one clone per account. Single-account/dev runs fine with `AUTO_PROVISION=false` (uses the shared terminal).

### Phase C — two-way Telegram bot (component)

You can also run the bot alone (without the supervisor):

```powershell
.\.venv\Scripts\python.exe -m backend.bot
```

Set `BOT_ADMIN_IDS` (your Telegram id) and, for payments, `CRYPTO_WALLET_ADDRESS` in `.env`.

**User commands:** `/start` (register + disclaimer), `/link` (guided MT5 connect — login → server → password → type; the password message is auto-deleted and stored encrypted), `/status`, `/risk`, `/lock`, `/unlock`, `/subscribe` (crypto instructions), `/paid <tx_hash>`, `/cancel`.
**Admin commands** (ids in `BOT_ADMIN_IDS`): `/pending`, `/verify <id>`, `/activate <telegram_id> <days> [plan]`, `/users`.

Built on httpx long-polling (no extra deps). **Only one bot process may poll a given token at a time.**

## Project structure

```
backend/
  main.py            # FastAPI app + lifespan (MT5 connect/shutdown)
  config.py          # settings from .env
  models.py          # Pydantic models
  logging_config.py  # logging (PRD principle 4: log everything)
  api/
    routes.py        # read-only HTTP routes
  scheduler.py       # background risk-check loop (V2)
  services/
    mt5_service.py         # MetaTrader5 read ops + (V2) close_position/close_all
    risk_service.py        # daily loss / trade count / streak / exposure math
    status_service.py      # assembles full status
    telegram_service.py    # outbound Telegram alerts + formatters
    lock_service.py        # (V2) file-based lock state
    enforcement_service.py # (V2) decide_actions (pure) + run_cycle (executor)
scripts/
  setup_telegram.py    # one-time Telegram bot/chat setup helper
mt5/
  ea/
    ZanzerGuardian.mq5 # (V2) Expert Advisor: enforces the lock on the terminal
    README.md          # EA setup + WebRequest whitelist instructions
```

## Roadmap

V1 Risk Manager (this) → V2 Risk Enforcement Engine → V3 Trading Journal → V4 Psychology Engine → V5 Performance Analytics → V6 Semi-Automated → V7 Multi-Agent. Full details in [CLAUDE.md](CLAUDE.md).
