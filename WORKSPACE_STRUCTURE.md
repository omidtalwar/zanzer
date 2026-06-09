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
