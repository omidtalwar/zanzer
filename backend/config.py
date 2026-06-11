"""Application configuration, loaded from environment / .env file.

Risk rules default to the PRD values but can be overridden via .env.
The Risk Engine (V2) will read these; V1 only displays/uses them for context.
"""
from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Treat blank env values (e.g. `MT5_LOGIN=` in .env) as "unset" so optional
    # fields fall back to their defaults instead of failing type parsing.
    @field_validator("*", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v):
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    # --- MetaTrader 5 ---
    mt5_login: int | None = None
    mt5_password: str | None = None
    mt5_server: str | None = None
    mt5_terminal_path: str | None = None
    # Broker server time offset from UTC, in hours (e.g. 3 for GMT+3). MT5 deal
    # timestamps are in server time. Leave blank to auto-detect from a live tick;
    # set explicitly for reliability (e.g. over weekends when ticks are stale).
    broker_utc_offset_hours: float | None = None

    # --- Terminal farm provisioning (multi-account on a Windows VPS) ---
    # Folder of a base MT5 install to clone per account (contains terminal64.exe).
    base_terminal_dir: str | None = None
    # Where per-account portable terminal copies are created.
    terminals_root: str = "terminals"
    # If true, the worker auto-provisions a terminal for its account on startup.
    auto_provision: bool = False

    # --- Telegram ---
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    # Comma-separated Telegram ids allowed to use admin bot commands.
    bot_admin_ids: str | None = None

    # --- Subscription / crypto payments (bot) ---
    crypto_wallet_address: str | None = None
    crypto_currency: str = "USDT"
    price_monthly: float = 20.0
    price_quarterly: float = 50.0
    # CryptoBot / CryptoPay (auto-confirmed crypto). Token from @CryptoBot →
    # Crypto Pay → Create App. Leave blank to fall back to manual wallet flow.
    cryptopay_token: str | None = None
    cryptopay_testnet: bool = False
    cryptopay_asset: str = "USDT"
    # Telegram Stars (native in-app payments). Prices in whole Stars (XTR).
    price_monthly_stars: int = 500
    price_quarterly_stars: int = 1200

    # --- Risk rules (PRD defaults) ---
    max_trades_per_day: int = 2
    max_daily_loss_pct: float = 5.0
    max_daily_loss_usd: float = 0.0  # 0 = use % only
    max_risk_per_trade_pct: float = 4.0
    max_consecutive_losses: int = 2
    max_account_exposure_pct: float = 5.0

    # --- Enforcement (V2) ---
    # "dry_run" = detect + alert + log intended actions, but take NO real
    # trading action. "live" = actually close positions and lock the account.
    enforcement_mode: str = "dry_run"
    # How often the background loop checks risk (seconds). 0 disables the loop.
    risk_check_interval_seconds: int = 30
    # Where the persisted lock state lives (relative to project root).
    lock_state_path: str = "data/lock_state.json"

    # --- Database (Phase A, multi-user) ---
    # Dev default: SQLite (zero install). Production: PostgreSQL, e.g.
    #   postgresql+asyncpg://user:pass@host:5432/zanzer
    database_url: str = "sqlite+aiosqlite:///./data/zanzer.db"
    # Fernet key for encrypting stored MT5 credentials. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # If unset, a dev key is derived (NOT safe for production).
    encryption_key: str | None = None
    # Shared secret guarding /admin/* endpoints (sent as X-Admin-Token header).
    admin_token: str | None = None
    # Free trial length granted on registration. 0 = no trial (users start
    # inactive and must pay or be activated by an admin).
    trial_days: int = 0
    # Admin web dashboard port (served by the all-in-one service, DB-only).
    dashboard_port: int = 8090
    # Days before expiry to send a renewal reminder.
    expiry_reminder_days: int = 3

    # --- AI Performance Coach (Hermes) ---
    # These are DEFAULTS only. The effective config is admin-editable at runtime
    # via the dashboard (stored in the app_settings table); see
    # repositories.get_ai_config which merges DB overrides over these env values.
    ai_provider: str = "openai"          # "openai" | "claude"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"
    ai_coach_enabled: bool = True
    # Max /coach calls per user per UTC day (cost protection). 0 = unlimited.
    coach_daily_limit: int = 10

    # Anti-gaming: when True, LOOSENING a risk rule is deferred (tightening is
    # always instant) so users can't relax limits in the heat of the moment.
    defer_loosening: bool = True
    # How long a loosening change waits before it takes effect, in hours.
    loosening_delay_hours: int = 5
    # A trade whose net P&L is within ±this many units of the account currency
    # is treated as BREAKEVEN (a scratch) — NOT a loss — for the consecutive-loss
    # rule. e.g. 1.0 → a -$0.40 scratch won't extend a losing streak.
    breakeven_band: float = 1.0

    # --- Marketing channel (aggregate, anonymized daily stats) ---
    # Channel @username (e.g. @zanzerhq) or numeric id (e.g. -1001234567890).
    # The bot must be an ADMIN of the channel. Leave blank to disable posting.
    marketing_channel_id: str | None = None
    # UTC hour (0–23) to post the daily community summary.
    channel_post_hour_utc: int = 21
    # Post anonymized real-time enforcement events (locks, revenge flags) to the
    # channel as social proof. Strictly anonymized — no name, no $ amount.
    channel_post_events: bool = True

    # --- App ---
    app_env: str = "development"

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def admin_ids(self) -> set[int]:
        if not self.bot_admin_ids:
            return set()
        out: set[int] = set()
        for part in self.bot_admin_ids.split(","):
            part = part.strip()
            if part:
                try:
                    out.add(int(part))
                except ValueError:
                    pass
        return out

    @property
    def is_live_enforcement(self) -> bool:
        return self.enforcement_mode.strip().lower() == "live"


settings = Settings()
