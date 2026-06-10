# Deploying Zanzer on a Windows VPS (Contabo)

Step-by-step to run the Zanzer service (bot + supervisor + dashboard + payments)
always-on on a Windows Server VPS.

> **⚠️ Windows version:** you need **Server 2016 or newer** (2019/2022 ideal).
> Modern Python (3.12+) does **not** run on Server 2012/2012 R2. When the setup
> email arrives, check the version (`winver`). If it's 2012, reinstall a newer
> image from the **Contabo control panel → your VPS → Reinstall** (pick Server
> 2019/2022) before continuing.

---

## 0. Connect to the VPS (RDP)
1. From the Contabo email, note **IP**, **Administrator**, **password**.
2. On your PC open **Remote Desktop Connection** (`mstsc`), enter the IP, then
   the Administrator credentials.
3. First thing: change the Administrator password to something strong
   (this box will hold users' encrypted credentials).

## 1. Prepare Windows
1. `winver` → confirm Server 2016+.
2. (Optional) Run Windows Update.
3. Install a browser if needed (download Chrome/Firefox via Edge).

## 2. Install prerequisites
1. **Python 3.14** (or 3.12): python.org → Windows installer →
   ✅ **"Add python.exe to PATH"** → Install.
   Verify in PowerShell: `py --version`.
2. **Git** (recommended, for pulling updates): git-scm.com → install with defaults.
3. **MetaTrader 5** terminal: install **one base copy** (from MetaQuotes or your
   broker). Note its folder — usually `C:\Program Files\MetaTrader 5`. This is
   your `BASE_TERMINAL_DIR`. Log in once manually to confirm it works.
4. **NSSM** (run the service as a Windows service): nssm.cc → download → unzip →
   put `nssm.exe` somewhere on PATH (e.g. `C:\Windows`).

## 3. Get the project onto the VPS
**Option A — Git (recommended):**
```powershell
cd C:\
git clone <your-private-repo-url> zanzer
cd C:\zanzer
```
**Option B — copy:** zip your local `zanzer` folder, paste into the RDP session
(RDP supports clipboard copy/paste of files), extract to `C:\zanzer`.

> `.env`, `data/`, `terminals/`, and `.venv/` are git-ignored — you'll create
> `.env` fresh on the VPS (next step).

## 4. Set up the app
```powershell
cd C:\zanzer
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Create **`.env`** (copy `.env.example` and fill in). Key values:
```
TELEGRAM_BOT_TOKEN=...           # @BotFather
TELEGRAM_CHAT_ID=...             # your chat id
BOT_ADMIN_IDS=...                # your telegram id(s)
ENCRYPTION_KEY=...               # generate a NEW one (below)
ADMIN_TOKEN=...                  # strong secret for the dashboard
CRYPTOPAY_TOKEN=...              # @CryptoBot Crypto Pay app
ENFORCEMENT_MODE=dry_run         # START SAFE; switch to live later
RISK_CHECK_INTERVAL_SECONDS=30
BASE_TERMINAL_DIR=C:\Program Files\MetaTrader 5
TERMINALS_ROOT=C:\zanzer\terminals
AUTO_PROVISION=true              # each account gets its own terminal
DATABASE_URL=sqlite+aiosqlite:///./data/zanzer.db
DASHBOARD_PORT=8090              # change if the port is taken on the box

# AI Performance Coach (Hermes) — enables /coach. Leave key blank to disable.
AI_PROVIDER=openai               # openai | claude
OPENAI_API_KEY=...               # from platform.openai.com (kept out of git)
OPENAI_MODEL=gpt-4o              # gpt-4o (best) or gpt-4o-mini (cheapest)
AI_COACH_ENABLED=true
# Optional, only if AI_PROVIDER=claude:
# ANTHROPIC_API_KEY=...
# ANTHROPIC_MODEL=claude-sonnet-4-6
```

> The coach can also be configured **at runtime from the dashboard** (provider,
> model, keys) — those override the `.env` values. The `.env` keys are just the
> startup defaults. Requires `openai` (and `anthropic` if using Claude) — both
> are already in `requirements.txt`, so step 4 installs them.
Generate a fresh encryption key:
```powershell
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

(Optional, for PostgreSQL instead of SQLite: install PostgreSQL, set
`DATABASE_URL=postgresql+asyncpg://user:pass@localhost/zanzer`,
`pip install asyncpg`, then `.\.venv\Scripts\python.exe -m alembic upgrade head`.)

## 5. Smoke test (before installing as a service)
```powershell
.\.venv\Scripts\python.exe -m backend.service
```
- The log should show *Bot started*, *Supervisor started*, *CryptoPay poller started*, *Dashboard(:8090)*.
- Message your bot `/start` on Telegram to confirm it responds.
- Open `http://localhost:8090/dashboard` in the VPS browser, sign in with `ADMIN_TOKEN`.
- Press **Ctrl+C** to stop once it's confirmed working.

## 6. Run it always-on (NSSM)
```powershell
nssm install Zanzer "C:\zanzer\.venv\Scripts\python.exe" "-m backend.service"
nssm set Zanzer AppDirectory C:\zanzer
nssm set Zanzer AppStdout C:\zanzer\data\service.log
nssm set Zanzer AppStderr C:\zanzer\data\service.log
nssm set Zanzer Start SERVICE_AUTO_START
nssm start Zanzer
```
- Survives reboots and restarts on crash.
- Manage: `nssm restart Zanzer`, `nssm stop Zanzer`, `nssm status Zanzer`.
- Logs: `C:\zanzer\data\service.log`.

## 7. Dashboard access (security)
- Easiest & safest: use the dashboard **on the VPS via RDP** at `http://localhost:8090/dashboard`.
- If you want remote access, **don't expose port 8090 raw** (it's plain HTTP).
  Put a reverse proxy (Caddy/Nginx) with HTTPS in front, or use an SSH/Cloudflare
  tunnel. The `ADMIN_TOKEN` guards it but should ride over HTTPS.

## 8. Go live carefully
1. Keep `ENFORCEMENT_MODE=dry_run` at first. Link **your own** account, watch the
   logs/`/status` for a day.
2. When confident, set `ENFORCEMENT_MODE=live` in `.env` and `nssm restart Zanzer`.
3. Onboard a couple of real users, watch the dashboard.

## Maintenance
- **Update code:** `cd C:\zanzer; git pull; .\.venv\Scripts\python.exe -m pip install -r requirements.txt; nssm restart Zanzer`
- **Backups:** snapshot the VPS (Contabo), and back up `C:\zanzer\data\` (DB) +
  your `.env` (secrets) somewhere safe.
- **Capacity:** ~12–15 monitored accounts on 8 GB. Watch RAM/CPU in Task Manager.

## ⚠️ Known limitation to fix before multi-account
Running **multiple** MT5 terminals reliably needs **every** account to have its
own provisioned terminal with an explicit path (no shared default). Single
account works today; the multi-account binding fix should be done + tested here
before onboarding several users. (See conversation notes.)
