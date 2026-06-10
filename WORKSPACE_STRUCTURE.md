# Workspace Structure

The following tree shows the project folder layout for `zanzer`, excluding the `.venv` package contents.

## Root files
- `.claude/settings.local.json`
- `.env`
- `.env.example`
- `.gitignore`
- `alembic.ini`
- `CLAUDE.md`
- `README.md`
- `requirements.txt`

## Root directories
- `.git/`
- `.venv/`
- `backend/`
- `data/`
- `docs/`
- `migrations/`
- `mt5/`
- `scripts/`
- `terminals/`
- `tests/`

---

## `backend/`
- `__init__.py`
- `admin_app.py`
- `config.py`
- `expiry.py`
- `logging_config.py`
- `main.py`
- `models.py`
- `provisioning.py`
- `repositories.py`
- `run_account.py`
- `scheduler.py`
- `schemas.py`
- `security.py`
- `service.py`
- `supervisor.py`
- `validate_account.py`
- `worker.py`

### `backend/api/`
- `__init__.py`
- `admin_routes.py`
- `dashboard_routes.py`
- `routes.py`
- `user_routes.py`

### `backend/bot/`
- `__init__.py`
- `__main__.py`
- `app.py`
- `client.py`
- `dispatcher.py`
- `validation.py`

### `backend/broker/`
- `__init__.py`
- `base.py`
- `local_mt5.py`
- `mock.py`

### `backend/db/`
- `__init__.py`
- `base.py`
- `models.py`
- `session.py`

### `backend/payments/`
- `__init__.py`
- `cryptopay.py`
- `flow.py`
- `stars.py`

### `backend/services/`
- `__init__.py`
- `enforcement_service.py`
- `lock_service.py`
- `mt5_service.py`
- `risk_service.py`
- `status_service.py`
- `telegram_service.py`

---

## `data/`
- `lock_state.json`
- `zanzer.db`
- `zanzer.db-shm`
- `zanzer.db-wal`

---

## `docs/`
- `DEPLOY_WINDOWS_VPS.md`
- `SAAS_PLAN.md`

---

## `migrations/`
- `env.py`
- `script.py.mako`

### `migrations/versions/`
- `cd1662e2b94e_initial_schema.py`
- `e42e0b52e4cf_add_max_daily_loss_usd.py`

---

## `mt5/ea/`
- `README.md`
- `ZanzerGuardian.mq5`

---

## `scripts/`
- `__init__.py`
- `send_guide.py`
- `setup_telegram.py`

---

## `tests/`
- `__init__.py`
- `test_bot_dispatcher.py`
- `test_enforcement_service.py`
- `test_expiry.py`
- `test_provisioning.py`
- `test_repositories.py`
- `test_risk_service.py`
- `test_supervisor.py`
- `test_worker.py`


# Copy this file to .env and fill in real values. Never commit .env.

# --- MetaTrader 5 ---
# Leave MT5_LOGIN blank to attach to an already-running, logged-in terminal.
# Fill all three to have the app log in to a specific account.
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=
# Optional: full path to terminal64.exe if not auto-detected
MT5_TERMINAL_PATH=
BROKER_UTC_OFFSET_HOURS=3
BASE_TERMINAL_DIR=C:\Program Files\MetaTrader 5
TERMINALS_ROOT=terminals
AUTO_PROVISION=false

# --- Telegram ---
# Create a bot via @BotFather to get the token.
# Get your chat id by messaging the bot then calling getUpdates, or via @userinfobot.
TELEGRAM_BOT_TOKEN=8980008444:AAFgesyOrWc5WoW-Wvy3EHwlDNm73_pBkP4
TELEGRAM_CHAT_ID=5625070857
BOT_ADMIN_IDS=5625070857
CRYPTO_WALLET_ADDRESS=
CRYPTO_CURRENCY=USDT
PRICE_MONTHLY=20
PRICE_QUARTERLY=50
CRYPTOPAY_TOKEN=593226:AAplAsaGEnQC4g6IUVyTOp5rkqY24LqMDFt
CRYPTOPAY_TESTNET=false
CRYPTOPAY_ASSET=USDT
PRICE_MONTHLY_STARS=500
PRICE_QUARTERLY_STARS=1200

# --- Risk Rules (defaults from PRD; adjust as needed) ---
MAX_TRADES_PER_DAY=2
MAX_DAILY_LOSS_PCT=5
MAX_RISK_PER_TRADE_PCT=4
MAX_CONSECUTIVE_LOSSES=2
MAX_ACCOUNT_EXPOSURE_PCT=5

# --- Enforcement (V2) ---
ENFORCEMENT_MODE=live
RISK_CHECK_INTERVAL_SECONDS=30
LOCK_STATE_PATH=data/lock_state.json
ADMIN_TOKEN=zanzer-admin-2026
DASHBOARD_PORT=8090
ENCRYPTION_KEY=8DAUWVXs0OsGAb3HIaUBZeBbRj52ONeuxto9CVhdkdw=

# --- AI Performance Coach (Hermes) ---
# Using OpenAI for now. Paste your key after the = (no quotes, no spaces).
AI_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
AI_COACH_ENABLED=true

# --- App ---
APP_ENV=development
