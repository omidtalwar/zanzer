"""Bot command dispatcher — pure logic, framework-free.

`send`/`delete` are injected so this is unit-testable without hitting Telegram.
Holds a small in-memory FSM for the multi-step /link flow (fine for a single
long-polling process).

Commands:
  user : /start /menu /status /link /risk /lock /unlock /subscribe /paid /cancel
  admin: /pending /verify <id> /activate <telegram_id> <days> [plan] /users
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from html import escape

from backend import repositories as repo
from backend.config import settings
from backend.db.session import SessionLocal
from backend.logging_config import get_logger
from backend.services.analytics_service import compute_metrics, fmt_pf, fmt_rr
from backend.services.psychology_service import score_emoji, score_label
from backend.services import hermes_service

log = get_logger("bot")

DISCLAIMER = (
    "<b>🛡️ Zanzer — AI Trading Guardian</b>\n"
    "I protect your trading capital by enforcing <b>your own</b> risk rules on MetaTrader 5.\n\n"
    "<b>What I do</b>\n"
    "• Watch your account &amp; risk limits (daily loss, trades, exposure)\n"
    "• Lock / close to stop you when you break your own rules\n\n"
    "<b>What I do NOT do</b>\n"
    "• I do <b>NOT</b> open or place trades for you\n"
    "• I do <b>NOT</b> send buy/sell signals or tips\n"
    "• I do <b>NOT</b> give financial advice or manage your money\n"
    "• I never trade on your behalf — <b>you stay in full control</b>\n\n"
    "⚠️ <i>Trading is risky and you are fully responsible for your account and "
    "decisions. Risk management always overrides everything. This is not financial advice.</i>"
)

# --- Inline menu: short home + tappable sections (set via callback buttons) ---

def _ik(rows: list[list[tuple[str, str]]]) -> dict:
    """Build a Telegram inline keyboard from rows of (label, callback_data)."""
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for (t, d) in row] for row in rows]}


def _home_keyboard(is_admin: bool) -> dict:
    rows = [
        [("📊 Performance", "m:perf"), ("📓 Journal & Coach", "m:journal")],
        [("⚙️ Settings", "m:settings"), ("💳 Subscription", "m:sub")],
    ]
    if is_admin:
        rows.append([("🔧 Admin", "m:admin")])
    return _ik(rows)


_BACK_KB = _ik([[("⬅️ Back to menu", "m:home")]])

_BACK_ROW = [("⬅️ Back to menu", "m:home")]

# Per-section keyboards: tappable buttons that RUN the command (callback "c:<cmd>").
_SECTION_KB = {
    "perf": _ik([
        [("📊 Status", "c:status"), ("📅 Today", "c:today")],
        [("🗓 Yesterday", "c:yesterday"), ("📈 Weekly", "c:weekly")],
        [("📉 Monthly", "c:monthly"), ("🏆 All-time", "c:performance")],
        _BACK_ROW,
    ]),
    "journal": _ik([
        [("📓 Journal", "c:journal"), ("📑 Trades", "c:trades")],
        [("🤖 Coach", "c:coach")],
        _BACK_ROW,
    ]),
    "settings": _ik([
        [("🛡️ View rules", "c:risk"), ("⚙️ Set rules", "c:setrisk")],
        [("🔗 Link MT5", "c:link"), ("🔒 Lock today", "c:lock")],
        _BACK_ROW,
    ]),
    "sub": _ik([
        [("💳 Subscribe", "c:subscribe"), ("⭐ Stars", "c:stars")],
        _BACK_ROW,
    ]),
    "admin": _ik([
        [("💰 Pending", "c:pending"), ("👥 Users", "c:users")],
        [("🖥 Accounts", "c:accounts")],
        _BACK_ROW,
    ]),
}

# Section bodies — commands stay tappable in Telegram.
_SECTION_TEXT = {
    "perf": (
        "📊 <b>Performance</b>\n"
        "/status — account, risk &amp; emotion score\n"
        "/today — today's trades &amp; P&amp;L\n"
        "/yesterday — yesterday's recap\n"
        "/weekly — weekly report (/weekly 30 for 30 days)\n"
        "/monthly — equity, drawdown &amp; emotion charts\n"
        "/performance — all-time stats"
    ),
    "journal": (
        "📓 <b>Journal &amp; Coaching</b>\n"
        "/journal — view / fill trade journals\n"
        "/trades — recent trade history\n"
        "/coach — AI psychology review 🤖"
    ),
    "settings": (
        "⚙️ <b>Settings</b>\n"
        "/risk — view your risk rules\n"
        "/setrisk — change your rules (guided)\n"
        "/link — connect / relink your MT5 account\n"
        "/lock — lock yourself for today\n"
        "/explain — record why (when locked)\n\n"
        "<i>A lock can't be removed on demand — it protects you from emotional "
        "trading and clears the next trading day.</i>"
    ),
    "sub": (
        "💳 <b>Subscription</b>\n"
        "/subscribe — pay with crypto (auto)\n"
        "/stars — pay with Telegram Stars ⭐\n"
        "/paid &lt;tx&gt; — submit a manual payment"
    ),
    "admin": (
        "🔧 <b>Admin</b>\n"
        "/pending — pending payments\n"
        "/verify &lt;id&gt; — verify a payment\n"
        "/activate &lt;tid&gt; &lt;days&gt; [plan] — activate a user\n"
        "/users — list users\n"
        "/accounts — list MT5 accounts\n"
        "/provision &lt;id&gt; — provision a terminal\n"
        "/creds &lt;id&gt; — get an account's MT5 login + password (self-deletes)\n"
        "/broadcast &lt;msg&gt; — announce to all users\n"
        "/channelnow — post daily community summary to the channel"
    ),
}


def _esc(v) -> str:
    return escape(str(v))


def _fmt_duration(secs: int | None) -> str:
    if secs is None or secs < 0:
        return "open"
    if secs < 60:
        return f"{secs}s"
    m = secs // 60
    if m < 60:
        return f"{m}m"
    h, rem = divmod(m, 60)
    return f"{h}h {rem}m" if rem else f"{h}h"


def _trades_to_dicts(trades) -> list[dict]:
    """ORM Trade rows → plain dicts for analytics_service.compute_metrics."""
    return [
        {"profit": t.profit, "symbol": t.symbol, "duration_s": t.duration_s}
        for t in trades
    ]


def _ago(ts: datetime) -> str:
    if ts is None:
        return "never"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    secs = (datetime.now(timezone.utc) - ts).total_seconds()
    if secs < 60:
        return f"{int(secs)}s ago"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    return f"{int(secs // 3600)}h ago"


class BotDispatcher:
    def __init__(self, send, delete=None, validator=None, provisioner=None,
                 invoicer=None, star_invoicer=None, edit=None, answer_cbq=None,
                 session_factory=SessionLocal) -> None:
        # Wrap send so plain messages end with a /menu footer. Messages that
        # carry an inline keyboard (the menu itself) skip the footer.
        _raw_send = send
        async def _send_with_help(chat_id, text, reply_markup=None):
            if reply_markup is not None:
                return await _raw_send(chat_id, text, reply_markup=reply_markup)
            if not str(text).rstrip().endswith("/menu"):
                text = str(text) + "\n\n/menu"
            return await _raw_send(chat_id, text)
        self.send = _send_with_help
        self.delete = delete        # async (chat_id, message_id) -> bool | None
        self.validator = validator  # async (account_id) -> (ok: bool, message: str)
        self.provisioner = provisioner  # async (account_id) -> str
        self.invoicer = invoicer    # async (telegram_id, plan) -> (ok: bool, url_or_msg)
        self.star_invoicer = star_invoicer  # async (telegram_id, plan) -> (ok, msg)
        self.edit = edit            # async (chat_id, message_id, text, reply_markup=)
        self.answer_cbq = answer_cbq  # async (callback_query_id) -> bool
        self._sf = session_factory
        self.states: dict[int, dict] = {}
        # /coach cost guard: telegram_id -> (utc_date_str, count_today)
        self._coach_usage: dict[int, tuple[str, int]] = {}

    def _coach_quota_ok(self, telegram_id: int) -> bool:
        """Per-user daily cap on /coach (0 = unlimited). Increments on success."""
        limit = settings.coach_daily_limit
        if not limit or limit <= 0:
            return True
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        day, count = self._coach_usage.get(telegram_id, (today, 0))
        if day != today:
            count = 0
        if count >= limit:
            return False
        self._coach_usage[telegram_id] = (today, count + 1)
        return True

    # ------------------------------------------------------------------ entry
    async def handle(self, *, telegram_id: int, username: str | None,
                     text: str | None, message_id: int | None = None,
                     photo_file_id: str | None = None) -> None:
        text = (text or "").strip()

        # A photo → attach it as a trade screenshot (unless it carries a command).
        if photo_file_id and not text.startswith("/"):
            await self._handle_photo(telegram_id, photo_file_id, text)
            return

        # Mid-conversation input (not a command) → feed the active flow.
        state = self.states.get(telegram_id)
        if state and not text.startswith("/"):
            flow = state.get("flow")
            if flow == "setrisk":
                await self._setrisk_step(telegram_id, text)
            elif flow == "entry_journal":
                await self._entry_journal_step(telegram_id, text)
            elif flow == "exit_journal":
                await self._exit_journal_step(telegram_id, text)
            else:
                await self._link_step(telegram_id, text, message_id)
            return

        if not text.startswith("/"):
            # The journal prompt is sent by the WORKER process, which can't set
            # this (bot-process) FSM state. So a free-text reply with no active
            # flow may be the user answering a worker-sent journal prompt —
            # resume it from the DB (most recently prompted unjournaled trade).
            if await self._maybe_resume_journal(telegram_id, text):
                return
            await self.send(telegram_id, "Type /menu to see what I can do.")
            return

        cmd, _, arg = text.partition(" ")
        cmd = cmd.lstrip("/").split("@", 1)[0].lower()
        arg = arg.strip()

        if cmd == "cancel":
            self.states.pop(telegram_id, None)
            await self.send(telegram_id, "Cancelled.")
            return

        handlers = {
            "start": self._start, "agree": self._agree, "menu": self._menu, "status": self._status,
            "today": self._today, "yesterday": self._yesterday,
            "weekly": self._weekly, "performance": self._performance,
            "trades": self._trades, "journal": self._journal, "explain": self._explain,
            "coach": self._coach, "monthly": self._monthly,
            "link": self._link_start, "risk": self._risk, "setrisk": self._setrisk,
            "lock": self._lock, "unlock": self._unlock, "subscribe": self._subscribe,
            "paid": self._paid, "stars": self._stars,
            # admin
            "pending": self._admin_pending, "verify": self._admin_verify,
            "activate": self._admin_activate, "users": self._admin_users,
            "accounts": self._admin_accounts, "provision": self._admin_provision,
            "broadcast": self._admin_broadcast, "channelnow": self._admin_channelnow,
            "creds": self._admin_creds,
        }
        handler = handlers.get(cmd)
        if handler is None:
            await self.send(telegram_id, "Unknown command. /menu")
            return
        await handler(telegram_id, username, arg)

    def _is_admin(self, telegram_id: int) -> bool:
        return telegram_id in settings.admin_ids

    # ------------------------------------------------------------- user cmds
    async def _start(self, telegram_id, username, arg) -> None:
        async with self._sf() as session:
            existed = await repo.get_user(session, telegram_id) is not None
            user = await repo.register_user(session, telegram_id, username)
            accepted = user.tos_accepted_at is not None
            active = repo.subscription_is_active(user.subscription)
        # Notify admins when a brand-new user joins the bot.
        if not existed:
            handle = f"@{username}" if username else "—"
            for admin_id in settings.admin_ids:
                if admin_id == telegram_id:
                    continue
                await self.send(
                    admin_id,
                    f"🆕 <b>New user joined</b>\n"
                    f"Telegram id: <code>{telegram_id}</code>\n"
                    f"Username: {_esc(handle)}",
                )
        if not accepted:
            tail = (
                "By continuing you confirm you have read and accept these terms.\n"
                "👉 Reply <b>/agree</b> to accept and start."
            )
            await self.send(telegram_id, f"{DISCLAIMER}\n\n{tail}")
            return
        if active:
            tail = "Your subscription is active. /link your MT5 account, then /status."
        else:
            tail = (
                "Your subscription is <b>inactive</b>.\n"
                "To start protecting your account:\n"
                "1) /link your MT5 account\n"
                "2) /subscribe (crypto) or /stars to activate\n\n"
                "/menu for all commands."
            )
        await self.send(telegram_id, f"{DISCLAIMER}\n\n{tail}")

    async def _agree(self, telegram_id, username, arg) -> None:
        async with self._sf() as session:
            user = await repo.register_user(session, telegram_id, username)
            await repo.accept_tos(session, user)
        await self.send(
            telegram_id,
            "✅ <b>Terms accepted.</b> Thank you.\n\nNext:\n"
            "1) /link your MT5 account\n"
            "2) /setrisk to set your risk rules (I'll guide you)\n"
            "3) /subscribe (crypto) or /stars to activate protection\n\n"
            "/menu for all commands.",
        )

    async def _home_text(self, telegram_id: int) -> str:
        """Short, contextual main-menu text reflecting subscription + link state."""
        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user is None:
                return "🛡️ <b>Zanzer</b>\nSend /start to begin."
            active = repo.subscription_is_active(user.subscription)
            linked = bool(user.accounts)
            login = user.accounts[0].login if linked else None
        lines = ["🛡️ <b>Zanzer — Menu</b>", ""]
        lines.append(f"Subscription: <b>{'🟢 active' if active else '🔴 inactive'}</b>")
        lines.append(f"MT5: <b>{f'🟢 linked ({login})' if linked else '⚪ not linked'}</b>")
        if not linked:
            lines.append("\n👉 Start in <b>⚙️ Settings → /link</b> to connect your account.")
        elif not active:
            lines.append("\n👉 Tap <b>💳 Subscription</b> to activate protection.")
        lines.append("\nTap a section below 👇")
        return "\n".join(lines)

    async def _menu(self, telegram_id, username, arg) -> None:
        text = await self._home_text(telegram_id)
        await self.send(telegram_id, text, reply_markup=_home_keyboard(self._is_admin(telegram_id)))

    async def handle_callback(self, *, telegram_id: int, username: str | None,
                              data: str, callback_query_id: str,
                              message_id: int | None = None) -> None:
        """Handle inline-menu button taps (callback queries).

        Two kinds of callback data:
          m:<section>  → navigate (edit the menu message in place)
          c:<command>  → run that command directly (sends its normal output)
        """
        if self.answer_cbq:
            try:
                await self.answer_cbq(callback_query_id)
            except Exception:  # noqa: BLE001
                pass

        kind, _, key = data.partition(":")

        # Direct command buttons.
        if kind == "c":
            await self._run_command_button(telegram_id, username, key)
            return

        # Section navigation (kind == "m").
        if key == "home" or not key:
            text = await self._home_text(telegram_id)
            kb = _home_keyboard(self._is_admin(telegram_id))
        elif key == "admin" and not self._is_admin(telegram_id):
            text, kb = "Not authorized.", _BACK_KB
        elif key in _SECTION_TEXT:
            text, kb = _SECTION_TEXT[key], _SECTION_KB.get(key, _BACK_KB)
        else:
            text = await self._home_text(telegram_id)
            kb = _home_keyboard(self._is_admin(telegram_id))
        if self.edit and message_id is not None:
            if await self.edit(telegram_id, message_id, text, reply_markup=kb):
                return
        await self.send(telegram_id, text, reply_markup=kb)

    async def _run_command_button(self, telegram_id, username, cmd: str) -> None:
        """Map a c:<cmd> button to its existing command handler (no args)."""
        handlers = {
            "status": self._status, "today": self._today, "yesterday": self._yesterday,
            "weekly": self._weekly, "monthly": self._monthly, "performance": self._performance,
            "journal": self._journal, "trades": self._trades, "coach": self._coach,
            "risk": self._risk, "setrisk": self._setrisk, "link": self._link_start,
            "lock": self._lock, "subscribe": self._subscribe, "stars": self._stars,
            "pending": self._admin_pending, "users": self._admin_users,
            "accounts": self._admin_accounts,
        }
        handler = handlers.get(cmd)
        if handler is None:
            await self.send(telegram_id, "Unknown action. /menu")
            return
        await handler(telegram_id, username, "")

    async def _status(self, telegram_id, username, arg) -> None:
        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user is None:
                await self.send(telegram_id, "You're not registered yet. Send /start.")
                return
            active = repo.subscription_is_active(user.subscription)
            rs = user.risk_settings
            accounts = user.accounts
            lock = await repo.get_lock(session, user.id)
            snap = await repo.get_snapshot(session, user.id)
            unjournaled = await repo.get_unjournaled_trades(session, user.id)
            emotion_row = await repo.get_latest_emotion_score(session, user.id)
        lines = [
            "<b>📊 Your Status</b>",
            f"Subscription: <b>{'active' if active else 'inactive'}</b> ({_esc(user.subscription.status)})",
        ]
        if user.subscription.expires_at:
            lines.append(f"Expires: {_esc(str(user.subscription.expires_at)[:10])}")
        if accounts:
            a = accounts[0]
            lines.append(f"MT5: <b>{_esc(a.login)}</b> @ {_esc(a.server)} ({_esc(a.status)})")
            if a.status == "needs_terminal":
                lines.append(
                    "⚠️ <i>This account is waiting for its own MT5 terminal "
                    "(provisioning) before monitoring can start.</i>"
                )
        else:
            lines.append("MT5: <i>not linked</i> — use /link")
        lines.append(f"Trading: {'🔒 LOCKED' if lock.locked else '🟢 unlocked'}")

        # Live data from the worker's latest snapshot.
        if snap is not None:
            lines.append("")
            lines.append(f"💰 Balance: <b>{snap.balance:,.2f} {_esc(snap.currency)}</b>")
            lines.append(f"📈 Equity: <b>{snap.equity:,.2f} {_esc(snap.currency)}</b>")
            lines.append(f"Open trades: <b>{snap.open_positions}</b> (floating {snap.floating_pnl:+,.2f})")
            lines.append(
                f"Today's PnL: <b>{snap.daily_loss:+,.2f} {_esc(snap.currency)}</b> "
                f"({snap.daily_loss_pct:+.2f}%)"
            )
            lines.append(
                f"Trades today: {snap.trades_today}/{rs.max_trades_per_day} · "
                f"Streak losses: {snap.consecutive_losses}/{rs.max_consecutive_losses} · "
                f"Exposure: {snap.exposure_pct:.2f}%"
            )
            lines.append(f"Risk: {'🔴 LIMIT HIT' if snap.any_limit_hit else '🟢 OK'}")
            lines.append(f"<i>Updated {_ago(snap.updated_at)}</i>")
        if emotion_row is not None:
            em = score_emoji(emotion_row.score)
            lines.append(
                f"🧠 Emotion score: {em} <b>{emotion_row.score}/100</b> "
                f"— {_esc(score_label(emotion_row.score))}"
            )
        if snap is None:
            lines.append(
                f"\nRisk rules: {rs.max_trades_per_day} trades/day, {rs.max_daily_loss_pct:.0f}% "
                f"daily loss, {rs.max_consecutive_losses} losses, {rs.max_account_exposure_pct:.0f}% exposure"
            )
            lines.append("<i>Live balance/PnL appears once your account worker connects (~1 min).</i>")
        # Journal badge — shown prominently so traders can't ignore it.
        if unjournaled:
            lines.append("")
            lines.append(
                f"📓 <b>⚠️ {len(unjournaled)} unjournaled trade(s)</b> — "
                f"use /journal to fill them in."
            )
        await self.send(telegram_id, "\n".join(lines))

    async def _yesterday(self, telegram_id, username, arg) -> None:
        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user is None:
                await self.send(telegram_id, "You're not registered yet. Send /start.")
                return
            snap = await repo.get_snapshot(session, user.id)

        if snap is None or not getattr(snap, "yesterday_json", None):
            await self.send(
                telegram_id,
                "📅 <b>Yesterday's Performance</b>\n\n"
                "<i>No data yet — this appears after the worker completes its first "
                "cycle on a day following at least one closed trade.</i>",
            )
            return

        try:
            trades: list[dict] = json.loads(snap.yesterday_json)
        except (ValueError, TypeError):
            await self.send(telegram_id, "Could not read yesterday's data. Try again later.")
            return

        if not trades:
            await self.send(
                telegram_id,
                "📅 <b>Yesterday's Performance</b>\n\n<i>No completed trades yesterday.</i>",
            )
            return

        wins = [t for t in trades if t["profit"] > 0]
        losses = [t for t in trades if t["profit"] <= 0]
        total_pnl = round(sum(t["profit"] for t in trades), 2)
        gross_profit = round(sum(t["profit"] for t in wins), 2)
        gross_loss = round(sum(t["profit"] for t in losses), 2)
        win_rate = round(len(wins) / len(trades) * 100)

        lines = [
            "📅 <b>Yesterday's Performance</b>",
            "",
            f"Trades: <b>{len(trades)}</b> "
            f"({'✅' * len(wins)}{'❌' * len(losses)})  "
            f"Win rate: <b>{win_rate}%</b>",
            f"Net P&amp;L: <b>{total_pnl:+.2f}</b>  "
            f"(profit {gross_profit:+.2f} / loss {gross_loss:+.2f})",
            "",
        ]

        # Sessions summary
        session_counts: dict[str, int] = {}
        for t in trades:
            s = t.get("session", "Unknown")
            session_counts[s] = session_counts.get(s, 0) + 1
        sessions_str = "  ".join(f"{s}: {n}" for s, n in session_counts.items())
        lines.append(f"Sessions: {_esc(sessions_str)}")
        lines.append("")
        lines.append("<b>Trade details</b>")

        for i, t in enumerate(trades, 1):
            profit = t["profit"]
            symbol = _esc(t.get("symbol", "?"))
            direction = _esc(t.get("direction", "?"))
            session = _esc(t.get("session", "?"))
            dur = _fmt_duration(t.get("duration_s"))
            emoji = "✅" if profit > 0 else "❌"
            lines.append(
                f"{i}. {emoji} {symbol} {direction} "
                f"<b>{profit:+.2f}</b> | {dur} | {session}"
            )

        await self.send(telegram_id, "\n".join(lines))

    # ----------------------------------------------------------------- /today
    async def _today(self, telegram_id, username, arg) -> None:
        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user is None:
                await self.send(telegram_id, "Send /start first.")
                return
            today_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
            snap = await repo.get_snapshot(session, user.id)
            emotion_row = await repo.get_latest_emotion_score(session, user.id)
            trades = await repo.get_trades_in_range(
                session, user.id,
                since=datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0),
                until=datetime.now(tz=timezone.utc),
            )

        lines = [f"📅 <b>Today — {today_str}</b>", ""]

        # Emotion score block.
        if emotion_row:
            em = score_emoji(emotion_row.score)
            lines.append(f"🧠 Emotion score: {em} <b>{emotion_row.score}/100</b> — {_esc(score_label(emotion_row.score))}")
            lines.append("")

        # Account snapshot block.
        if snap:
            lines.append(f"💰 P&amp;L: <b>{snap.daily_loss:+,.2f} {_esc(snap.currency)}</b> ({snap.daily_loss_pct:+.2f}%)")
            lines.append(f"Trades: {snap.trades_today} | Open: {snap.open_positions} | Exposure: {snap.exposure_pct:.1f}%")
            lines.append("")

        # Trade list + standard metrics.
        m = compute_metrics(_trades_to_dicts(trades))
        if m is not None:
            lines.append(
                f"Completed trades: <b>{m.total_trades}</b> | Win rate: <b>{m.win_rate:g}%</b> · "
                f"PF: <b>{fmt_pf(m.profit_factor)}</b> · Avg RR: <b>{fmt_rr(m.payoff_ratio)}</b>"
            )
            for t in trades:
                profit_str = f"{t.profit:+.2f}" if t.profit is not None else "open"
                if t.status == "open":
                    emoji = "🔵"
                elif (t.profit or 0) > 0:
                    emoji = "✅"
                elif (t.profit or 0) < 0:
                    emoji = "❌"
                else:
                    emoji = "➖"  # breakeven
                dur = _fmt_duration(t.duration_s)
                lines.append(f"  {emoji} {_esc(t.symbol)} {_esc(t.direction)} {_esc(profit_str)} | {dur}")
        else:
            lines.append("<i>No completed trades today.</i>")

        await self.send(telegram_id, "\n".join(lines))

    # --------------------------------------------------------------- /explain
    async def _explain(self, telegram_id, username, arg) -> None:
        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user is None:
                await self.send(telegram_id, "Send /start first.")
                return
            lock = await repo.get_lock(session, user.id)
            if not lock.locked:
                await self.send(
                    telegram_id,
                    "You're not currently locked — /explain is only needed when "
                    "your emotion score drops below 50 and trading is auto-locked.",
                )
                return
            explanation = arg.strip()
            if not explanation:
                await self.send(
                    telegram_id,
                    "📝 <b>Explain yourself</b>\n\n"
                    "Your trading is locked because your emotion score dropped too low.\n"
                    "Write what happened: what triggered the poor trades, "
                    "how you were feeling, and what you'll do differently.\n\n"
                    "<b>Usage:</b> /explain &lt;your reflection here&gt;\n\n"
                    "<i>Your explanation is saved to your journal. "
                    "The lock still clears automatically at the next trading day.</i>",
                )
                return
            await repo.save_lock_explanation(session, user.id, explanation)

        await self.send(
            telegram_id,
            "✅ <b>Reflection saved.</b>\n\n"
            f"<i>\"{_esc(explanation[:200])}\"</i>\n\n"
            "The lock clears automatically at the next trading day. "
            "Use this time to review your journal and reset your mindset.\n\n"
            "📖 /journal to review your trades.",
        )

    # -------------------------------------------------------------- /weekly
    async def _weekly(self, telegram_id, username, arg) -> None:
        days = 7
        if arg.strip() in ("30", "month"):
            days = 30
        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user is None:
                await self.send(telegram_id, "Send /start first.")
                return
            until = datetime.now(tz=timezone.utc)
            since = until.replace(hour=0, minute=0, second=0, microsecond=0)
            from datetime import timedelta
            since = since - timedelta(days=days - 1)
            trades = await repo.get_trades_in_range(session, user.id, since=since, until=until)
            since_str = since.strftime("%Y-%m-%d")
            until_str = until.strftime("%Y-%m-%d")
            # Emotion scores for the period.
            emotion_rows = await repo.get_emotion_scores_in_range(
                session, user.id, since_str, until_str
            )

        m = compute_metrics(_trades_to_dicts(trades))
        label = f"Last {days} days" if days > 7 else "Last 7 days"

        if m is None:
            await self.send(
                telegram_id,
                f"📊 <b>{label} — {since_str} → {until_str}</b>\n\n"
                "<i>No completed trades in this period.</i>",
            )
            return

        # Journal completion rate (closed trades that have both journals).
        closed = [t for t in trades if t.profit is not None]
        journaled = sum(1 for t in closed if t.entry_journal_id and t.exit_journal_id)
        journal_rate = round(journaled / len(closed) * 100) if closed else 0
        avg_score = round(sum(r.score for r in emotion_rows) / len(emotion_rows)) if emotion_rows else None

        be = f" | Breakeven: {m.breakeven}" if m.breakeven else ""
        lines = [
            f"📊 <b>{label} — {since_str} → {until_str}</b>", "",
            f"Trades: <b>{m.total_trades}</b> | Win rate: <b>{m.win_rate:g}%</b>",
            f"Net P&amp;L: <b>{m.net_pnl:+.2f}</b> "
            f"(gross +{m.gross_profit:.2f} / −{m.gross_loss:.2f})",
            f"Wins: {m.wins} | Losses: {m.losses}{be}",
            "",
            f"Profit factor: <b>{fmt_pf(m.profit_factor)}</b> · "
            f"Avg RR: <b>{fmt_rr(m.payoff_ratio)}</b>",
            f"Expectancy: <b>{m.expectancy:+.2f}/trade</b>"
            + (f" ({m.expectancy_r:+.2f}R)" if m.expectancy_r is not None else ""),
            "",
            f"Best pair: <b>{_esc(m.best_symbol or '—')}</b> ({m.best_symbol_pnl:+.2f})",
            f"Worst pair: <b>{_esc(m.worst_symbol or '—')}</b> ({m.worst_symbol_pnl:+.2f})",
            "",
            f"Journal completion: <b>{journal_rate}%</b> ({journaled}/{len(closed)} trades)",
        ]
        if avg_score is not None:
            em = score_emoji(avg_score)
            lines.append(f"Avg emotion score: {em} <b>{avg_score}/100</b>")
        if days == 7:
            lines.append("\n<i>For 30-day view: /weekly 30</i>")
        await self.send(telegram_id, "\n".join(lines))

    # ---------------------------------------------------------- /performance
    async def _performance(self, telegram_id, username, arg) -> None:
        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user is None:
                await self.send(telegram_id, "Send /start first.")
                return
            trades = await repo.get_recent_trades(session, user.id, limit=500)

        m = compute_metrics(_trades_to_dicts(trades))
        if m is None:
            await self.send(telegram_id, "📈 <b>Performance</b>\n\n<i>No completed trades yet.</i>")
            return

        closed = [t for t in trades if t.profit is not None]
        journaled = sum(1 for t in closed if t.entry_journal_id and t.exit_journal_id)
        skipped = sum(1 for t in closed if t.status in ("entry_skipped", "exit_skipped"))
        avg_dur = _fmt_duration(m.avg_hold_s)
        be = f" | Breakeven: {m.breakeven}" if m.breakeven else ""

        lines = [
            "📈 <b>All-time Performance</b>", "",
            f"Total trades: <b>{m.total_trades}</b> | Win rate: <b>{m.win_rate:g}%</b>",
            f"Wins: {m.wins} | Losses: {m.losses}{be}",
            f"Net P&amp;L: <b>{m.net_pnl:+.2f}</b> "
            f"(gross +{m.gross_profit:.2f} / −{m.gross_loss:.2f})",
            "",
            f"Profit factor: <b>{fmt_pf(m.profit_factor)}</b>",
            f"Avg RR (payoff): <b>{fmt_rr(m.payoff_ratio)}</b>",
            f"Expectancy: <b>{m.expectancy:+.2f}/trade</b>"
            + (f" ({m.expectancy_r:+.2f}R)" if m.expectancy_r is not None else ""),
            f"Avg win: <b>{m.avg_win:+.2f}</b> | Avg loss: <b>−{m.avg_loss:.2f}</b>",
            f"Largest win: <b>{m.largest_win:+.2f}</b> | Largest loss: <b>{m.largest_loss:+.2f}</b>",
            f"Avg hold time: <b>{avg_dur}</b>",
            "",
            f"Best pair: <b>{_esc(m.best_symbol or '—')}</b> ({m.best_symbol_pnl:+.2f})",
            f"Worst pair: <b>{_esc(m.worst_symbol or '—')}</b> ({m.worst_symbol_pnl:+.2f})",
            "",
            f"Journal completion: <b>{round(journaled / m.total_trades * 100)}%</b> "
            f"({journaled}/{m.total_trades}) | Skipped: {skipped}",
        ]
        await self.send(telegram_id, "\n".join(lines))

    # ----------------------------------------------------------------- /coach
    async def _coach(self, telegram_id, username, arg) -> None:
        """AI performance & psychology review of the last N days (default 7)."""
        days = 30 if arg.strip() in ("30", "month") else 7
        from datetime import timedelta
        until = datetime.now(tz=timezone.utc)
        since = until.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)

        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user is None:
                await self.send(telegram_id, "Send /start first.")
                return
            ai_config = await repo.get_ai_config(session)
            if not ai_config["available"]:
                await self.send(
                    telegram_id,
                    "🤖 <b>AI Coach</b>\n\n"
                    "The AI coach isn't enabled yet. An admin can turn it on from "
                    "the dashboard (pick a provider and set an API key).",
                )
                return
            trades = await repo.get_trades_in_range(session, user.id, since=since, until=until)
        # Cost guard: cap /coach per user per day (checked after we know it's enabled).
        if not self._coach_quota_ok(telegram_id):
            await self.send(
                telegram_id,
                f"🤖 You've reached today's coaching limit "
                f"({settings.coach_daily_limit}/day). Try again tomorrow — "
                f"your review reflects the same data until new trades close.",
            )
            return
        async with self._sf() as session:
            journals = await repo.get_journals_in_range(session, user.id, since=since, until=until)
            since_str = since.strftime("%Y-%m-%d")
            until_str = until.strftime("%Y-%m-%d")
            emotion_rows = await repo.get_emotion_scores_in_range(
                session, user.id, since_str, until_str
            )

        metrics = compute_metrics(_trades_to_dicts(trades))
        if metrics is None and not journals:
            await self.send(
                telegram_id,
                "🤖 <b>AI Coach</b>\n\nI don't have enough trades or journals yet "
                "to coach you. Keep trading and journaling, then try again.",
            )
            return

        await self.send(telegram_id, "🤖 <i>Analysing your trades, journals &amp; psychology…</i>")

        journal_dicts = [
            {
                "type": j.type, "plan_followed": j.plan_followed or j.plan_followed_exit,
                "emotion_entry": j.emotion_entry, "emotion_exit": j.emotion_exit,
                "mistakes": j.mistakes, "rating": j.rating,
                "setup_reason": j.setup_reason, "skipped": j.skipped,
            }
            for j in journals
        ]
        emotion_dicts = [{"date": e.date, "score": e.score} for e in emotion_rows]

        context = hermes_service.build_review_context(
            metrics=metrics, journals=journal_dicts,
            emotion_scores=emotion_dicts,
            period_label=f"Last {days} days ({since_str} → {until_str})",
        )
        review = await hermes_service.generate_review(context, ai_config)

        await self.send(
            telegram_id,
            f"🤖 <b>Hermes — Your {days}-day Coaching Review</b>\n\n{_esc(review)}\n\n"
            "<i>This is reflection on your past behaviour, not financial advice. "
            "I never tell you what to trade.</i>",
        )

    # ---------------------------------------------------------------- /monthly
    async def _monthly(self, telegram_id, username, arg) -> None:
        """Equity curve, drawdown and emotion-score-trend charts (last 30 days)."""
        from datetime import timedelta
        until = datetime.now(tz=timezone.utc)
        since = until.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=29)

        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user is None:
                await self.send(telegram_id, "Send /start first.")
                return
            trades = await repo.get_trades_in_range(session, user.id, since=since, until=until)
            snap = await repo.get_snapshot(session, user.id)
            since_str = since.strftime("%Y-%m-%d")
            until_str = until.strftime("%Y-%m-%d")
            emotion_rows = await repo.get_emotion_scores_in_range(
                session, user.id, since_str, until_str
            )

        closed = [t for t in trades if t.profit is not None and t.closed_at is not None]
        closed.sort(key=lambda t: t.closed_at)

        if not closed and not emotion_rows:
            await self.send(
                telegram_id,
                "📈 <b>Monthly Charts</b>\n\n<i>No trades or emotion data in the "
                "last 30 days yet.</i>",
            )
            return

        await self.send(telegram_id, "📈 <i>Generating your monthly charts…</i>")

        # Build cumulative-PnL equity points.
        from backend.bot import client as bot_client
        from backend.services import charts_service

        cum = 0.0
        points: list[tuple[object, float]] = []
        for t in closed:
            cum = round(cum + t.profit, 2)
            points.append((t.closed_at, cum))

        # Starting balance ≈ current balance minus the period's net P&L.
        currency = snap.currency if snap else ""
        end_balance = snap.balance if snap else (cum if cum else 0.0)
        starting_balance = round(end_balance - cum, 2) if snap else 1000.0

        try:
            if points:
                eq = charts_service.equity_curve_png(points, starting_balance, currency)
                await bot_client.send_photo(
                    telegram_id, eq,
                    caption=f"📈 <b>Equity Curve</b> — {since_str} → {until_str}",
                )
                dd = charts_service.drawdown_png(points, starting_balance)
                await bot_client.send_photo(telegram_id, dd, caption="📉 <b>Drawdown (%)</b>")

            if emotion_rows:
                rows = sorted(emotion_rows, key=lambda e: e.date)
                em = charts_service.emotion_trend_png(
                    [e.date[5:] for e in rows], [e.score for e in rows]
                )
                await bot_client.send_photo(telegram_id, em, caption="🧠 <b>Emotion Score Trend</b>")
        except Exception as exc:  # noqa: BLE001
            log.error("chart generation failed for %s: %s", telegram_id, exc)
            await self.send(telegram_id, "⚠️ Couldn't generate charts right now. Try again later.")
            return

        await self.send(telegram_id, "✅ Monthly charts above. /coach for an AI review.")

    # ---------------------------------------------------------------- /trades
    async def _trades(self, telegram_id, username, arg) -> None:
        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user is None:
                await self.send(telegram_id, "Send /start first.")
                return
            trades = await repo.get_recent_trades(session, user.id, limit=10)

        if not trades:
            await self.send(telegram_id, "📋 <b>Recent Trades</b>\n\n<i>No trades recorded yet.</i>")
            return

        lines = ["📋 <b>Recent Trades</b>", ""]
        for t in trades:
            profit_str = f"{t.profit:+.2f}" if t.profit is not None else "open"
            emoji = "✅" if (t.profit or 0) > 0 else ("🔵" if t.status == "open" else "❌")
            dur = _fmt_duration(t.duration_s)
            journal_flag = ""
            if t.entry_journal_id is None and t.entry_prompted_at:
                journal_flag += " ⚠️entry"
            if t.status in ("closed", "entry_skipped") and t.exit_journal_id is None:
                journal_flag += " ⚠️exit"
            lines.append(
                f"{emoji} <b>#{t.id}</b> {_esc(t.symbol)} {_esc(t.direction)} "
                f"{_esc(profit_str)} | {dur}{journal_flag}"
            )
        lines.append("\n<i>Use /journal &lt;id&gt; to add notes to a trade.</i>")
        await self.send(telegram_id, "\n".join(lines))

    # --------------------------------------------------------------- /journal
    async def _journal(self, telegram_id, username, arg) -> None:
        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user is None:
                await self.send(telegram_id, "Send /start first.")
                return
            # /journal <id> → start FSM if it needs journaling, else show it.
            if arg.strip().isdigit():
                trade = await repo.get_trade_by_id(session, int(arg.strip()))
                if trade is None or trade.user_id != user.id:
                    await self.send(telegram_id, "Trade not found.")
                    return
                needs_entry = trade.entry_journal_id is None
                needs_exit = (
                    trade.status in ("closed", "entry_skipped", "exit_skipped")
                    and trade.exit_journal_id is None
                )
                if needs_entry or needs_exit:
                    await self._start_journal_fsm(telegram_id, trade)
                else:
                    await self._show_trade_journal(session, telegram_id, trade)
                return
            # /journal → show unjournaled + summary
            unjournaled = await repo.get_unjournaled_trades(session, user.id)
            recent = await repo.get_recent_trades(session, user.id, limit=5)

        lines = ["📓 <b>Journal</b>", ""]
        if unjournaled:
            lines.append(f"⚠️ <b>{len(unjournaled)} unjournaled trade(s):</b>")
            for t in unjournaled:
                needs = []
                if t.entry_journal_id is None:
                    needs.append("entry")
                if t.status in ("closed", "entry_skipped") and t.exit_journal_id is None:
                    needs.append("exit")
                lines.append(
                    f"  • #{t.id} {_esc(t.symbol)} {_esc(t.direction)} "
                    f"— needs {', '.join(needs)} journal"
                )
            lines.append("")
            first = unjournaled[0]
            lines.append(f"👉 Reply /journal {first.id} to start with #{first.id}")
        else:
            lines.append("✅ All trades are journaled. Keep it up!")

        if recent:
            lines.append("")
            lines.append("<b>Recent trades</b>")
            for t in recent:
                status_icon = {"open": "🔵", "closed": "⚫", "fully_journaled": "✅",
                               "entry_skipped": "⚠️", "exit_skipped": "⚠️"}.get(t.status, "•")
                profit_str = f"{t.profit:+.2f}" if t.profit is not None else "open"
                lines.append(
                    f"{status_icon} #{t.id} {_esc(t.symbol)} {_esc(t.direction)} {_esc(profit_str)}"
                )
        await self.send(telegram_id, "\n".join(lines))

    async def _show_trade_journal(self, session, telegram_id: int, trade) -> None:
        """Display a fully-journaled trade's notes + screenshot."""
        entry = await repo.get_journal_for_trade(session, trade.id, "entry")
        exit_j = await repo.get_journal_for_trade(session, trade.id, "exit")
        profit_str = f"{trade.profit:+.2f}" if trade.profit is not None else "open"
        dur = _fmt_duration(trade.duration_s)
        lines = [
            f"📓 <b>Trade #{trade.id} — {_esc(trade.symbol)} {_esc(trade.direction)}</b>",
            f"P&amp;L: <b>{_esc(profit_str)}</b> | Duration: {dur}",
            "",
        ]
        if entry and not entry.skipped:
            lines += [
                "<b>Entry</b>",
                f"• Setup: <i>{_esc(entry.setup_reason or '—')}</i>",
                f"• Emotion: {_esc(entry.emotion_entry or '—')} | "
                f"Plan: {_esc(entry.plan_followed or '—')} | "
                f"Confidence: {entry.confidence or '—'}/10",
                "",
            ]
        elif entry and entry.skipped:
            lines.append("<b>Entry</b>: ⚠️ skipped\n")
        if exit_j and not exit_j.skipped:
            lines += [
                "<b>Exit</b>",
                f"• Reason: {_esc(exit_j.exit_reason or '—')} | "
                f"Plan: {_esc(exit_j.plan_followed_exit or '—')}",
                f"• Mistakes: <i>{_esc(exit_j.mistakes or 'none')}</i>",
                f"• Emotion: {_esc(exit_j.emotion_exit or '—')} | "
                f"Rating: {'⭐' * (exit_j.rating or 0) or '—'}",
            ]
        elif exit_j and exit_j.skipped:
            lines.append("<b>Exit</b>: ⚠️ skipped")

        # Send the screenshot (if any) with the journal text as caption.
        if trade.screenshot_file_id:
            from backend.bot import client as bot_client
            ok = await bot_client.send_photo_by_id(
                telegram_id, trade.screenshot_file_id, caption="\n".join(lines)
            )
            if ok:
                return  # photo sent with the journal as caption
        await self.send(telegram_id, "\n".join(lines))

    # ------------------------------------------------------- screenshot attach
    async def _handle_photo(self, telegram_id: int, file_id: str, caption: str) -> None:
        """Attach a sent photo to a trade as its chart screenshot.

        Target: the trade id in the caption if given, else the active journal
        flow's trade, else the most recent trade without a screenshot.
        """
        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user is None:
                await self.send(telegram_id, "Send /start first.")
                return

            target = None
            # 1) explicit trade id in caption (a bare number).
            cap = caption.strip()
            if cap.isdigit():
                t = await repo.get_trade_by_id(session, int(cap))
                if t and t.user_id == user.id:
                    target = t
            # 2) active journal flow.
            if target is None:
                state = self.states.get(telegram_id)
                if state and state.get("flow") in ("entry_journal", "exit_journal"):
                    target = await repo.get_trade_by_id(session, state["trade_id"])
            # 3) most recent trade without a screenshot.
            if target is None:
                target = await repo.get_latest_trade_without_screenshot(session, user.id)

            if target is None:
                await self.send(
                    telegram_id,
                    "📷 I got your photo, but there's no trade to attach it to.\n"
                    "Send a photo with the trade number as the caption "
                    "(e.g. caption <b>12</b>), or use /trades to find the id.",
                )
                return

            await repo.set_trade_screenshot(session, target, file_id)
            symbol, direction, tid = target.symbol, target.direction, target.id

        await self.send(
            telegram_id,
            f"📷 Screenshot attached to trade <b>#{tid}</b> "
            f"({_esc(symbol)} {_esc(direction)}). View it anytime with /journal {tid}.",
        )

    async def _maybe_resume_journal(self, telegram_id: int, text: str) -> bool:
        """Resume a worker-prompted journal whose FSM isn't in bot memory.

        The worker (separate process) sends the journal question but can't set
        this dispatcher's in-memory state. When a user sends free text and we
        have no active flow, look up their most-recently-prompted unjournaled
        trade and start the matching FSM, feeding this message as the first
        answer. Subsequent steps stay in-memory and flow normally.
        Returns True if it consumed the message.
        """
        def _aware(ts):
            if ts is None:
                return None
            return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts

        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user is None:
                return False
            unjournaled = await repo.get_unjournaled_trades(session, user.id)
            if not unjournaled:
                return False
            best = None  # (prompted_at, trade_id, type)
            for t in unjournaled:
                if t.entry_journal_id is None and t.entry_prompted_at:
                    ts = _aware(t.entry_prompted_at)
                    if best is None or (ts and ts > best[0]):
                        best = (ts, t.id, "entry")
                if (t.status in ("closed", "entry_skipped")
                        and t.exit_journal_id is None and t.exit_prompted_at):
                    ts = _aware(t.exit_prompted_at)
                    if best is None or (ts and ts > best[0]):
                        best = (ts, t.id, "exit")
            if best is None:
                return False
            _, trade_id, jtype = best

        flow = "entry_journal" if jtype == "entry" else "exit_journal"
        self.states[telegram_id] = {"flow": flow, "step": 0, "trade_id": trade_id, "data": {}}
        if jtype == "entry":
            await self._entry_journal_step(telegram_id, text)
        else:
            await self._exit_journal_step(telegram_id, text)
        return True

    async def _start_journal_fsm(self, telegram_id: int, trade) -> None:
        """Begin entry or exit journal FSM depending on what's missing."""
        needs_entry = trade.entry_journal_id is None
        needs_exit = (
            trade.status in ("closed", "entry_skipped", "exit_skipped")
            and trade.exit_journal_id is None
        )
        if needs_entry:
            self.states[telegram_id] = {
                "flow": "entry_journal",
                "step": 0,
                "trade_id": trade.id,
                "data": {},
            }
            await self.send(
                telegram_id,
                f"📝 <b>Entry Journal — #{trade.id} {_esc(trade.symbol)} {_esc(trade.direction)}</b>\n\n"
                f"<b>1/4 — Setup &amp; reason</b>\n"
                f"What was your setup / reason for entering this trade?\n"
                f"<i>(Be specific: e.g. 'London open breakout above key resistance, trend continuation')</i>",
            )
        elif needs_exit:
            self.states[telegram_id] = {
                "flow": "exit_journal",
                "step": 0,
                "trade_id": trade.id,
                "data": {},
            }
            profit = trade.profit or 0.0
            dur = _fmt_duration(trade.duration_s)
            emoji = "✅" if profit >= 0 else "❌"
            await self.send(
                telegram_id,
                f"📝 <b>Exit Journal — #{trade.id} {_esc(trade.symbol)} {_esc(trade.direction)}</b>\n"
                f"{emoji} P&amp;L: <b>{profit:+.2f}</b> | Duration: {dur}\n\n"
                f"<b>1/5 — Exit reason</b>\n"
                f"Why did you exit?\n"
                f"Reply: <b>tp</b> · <b>sl</b> · <b>manual</b> · <b>partial</b>",
            )
        else:
            await self.send(telegram_id, f"✅ Trade #{trade.id} is already fully journaled.")

    # ------------------------------------------------------- entry journal FSM
    _ENTRY_STEPS = ["setup", "emotion", "plan", "confidence"]
    _EMOTIONS = {"calm", "frustrated", "anxious", "greedy", "neutral", "scared",
                 "disciplined", "stressed", "excited", "fearful"}

    async def _entry_journal_step(self, telegram_id: int, text: str) -> None:
        state = self.states.get(telegram_id)
        if not state:
            return
        step = self._ENTRY_STEPS[state["step"]]
        data = state["data"]
        t = text.strip().lower()

        if step == "setup":
            if len(text.strip()) < 5:
                await self.send(telegram_id, "Please be more specific (at least a few words).")
                return
            data["setup_reason"] = text.strip()
            state["step"] = 1
            await self.send(
                telegram_id,
                "<b>2/4 — Emotion at entry</b>\n"
                "How were you feeling when you entered?\n"
                "Reply: <b>calm</b> · <b>excited</b> · <b>greedy</b> · <b>anxious</b> "
                "· <b>fearful</b> · <b>frustrated</b> · <b>disciplined</b> · <b>neutral</b>",
            )

        elif step == "emotion":
            if t not in self._EMOTIONS:
                await self.send(
                    telegram_id,
                    f"Please choose one: calm · excited · greedy · anxious · fearful "
                    f"· frustrated · disciplined · neutral\n(you typed: {_esc(text)})",
                )
                return
            data["emotion"] = t
            state["step"] = 2
            await self.send(
                telegram_id,
                "<b>3/4 — Did this follow your trading plan?</b>\n"
                "Reply: <b>yes</b> · <b>mostly</b> · <b>no</b>",
            )

        elif step == "plan":
            if t not in ("yes", "mostly", "no"):
                await self.send(telegram_id, "Reply yes · mostly · no")
                return
            data["plan_followed"] = t
            state["step"] = 3
            await self.send(
                telegram_id,
                "<b>4/4 — Confidence level (1–10)</b>\n"
                "How confident were you in this trade? (1 = very unsure, 10 = very confident)",
            )

        elif step == "confidence":
            try:
                v = int(t)
                assert 1 <= v <= 10
            except (ValueError, AssertionError):
                await self.send(telegram_id, "Please reply a number from 1 to 10.")
                return
            data["confidence"] = v
            self.states.pop(telegram_id, None)

            async with self._sf() as session:
                user = await repo.get_user(session, telegram_id)
                trade = await repo.get_trade_by_id(session, state["trade_id"])
                if user and trade and trade.user_id == user.id:
                    await repo.save_entry_journal(
                        session, trade,
                        setup_reason=data["setup_reason"],
                        emotion=data["emotion"],
                        plan_followed=data["plan_followed"],
                        confidence=data["confidence"],
                    )
            plan_note = {
                "yes": "✅ Great — following the plan is the key to consistency.",
                "mostly": "⚠️ Partial plan adherence — review what deviated next time.",
                "no": "❌ Off-plan trade — be honest with yourself: was this emotion-driven?",
            }.get(data["plan_followed"], "")
            await self.send(
                telegram_id,
                f"✅ <b>Entry journal saved!</b>\n\n"
                f"Setup: <i>{_esc(data['setup_reason'])}</i>\n"
                f"Emotion: {_esc(data['emotion'])} | Plan: {_esc(data['plan_followed'])} "
                f"| Confidence: {data['confidence']}/10\n\n"
                f"{plan_note}\n\n"
                f"<i>I'll ask for your exit journal when the trade closes.</i>",
            )

    # -------------------------------------------------------- exit journal FSM
    _EXIT_STEPS = ["exit_reason", "plan", "mistakes", "emotion", "rating"]
    _EXIT_REASONS = {"tp", "sl", "manual", "partial"}
    _EXIT_EMOTIONS = _EMOTIONS

    async def _exit_journal_step(self, telegram_id: int, text: str) -> None:
        state = self.states.get(telegram_id)
        if not state:
            return
        step = self._EXIT_STEPS[state["step"]]
        data = state["data"]
        t = text.strip().lower()

        if step == "exit_reason":
            if t not in self._EXIT_REASONS:
                await self.send(telegram_id, "Reply: tp · sl · manual · partial")
                return
            data["exit_reason"] = t
            state["step"] = 1
            await self.send(
                telegram_id,
                "<b>2/5 — Did you follow your entry plan?</b>\n"
                "Reply: <b>yes</b> · <b>mostly</b> · <b>no</b>",
            )

        elif step == "plan":
            if t not in ("yes", "mostly", "no"):
                await self.send(telegram_id, "Reply yes · mostly · no")
                return
            data["plan_followed"] = t
            state["step"] = 2
            await self.send(
                telegram_id,
                "<b>3/5 — Any mistakes?</b>\n"
                "Be honest: did you move the SL, close early, FOMO in, revenge trade, "
                "hold too long, or deviate from the plan?\n"
                "<i>Type your mistakes, or reply <b>none</b></i>",
            )

        elif step == "mistakes":
            data["mistakes"] = text.strip() if t != "none" else ""
            state["step"] = 3
            await self.send(
                telegram_id,
                "<b>4/5 — Emotion during the trade</b>\n"
                "How did you feel while the trade was running?\n"
                "Reply: <b>calm</b> · <b>excited</b> · <b>greedy</b> · <b>anxious</b> "
                "· <b>fearful</b> · <b>frustrated</b> · <b>disciplined</b> · <b>neutral</b>",
            )

        elif step == "emotion":
            if t not in self._EXIT_EMOTIONS:
                await self.send(
                    telegram_id,
                    "Please choose: calm · excited · greedy · anxious · fearful "
                    "· frustrated · disciplined · neutral",
                )
                return
            data["emotion"] = t
            state["step"] = 4
            await self.send(
                telegram_id,
                "<b>5/5 — Rate this trade (1–5 ⭐)</b>\n"
                "Rate your <b>decision quality</b>, not the outcome:\n"
                "1 = terrible decision  |  3 = average  |  5 = perfect execution",
            )

        elif step == "rating":
            try:
                v = int(t.replace("⭐", "").strip())
                assert 1 <= v <= 5
            except (ValueError, AssertionError):
                await self.send(telegram_id, "Please reply a number from 1 to 5.")
                return
            data["rating"] = v
            self.states.pop(telegram_id, None)

            async with self._sf() as session:
                user = await repo.get_user(session, telegram_id)
                trade = await repo.get_trade_by_id(session, state["trade_id"])
                if user and trade and trade.user_id == user.id:
                    await repo.save_exit_journal(
                        session, trade,
                        exit_reason=data["exit_reason"],
                        plan_followed=data["plan_followed"],
                        mistakes=data.get("mistakes", ""),
                        emotion=data["emotion"],
                        rating=data["rating"],
                    )

            # Feedback based on answers.
            feedback = []
            if data["plan_followed"] == "no":
                feedback.append("⚠️ Off-plan exit — reflect on what triggered the deviation.")
            if data.get("mistakes"):
                feedback.append(f"📌 Mistakes noted: <i>{_esc(data['mistakes'])}</i> — review these in your weekly session.")
            if data["emotion"] in ("greedy", "fearful", "frustrated", "anxious"):
                feedback.append(f"🧠 Emotion was <b>{data['emotion']}</b> — high-emotion exits often lead to regret. Track the pattern.")
            if data["rating"] >= 4:
                feedback.append("🌟 High-quality execution — this is what consistency looks like.")
            if not feedback:
                feedback.append("Good job journaling. Every entry builds your self-awareness.")

            stars = "⭐" * data["rating"]
            await self.send(
                telegram_id,
                f"✅ <b>Exit journal saved!</b> {stars}\n\n"
                f"Exit: {_esc(data['exit_reason'])} | Plan: {_esc(data['plan_followed'])} "
                f"| Emotion: {_esc(data['emotion'])}\n\n"
                + "\n".join(feedback) +
                f"\n\n📷 <i>Send a chart screenshot now to attach it to trade "
                f"#{state['trade_id']}.</i>\n<i>Use /journal to see all your trades.</i>",
            )

    async def _risk(self, telegram_id, username, arg) -> None:
        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user is None:
                await self.send(telegram_id, "Send /start first.")
                return
            rs = user.risk_settings
            pending_json = rs.pending_json
            pending_effective = rs.pending_effective
        usd = getattr(rs, "max_daily_loss_usd", 0.0) or 0.0
        daily = (f"{rs.max_daily_loss_pct:g}%" if rs.max_daily_loss_pct > 0 else "off")
        daily_usd = (f"{usd:g} (account currency)" if usd > 0 else "off")
        text = (
            "<b>🛡️ Your risk rules</b> (currently active)\n"
            f"• Max trades/day: <b>{rs.max_trades_per_day}</b>\n"
            f"• Max daily loss %: <b>{daily}</b>\n"
            f"• Max daily loss $: <b>{daily_usd}</b>\n"
            f"• Max risk/trade: <b>{rs.max_risk_per_trade_pct:g}%</b>\n"
            f"• Max consecutive losses: <b>{rs.max_consecutive_losses}</b>\n"
            f"• Max exposure: <b>{rs.max_account_exposure_pct:g}%</b>\n"
        )
        if pending_json and pending_effective:
            import json as _json
            try:
                pend = _json.loads(pending_json)
            except Exception:  # noqa: BLE001
                pend = {}
            _lbl = {
                "max_trades_per_day": "trades/day", "max_daily_loss_pct": "daily loss %",
                "max_daily_loss_usd": "daily loss $", "max_risk_per_trade_pct": "risk/trade",
                "max_consecutive_losses": "losses in a row", "max_account_exposure_pct": "exposure",
            }
            changes = ", ".join(f"{_lbl.get(k, k)} → {v:g}" if isinstance(v, (int, float)) else f"{_lbl.get(k, k)} → {v}" for k, v in pend.items())
            text += (
                f"\n⏳ <b>Pending (looser) change</b> takes effect <b>{_esc(pending_effective)}</b>: "
                f"{_esc(changes)}\n<i>Loosening is delayed to protect you from emotional changes.</i>\n"
            )
        text += "\n👉 To change them, send /setrisk — tightening applies instantly."
        await self.send(telegram_id, text)

    # --- /setrisk guided wizard --------------------------------------------
    _SETRISK_STEPS = ["trades", "dailyloss", "riskpertrade", "losses", "exposure"]

    def _daily_loss_desc(self, data: dict) -> str:
        if data["max_daily_loss_usd"] > 0:
            return f"{data['max_daily_loss_usd']:g} (account currency)"
        if data["max_daily_loss_pct"] > 0:
            return f"{data['max_daily_loss_pct']:g}%"
        return "off"

    def _setrisk_prompt(self, step: str, data: dict) -> str:
        if step == "trades":
            return (f"<b>1/5 — Max trades per day?</b>\nNow: <b>{data['max_trades_per_day']}</b>\n"
                    "Reply a number (e.g. <b>2</b>), or <i>skip</i> to keep it.")
        if step == "dailyloss":
            return (f"<b>2/5 — Max daily loss?</b>\nNow: <b>{self._daily_loss_desc(data)}</b>\n"
                    "Reply <b>5%</b> (percent) or <b>50$</b> (dollars), <i>off</i> to disable, or <i>skip</i>.")
        if step == "riskpertrade":
            return (f"<b>3/5 — Max risk per trade (%)?</b>\nNow: <b>{data['max_risk_per_trade_pct']:g}%</b>\n"
                    "Reply a number (e.g. <b>2</b>), or <i>skip</i>.")
        if step == "losses":
            return (f"<b>4/5 — Max losing trades in a row?</b>\nNow: <b>{data['max_consecutive_losses']}</b>\n"
                    "Reply a number (e.g. <b>2</b>), or <i>skip</i>.")
        return (f"<b>5/5 — Max account exposure (%)?</b>\nNow: <b>{data['max_account_exposure_pct']:g}%</b>\n"
                "Reply a number (e.g. <b>5</b>), <i>off</i>, or <i>skip</i>.")

    async def _setrisk(self, telegram_id, username, arg) -> None:
        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user is None:
                await self.send(telegram_id, "Send /start first.")
                return
            rs = user.risk_settings
            data = {
                "max_trades_per_day": rs.max_trades_per_day,
                "max_daily_loss_pct": rs.max_daily_loss_pct,
                "max_daily_loss_usd": getattr(rs, "max_daily_loss_usd", 0.0) or 0.0,
                "max_risk_per_trade_pct": rs.max_risk_per_trade_pct,
                "max_consecutive_losses": rs.max_consecutive_losses,
                "max_account_exposure_pct": rs.max_account_exposure_pct,
            }
        self.states[telegram_id] = {"flow": "setrisk", "step": 0, "data": data}
        await self.send(
            telegram_id,
            "<b>⚙️ Set your risk rules</b>\nI'll ask 5 quick questions. "
            "Reply <i>skip</i> to keep any value, or /cancel to stop.\n\n"
            + self._setrisk_prompt("trades", data),
        )

    async def _setrisk_step(self, telegram_id, text) -> None:
        state = self.states.get(telegram_id)
        if not state:
            return
        idx = state["step"]
        data = state["data"]
        step = self._SETRISK_STEPS[idx]
        t = text.strip().lower()

        if t not in ("skip", "keep"):
            err = self._apply_setrisk_answer(step, t, data)
            if err:
                await self.send(telegram_id, f"{err}\n\n{self._setrisk_prompt(step, data)}")
                return

        idx += 1
        if idx < len(self._SETRISK_STEPS):
            state["step"] = idx
            await self.send(telegram_id, self._setrisk_prompt(self._SETRISK_STEPS[idx], data))
            return

        # Done — save with anti-gaming: stricter now, looser deferred to tomorrow.
        self.states.pop(telegram_id, None)
        deferred: dict = {}
        effective = None
        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user is not None:
                _applied, deferred, effective = await repo.apply_risk_change(session, user, data)

        _labels = {
            "max_trades_per_day": "Trades/day",
            "max_daily_loss_pct": "Daily loss %", "max_daily_loss_usd": "Daily loss $",
            "max_risk_per_trade_pct": "Risk/trade", "max_consecutive_losses": "Losses in a row",
            "max_account_exposure_pct": "Exposure",
        }
        msg = [
            "✅ <b>Your rules are saved.</b>",
            f"• Trades/day: <b>{data['max_trades_per_day']}</b>",
            f"• Daily loss: <b>{self._daily_loss_desc(data)}</b>",
            f"• Risk/trade: <b>{data['max_risk_per_trade_pct']:g}%</b>",
            f"• Losses in a row: <b>{data['max_consecutive_losses']}</b>",
            f"• Exposure: <b>{data['max_account_exposure_pct']:g}%</b>",
        ]
        if deferred:
            names = ", ".join(_labels.get(k, k) for k in deferred)
            msg.append(
                f"\n🛡️ <b>Heads up:</b> you <i>loosened</i> {names}. To protect you "
                f"from emotional changes, looser limits take effect <b>tomorrow "
                f"({_esc(effective)})</b>. Until then your current (stricter) limits "
                f"stay active. Tightening always applies instantly."
            )
        msg.append("\n/status")
        await self.send(telegram_id, "\n".join(msg))

    def _apply_setrisk_answer(self, step: str, t: str, data: dict) -> str | None:
        """Apply one answer to `data`. Returns an error string if invalid."""
        if step == "trades":
            try:
                v = int(t)
            except ValueError:
                return "Please reply a whole number (e.g. 2)."
            if not (1 <= v <= 100):
                return "Number must be between 1 and 100."
            data["max_trades_per_day"] = v
        elif step == "dailyloss":
            if t in ("off", "none", "0"):
                data["max_daily_loss_pct"] = 0
                data["max_daily_loss_usd"] = 0
            elif "$" in t or "usd" in t:
                try:
                    v = float(t.replace("$", "").replace("usd", "").strip())
                except ValueError:
                    return "Please reply like 50$ or 5%."
                data["max_daily_loss_usd"] = v
                data["max_daily_loss_pct"] = 0
            else:
                try:
                    v = float(t.replace("%", "").strip())
                except ValueError:
                    return "Please reply like 5% or 50$ (or 'off')."
                data["max_daily_loss_pct"] = v
                data["max_daily_loss_usd"] = 0
        elif step == "riskpertrade":
            try:
                v = float(t.replace("%", "").strip())
            except ValueError:
                return "Please reply a number (e.g. 2)."
            if not (0 <= v <= 100):
                return "Number must be between 0 and 100."
            data["max_risk_per_trade_pct"] = v
        elif step == "losses":
            try:
                v = int(t)
            except ValueError:
                return "Please reply a whole number (e.g. 2)."
            if not (1 <= v <= 100):
                return "Number must be between 1 and 100."
            data["max_consecutive_losses"] = v
        elif step == "exposure":
            if t in ("off", "none"):
                data["max_account_exposure_pct"] = 0
            else:
                try:
                    v = float(t.replace("%", "").strip())
                except ValueError:
                    return "Please reply a number (e.g. 5) or 'off'."
                if not (0 <= v <= 100):
                    return "Number must be between 0 and 100."
                data["max_account_exposure_pct"] = v
        return None

    async def _lock(self, telegram_id, username, arg) -> None:
        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user is None:
                await self.send(telegram_id, "Send /start first.")
                return
            # Daily lock: holds for the rest of the day and CANNOT be self-undone.
            await repo.set_lock(session, user.id, "self-lock (Telegram)", daily=True)
        await self.send(
            telegram_id,
            "🔒 You're locked for the rest of today. New trades will be closed.\n\n"
            "This <b>cannot be undone on demand</b> — that's the point: it protects you "
            "from emotional trading. It clears automatically at the next trading day.",
        )

    async def _unlock(self, telegram_id, username, arg) -> None:
        """Locks can NOT be removed on demand — not even by an admin.

        By design: if the owner could unlock from the bot, the lock would be
        meaningless (you'd cheat your own discipline). Locks clear automatically
        at the next trading day. A genuine emergency requires direct server/DB
        access — deliberately not exposed as a command.
        """
        await self.send(
            telegram_id,
            "🔒 Locks can't be removed on demand — <b>not even by an admin</b>.\n\n"
            "This is intentional: it protects you from emotional trading and keeps "
            "the lock meaningful. It clears automatically at the next trading day.",
        )

    async def _subscribe(self, telegram_id, username, arg) -> None:
        plan = "quarterly" if arg.strip().lower().startswith("q") else "monthly"

        # Preferred: CryptoPay auto-confirmed invoice (one-tap, auto-activates).
        if self.invoicer is not None:
            await self.send(telegram_id, f"🧾 Creating your {plan} invoice…")
            ok, result = await self.invoicer(telegram_id, plan)
            if ok:
                await self.send(
                    telegram_id,
                    f"<b>💳 Pay your {plan} subscription</b>\n"
                    f"Tap to pay (crypto): {_esc(result)}\n\n"
                    "Activates automatically once paid. "
                    "Use /subscribe quarterly for the quarterly plan, "
                    "or /stars to pay with Telegram Stars.",
                )
            else:
                await self.send(telegram_id, result)
            return

        # Fallback: manual wallet + tx-hash flow.
        if not settings.crypto_wallet_address:
            await self.send(telegram_id, "Payments aren't configured yet. Please contact the admin.")
            return
        await self.send(
            telegram_id,
            "<b>💳 Subscribe (crypto)</b>\n"
            f"Monthly: <b>{settings.price_monthly:g} {settings.crypto_currency}</b>\n"
            f"Quarterly: <b>{settings.price_quarterly:g} {settings.crypto_currency}</b>\n\n"
            f"Send to:\n<code>{_esc(settings.crypto_wallet_address)}</code>\n"
            f"Network: {settings.crypto_currency}\n"
            f"Reference (include if possible): <code>{telegram_id}</code>\n\n"
            "After paying, send:\n<code>/paid YOUR_TX_HASH</code>",
        )

    async def _stars(self, telegram_id, username, arg) -> None:
        """Pay with Telegram Stars (native)."""
        if self.star_invoicer is None:
            await self.send(telegram_id, "Stars payments aren't available right now.")
            return
        plan = "quarterly" if arg.strip().lower().startswith("q") else "monthly"
        async with self._sf() as session:
            if await repo.get_user(session, telegram_id) is None:
                await repo.register_user(session, telegram_id, username)
        ok, msg = await self.star_invoicer(telegram_id, plan)
        await self.send(telegram_id, msg)

    async def _paid(self, telegram_id, username, arg) -> None:
        if not arg:
            await self.send(telegram_id, "Usage: /paid &lt;tx_hash&gt;")
            return
        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user is None:
                await self.send(telegram_id, "Send /start first.")
                return
            payment = await repo.submit_payment(
                session, user, tx_hash=arg, amount=None,
                currency=settings.crypto_currency, note=None,
            )
        await self.send(
            telegram_id,
            f"✅ Payment #{payment.id} submitted (tx <code>{_esc(arg[:24])}</code>). "
            "We'll verify and activate your subscription shortly.",
        )
        # Notify admins.
        for admin_id in settings.admin_ids:
            await self.send(
                admin_id,
                f"💰 New payment #{payment.id} from <code>{telegram_id}</code> "
                f"(@{_esc(username) if username else '—'})\ntx: <code>{_esc(arg)}</code>\n"
                f"/verify {payment.id} then /activate {telegram_id} 30",
            )

    # --------------------------------------------------------------- /link FSM
    async def _link_start(self, telegram_id, username, arg) -> None:
        async with self._sf() as session:
            user = await repo.register_user(session, telegram_id, username)  # ensure exists
            if user.tos_accepted_at is None:
                await self.send(
                    telegram_id,
                    "Please accept the terms first — reply /agree (or /start to read them).",
                )
                return
        self.states[telegram_id] = {"flow": "link", "step": "login", "data": {}}
        await self.send(
            telegram_id,
            "<b>🔗 Link your MT5 account</b>\n"
            "Send your <b>account number (login)</b>.\n"
            "<i>(Send /cancel anytime.)</i>",
        )

    async def _link_step(self, telegram_id, text, message_id) -> None:
        state = self.states.get(telegram_id)
        if not state:
            return
        step = state["step"]
        data = state["data"]

        if step == "login":
            if not text.isdigit():
                await self.send(telegram_id, "Login must be a number. Try again, or /cancel.")
                return
            data["login"] = int(text)
            state["step"] = "server"
            await self.send(telegram_id, "Now send your <b>broker server</b> (e.g. <code>OctaFX-Real</code>).")
            return

        if step == "server":
            data["server"] = text
            state["step"] = "password"
            await self.send(
                telegram_id,
                "Now send your MT5 <b>password</b>.\n"
                "🔐 <i>I'll delete your message immediately and store it encrypted.</i>",
            )
            return

        if step == "password":
            data["password"] = text
            # Remove the password message from the chat for safety.
            if self.delete and message_id is not None:
                await self.delete(telegram_id, message_id)
            # Users connect their trading account — no need to ask the type.
            data["account_type"] = "trading"
            self.states.pop(telegram_id, None)
            await self._finish_link(telegram_id, data)
            return

    async def _finish_link(self, telegram_id, data: dict) -> None:
        atype = data["account_type"]
        async with self._sf() as session:
            user = await repo.get_user(session, telegram_id)
            if user.accounts:  # re-link → update the existing account
                acct = await repo.update_account(
                    session, user.accounts[0],
                    login=data["login"], server=data["server"],
                    password=data["password"], broker=None, account_type=atype,
                )
            else:
                acct = await repo.add_account(
                    session, user,
                    login=data["login"], server=data["server"],
                    password=data["password"], broker=None, account_type=atype,
                )
            account_id = acct.id
            subscribed = repo.subscription_is_active(user.subscription)
        await self.send(
            telegram_id,
            f"🔗 Saved MT5 <b>{data['login']}</b> @ {_esc(data['server'])}, encrypted.",
        )

        # Validate the credentials immediately (if a validator is wired).
        connected = True
        if self.validator is not None:
            await self.send(telegram_id, "🔄 Checking your credentials…")
            try:
                ok, message = await self.validator(account_id)
            except Exception:  # noqa: BLE001
                ok, message = False, "validation error"
            connected = ok
            if ok:
                await self.send(telegram_id, f"✅ Connected to <b>{_esc(message)}</b>.")
            else:
                await self.send(
                    telegram_id,
                    f"❌ Couldn't log in: {_esc(message)}\n"
                    "Please /link again with the correct login, server, and password.",
                )

        # Subscription gate: protection only starts when subscribed.
        if connected:
            if subscribed:
                await self.send(telegram_id, "🛡️ Zanzer is now protecting your account. /status")
            else:
                await self.send(
                    telegram_id,
                    "⏳ You're <b>not subscribed yet</b>, so monitoring is paused "
                    "(you're added as pending). Activate protection with "
                    "/subscribe or /stars.",
                )

    # ----------------------------------------------------------- admin cmds
    async def _admin_pending(self, telegram_id, username, arg) -> None:
        if not self._is_admin(telegram_id):
            await self.send(telegram_id, "Not authorized.")
            return
        async with self._sf() as session:
            pending = await repo.list_pending_payments(session)
            rows = []
            for p in pending:
                rows.append(f"#{p.id} user={p.user_id} {p.currency or ''} tx={_esc((p.tx_hash or '')[:18])}")
        await self.send(telegram_id, "<b>Pending payments</b>\n" + ("\n".join(rows) if rows else "(none)"))

    async def _admin_verify(self, telegram_id, username, arg) -> None:
        if not self._is_admin(telegram_id):
            await self.send(telegram_id, "Not authorized.")
            return
        if not arg.isdigit():
            await self.send(telegram_id, "Usage: /verify &lt;payment_id&gt;")
            return
        async with self._sf() as session:
            payment = await repo.get_payment(session, int(arg))
            if payment is None:
                await self.send(telegram_id, "Payment not found.")
                return
            await repo.set_payment_status(session, payment, "verified")
        await self.send(telegram_id, f"Payment #{arg} marked verified. Now /activate the user.")

    async def _admin_activate(self, telegram_id, username, arg) -> None:
        if not self._is_admin(telegram_id):
            await self.send(telegram_id, "Not authorized.")
            return
        parts = arg.split()
        if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
            await self.send(telegram_id, "Usage: /activate &lt;telegram_id&gt; &lt;days&gt; [plan]")
            return
        target_tid, days = int(parts[0]), int(parts[1])
        plan = parts[2] if len(parts) > 2 else "monthly"
        async with self._sf() as session:
            user = await repo.get_user(session, target_tid)
            if user is None:
                await self.send(telegram_id, "User not found (have they sent /start?).")
                return
            sub = await repo.activate_subscription(session, user, days, plan=plan,
                                                   activated_by=telegram_id)
            expires = str(sub.expires_at)[:10]
        await self.send(telegram_id, f"✅ Activated {target_tid} for {days}d ({plan}), expires {expires}.")
        await self.send(target_tid, f"🎉 Your subscription is active until {expires}. /status")

    async def _admin_accounts(self, telegram_id, username, arg) -> None:
        if not self._is_admin(telegram_id):
            await self.send(telegram_id, "Not authorized.")
            return
        async with self._sf() as session:
            accounts = await repo.list_all_accounts(session)
            rows = []
            for a in accounts:
                emoji = {"active": "🟢", "pending": "🟡", "error": "🔴"}.get(a.status, "•")
                paid = repo.subscription_is_active(a.user.subscription)
                sub = "💳 PAID" if paid else "🚫 unpaid"
                rows.append(
                    f"{emoji} #{a.id} {a.login}@{_esc(a.server)} — {sub}\n"
                    f"     user={a.user.telegram_id} · status [{_esc(a.status)}]"
                )
        await self.send(
            telegram_id,
            "<b>MT5 accounts</b>\n" + ("\n".join(rows) if rows else "(none)") +
            "\n\n<i>💳 PAID = subscription active (connect these). "
            "Use /creds &lt;id&gt; to get login details.</i>",
        )

    async def _admin_provision(self, telegram_id, username, arg) -> None:
        if not self._is_admin(telegram_id):
            await self.send(telegram_id, "Not authorized.")
            return
        if not arg.isdigit():
            await self.send(telegram_id, "Usage: /provision &lt;account_id&gt;")
            return
        if self.provisioner is None:
            await self.send(
                telegram_id,
                "Provisioning runs on the VPS. Use: "
                "<code>python -m backend.provisioning &lt;account_id&gt;</code> "
                "(or set AUTO_PROVISION=true).",
            )
            return
        await self.send(telegram_id, f"⏳ Cloning a terminal for account {_esc(arg)}… (this can take a minute)")
        result = await self.provisioner(int(arg))
        # Look up login + the provisioned path to give turnkey next steps.
        async with self._sf() as session:
            acct = await repo.get_account_by_id(session, int(arg))
        if acct is None or not acct.terminal_path:
            await self.send(
                telegram_id,
                f"⚠️ Provisioning didn't complete: <code>{_esc(result)}</code>\n"
                "Check that BASE_TERMINAL_DIR points to a real MT5 install on the VPS.",
            )
            return
        await self.send(
            telegram_id,
            f"✅ <b>Terminal ready for account #{acct.id}</b> (login {acct.login})\n"
            f"📁 <code>{_esc(acct.terminal_path)}</code>\n\n"
            f"<b>You only need to launch it + log in once:</b>\n"
            f"1️⃣ On the VPS, run:\n"
            f"<code>\"{_esc(acct.terminal_path)}\" /portable</code>\n"
            f"2️⃣ In that MT5 window: File → Login → use <b>/creds {acct.id}</b> for the password\n"
            f"3️⃣ Tools → Options → Expert Advisors → enable <b>Allow algorithmic trading</b> (button green)\n"
            f"4️⃣ Leave it running — the worker auto-connects within ~30s.\n\n"
            f"<i>The folder copy is done for you; you just log in.</i>",
        )

    async def _admin_creds(self, telegram_id, username, arg) -> None:
        """Admin-only: reveal an account's MT5 login/server/broker/password so the
        admin can log into the terminal on the VPS. The message self-deletes."""
        if not self._is_admin(telegram_id):
            await self.send(telegram_id, "Not authorized.")
            return
        if not arg.strip().isdigit():
            await self.send(telegram_id, "Usage: /creds &lt;account_id&gt;  (see /accounts for ids)")
            return
        from sqlalchemy import select
        from backend.db.models import MT5Account
        from backend.security import decrypt
        n = int(arg.strip())
        async with self._sf() as session:
            acct = await repo.get_account_by_id(session, n)
            if acct is None:  # fall back to matching by MT5 login number
                res = await session.execute(select(MT5Account).where(MT5Account.login == n))
                acct = res.scalar_one_or_none()
            if acct is None:
                await self.send(telegram_id, "Account not found. Use the id shown in /accounts.")
                return
            try:
                pw = decrypt(acct.password_encrypted)
            except Exception:  # noqa: BLE001
                pw = "(could not decrypt — check ENCRYPTION_KEY)"
            login, server, broker, tpath = acct.login, acct.server, acct.broker, acct.terminal_path
        text = (
            f"🔐 <b>MT5 credentials — account #{acct.id}</b>\n"
            f"Login: <code>{login}</code>\n"
            f"Server: <code>{_esc(server)}</code>\n"
            f"Broker: <code>{_esc(broker or '—')}</code>\n"
            f"Password: <code>{_esc(pw)}</code>\n"
            f"Terminal: <code>{_esc(tpath or 'default')}</code>\n\n"
            f"⚠️ <i>Tap to copy, log into MT5, then this message self-deletes in 2 min. "
            f"Do NOT forward it.</i>"
        )
        from backend.bot import client as bot_client
        mid = await bot_client.send_message_id(telegram_id, text)
        if mid:
            async def _auto_delete():
                try:
                    await asyncio.sleep(120)
                    await bot_client.delete_message(telegram_id, mid)
                except Exception:  # noqa: BLE001
                    pass
            asyncio.create_task(_auto_delete())
        else:
            await self.send(telegram_id, text)  # fallback (no auto-delete)

    async def _admin_channelnow(self, telegram_id, username, arg) -> None:
        """Preview/post the daily community summary to the marketing channel now."""
        if not self._is_admin(telegram_id):
            await self.send(telegram_id, "Not authorized.")
            return
        from backend import channel as channel_mod
        if not settings.marketing_channel_id:
            # No channel set yet — just show the admin the preview.
            preview = await channel_mod.build_post()
            await self.send(
                telegram_id,
                "ℹ️ MARKETING_CHANNEL_ID isn't set, so I can't post. Preview:\n\n" + preview,
            )
            return
        ok = await channel_mod.post_now()
        await self.send(
            telegram_id,
            f"✅ Posted to {_esc(settings.marketing_channel_id)}." if ok
            else "❌ Post failed — is the bot an ADMIN of the channel, and the id correct?",
        )

    async def _admin_broadcast(self, telegram_id, username, arg) -> None:
        """Send an announcement to every registered user."""
        if not self._is_admin(telegram_id):
            await self.send(telegram_id, "Not authorized.")
            return
        msg = arg.strip()
        if not msg:
            await self.send(telegram_id, "Usage: /broadcast &lt;message&gt;")
            return
        async with self._sf() as session:
            users = await repo.list_users(session)
        await self.send(telegram_id, f"📢 Broadcasting to {len(users)} user(s)…")
        body = f"📢 <b>Announcement</b>\n\n{_esc(msg)}"
        sent = failed = 0
        for u in users:
            try:
                ok = await self.send(u.telegram_id, body)
                sent += 1 if ok else 0
                failed += 0 if ok else 1
            except Exception:  # noqa: BLE001
                failed += 1
            await asyncio.sleep(0.05)  # stay under Telegram's ~30 msg/s limit
        await self.send(telegram_id, f"✅ Broadcast done — sent {sent}, failed {failed}.")

    async def _admin_users(self, telegram_id, username, arg) -> None:
        if not self._is_admin(telegram_id):
            await self.send(telegram_id, "Not authorized.")
            return
        async with self._sf() as session:
            users = await repo.list_users(session)
            rows = []
            for u in users:
                act = "✅" if repo.subscription_is_active(u.subscription) else "—"
                rows.append(f"{act} {u.telegram_id} @{_esc(u.username) if u.username else '—'}")
        await self.send(telegram_id, "<b>Users</b>\n" + ("\n".join(rows) if rows else "(none)"))
