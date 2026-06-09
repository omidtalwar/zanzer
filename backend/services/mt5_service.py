"""MetaTrader 5 integration — READ ONLY for V1.

V1 does not place, modify, or close any orders. It only reads account state,
open positions, and deal history. Enforcement actions (block/close/lock) arrive
in V2 behind the Risk Engine, never here.

The `MetaTrader5` package is Windows-only and talks to a running MT5 terminal.
If the package isn't installed (e.g. wrong Python version), importing it raises
ImportError; callers get a clear MT5Error instead of a crash.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.config import settings
from backend.logging_config import get_logger
from backend.models import AccountInfo, HistoryDeal, OpenPosition

log = get_logger("mt5")

try:
    import MetaTrader5 as mt5  # type: ignore
    _MT5_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on environment
    mt5 = None  # type: ignore
    _MT5_AVAILABLE = False


class MT5Error(RuntimeError):
    """Raised when MT5 is unavailable or a terminal call fails."""


def _require_mt5() -> None:
    if not _MT5_AVAILABLE:
        raise MT5Error(
            "The MetaTrader5 package is not installed for this Python interpreter. "
            "It is Windows-only and may not have a wheel for the newest Python "
            "versions — try Python 3.12 if install failed."
        )


class MT5Service:
    """Thin wrapper around the MetaTrader5 terminal connection.

    Defaults to the global settings (personal/single-user path). Workers in the
    terminal farm pass per-account overrides (each worker is its own process, as
    the MetaTrader5 package is one-terminal-per-process)."""

    def __init__(
        self,
        *,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        path: str | None = None,
    ) -> None:
        self._initialized = False
        self._login = login if login is not None else settings.mt5_login
        self._password = password if password is not None else settings.mt5_password
        self._server = server if server is not None else settings.mt5_server
        self._path = path if path is not None else settings.mt5_terminal_path

    # --- connection lifecycle -------------------------------------------------
    def connect(self) -> None:
        _require_mt5()
        kwargs: dict = {}
        if self._path:
            kwargs["path"] = self._path
        if self._login:
            kwargs["login"] = int(self._login)
            kwargs["password"] = self._password or ""
            kwargs["server"] = self._server or ""

        if not mt5.initialize(**kwargs):
            code, msg = mt5.last_error()
            raise MT5Error(f"MT5 initialize failed [{code}]: {msg}")

        self._initialized = True
        info = mt5.account_info()
        if info is None:
            raise MT5Error(
                "Connected to terminal but no account is logged in. "
                "Log in to a broker account in the MT5 terminal."
            )
        log.info("Connected to MT5: account %s @ %s", info.login, info.server)

    def shutdown(self) -> None:
        if self._initialized and mt5 is not None:
            mt5.shutdown()
            self._initialized = False
            log.info("MT5 connection closed")

    def _ensure(self) -> None:
        if not self._initialized:
            self.connect()

    # --- read operations ------------------------------------------------------
    def get_account_info(self) -> AccountInfo:
        self._ensure()
        info = mt5.account_info()
        if info is None:
            code, msg = mt5.last_error()
            raise MT5Error(f"account_info failed [{code}]: {msg}")
        return AccountInfo(
            login=info.login,
            broker=info.company,
            server=info.server,
            currency=info.currency,
            balance=info.balance,
            equity=info.equity,
            margin=info.margin,
            margin_free=info.margin_free,
            profit=info.profit,
        )

    def get_open_positions(self) -> list[OpenPosition]:
        self._ensure()
        positions = mt5.positions_get()
        if positions is None:
            code, msg = mt5.last_error()
            raise MT5Error(f"positions_get failed [{code}]: {msg}")
        result: list[OpenPosition] = []
        for p in positions:
            result.append(
                OpenPosition(
                    ticket=p.ticket,
                    symbol=p.symbol,
                    direction="BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
                    volume=p.volume,
                    price_open=p.price_open,
                    price_current=p.price_current,
                    sl=p.sl,
                    tp=p.tp,
                    profit=p.profit,
                    time=datetime.fromtimestamp(p.time, tz=timezone.utc),
                )
            )
        return result

    def get_history_deals(self, since: datetime, until: datetime | None = None) -> list[HistoryDeal]:
        """Return closed deals between `since` and `until` (default now)."""
        self._ensure()
        until = until or datetime.now(tz=timezone.utc)
        deals = mt5.history_deals_get(since, until)
        if deals is None:
            code, msg = mt5.last_error()
            raise MT5Error(f"history_deals_get failed [{code}]: {msg}")
        result: list[HistoryDeal] = []
        for d in deals:
            # Skip balance/credit operations (entry deals with no symbol)
            if not d.symbol:
                continue
            if d.type == mt5.DEAL_TYPE_BUY:
                direction = "BUY"
            elif d.type == mt5.DEAL_TYPE_SELL:
                direction = "SELL"
            else:
                direction = ""
            entry = {
                mt5.DEAL_ENTRY_IN: "IN",
                mt5.DEAL_ENTRY_OUT: "OUT",
                mt5.DEAL_ENTRY_INOUT: "INOUT",
            }.get(d.entry, "")
            result.append(
                HistoryDeal(
                    ticket=d.ticket,
                    position_id=d.position_id,
                    symbol=d.symbol,
                    direction=direction,
                    entry=entry,
                    volume=d.volume,
                    price=d.price,
                    profit=d.profit,
                    time=datetime.fromtimestamp(d.time, tz=timezone.utc),
                )
            )
        return result

    def _server_offset(self) -> timedelta:
        """Broker server time minus UTC.

        MT5 deal timestamps are in the broker's server time (not UTC). We need
        this offset both to define the server "day" and to fetch the right
        window. Prefer the configured value; otherwise auto-detect from a live
        tick (rounded to the nearest hour). Falls back to 0.
        """
        if settings.broker_utc_offset_hours is not None:
            return timedelta(hours=settings.broker_utc_offset_hours)
        try:
            candidates = ["EURUSD", "XAUUSD", "GBPUSD", "USDJPY", "SPX500", "BTCUSD"]
            symbols = mt5.symbols_get()
            if symbols:
                candidates += [s.name for s in symbols[:200]]
            now_utc = datetime.now(tz=timezone.utc)
            for name in candidates:
                tick = mt5.symbol_info_tick(name)
                if tick and tick.time:
                    server = datetime.fromtimestamp(tick.time, tz=timezone.utc)
                    raw = (server - now_utc).total_seconds()
                    # Server offsets are whole hours — round to clean up tick lag.
                    return timedelta(hours=round(raw / 3600.0))
        except Exception as exc:  # noqa: BLE001
            log.warning("server offset auto-detect failed: %s", exc)
        return timedelta(0)

    def get_today_deals(self) -> list[HistoryDeal]:
        """Deals since the broker's server-day midnight.

        Deal times are server time, so we compute the day boundary in server
        time and fetch a widened window (to avoid clipping recent deals whose
        server timestamps sit ahead of UTC 'now'), then filter to today.
        """
        offset = self._server_offset()
        now_utc = datetime.now(tz=timezone.utc)
        server_now = now_utc + offset
        # Server-day midnight, expressed in the same (server-as-UTC) frame the
        # HistoryDeal.time values use.
        server_midnight = server_now.replace(hour=0, minute=0, second=0, microsecond=0)
        # Fetch a wide window so nothing is clipped by the server/UTC skew.
        deals = self.get_history_deals(now_utc - timedelta(days=2), now_utc + timedelta(days=2))
        return [d for d in deals if d.time >= server_midnight]

    # --- trading actions (V2, LIVE ONLY) -------------------------------------
    # These are the ONLY methods that change broker state. They must never be
    # called outside live enforcement; the enforcement service gates them.
    def close_position(self, ticket: int) -> str:
        """Market-close a single position by ticket. Returns a status string.

        Raises MT5Error on failure so the caller can report it.
        """
        self._ensure()
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            raise MT5Error(f"position {ticket} not found (already closed?)")
        pos = positions[0]

        # Ensure the symbol is selected so we can read its tick/filling modes.
        mt5.symbol_select(pos.symbol, True)

        # Opposite side closes the position; use current market price.
        tick = mt5.symbol_info_tick(pos.symbol)
        if pos.type == mt5.POSITION_TYPE_BUY:
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": order_type,
            "position": pos.ticket,
            "price": price,
            "deviation": 50,
            "magic": 0,
            "comment": "zanzer risk-engine close",
            "type_time": mt5.ORDER_TIME_GTC,
        }

        # Different brokers/symbols accept different filling modes; the wrong one
        # returns retcode 10030 "Unsupported filling mode". Try the symbol's
        # advertised modes first, then fall back, until one is accepted.
        last_result = None
        for filling in self._filling_modes(pos.symbol):
            request["type_filling"] = filling
            result = mt5.order_send(request)
            if result is None:
                code, msg = mt5.last_error()
                raise MT5Error(f"order_send returned None [{code}]: {msg}")
            last_result = result
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                log.warning("Closed position %s (%s %s)", ticket, pos.symbol, pos.volume)
                return f"closed {pos.symbol} {pos.volume} lots @ {price}"
            if result.retcode != 10030:  # not a filling-mode problem → stop
                break

        rc = last_result.retcode if last_result else "?"
        cm = last_result.comment if last_result else ""
        raise MT5Error(f"close of position {ticket} failed: retcode={rc} {cm}")

    @staticmethod
    def _filling_modes(symbol: str) -> list[int]:
        """Filling modes to try for `symbol`, best first."""
        order: list[int] = []
        info = mt5.symbol_info(symbol)
        if info is not None:
            fm = info.filling_mode
            if fm & getattr(mt5, "SYMBOL_FILLING_FOK", 1):
                order.append(mt5.ORDER_FILLING_FOK)
            if fm & getattr(mt5, "SYMBOL_FILLING_IOC", 2):
                order.append(mt5.ORDER_FILLING_IOC)
        # Fallbacks (dedup, preserve order).
        for f in (mt5.ORDER_FILLING_RETURN, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK):
            if f not in order:
                order.append(f)
        return order

    def close_all_positions(self) -> list[str]:
        """Close every open position. Returns one status string per attempt."""
        results: list[str] = []
        for pos in self.get_open_positions():
            try:
                results.append(self.close_position(pos.ticket))
            except MT5Error as exc:
                results.append(f"FAILED {pos.ticket}: {exc}")
                log.error("Failed to close position %s: %s", pos.ticket, exc)
        return results


# Module-level singleton; FastAPI lifespan manages connect/shutdown.
mt5_service = MT5Service()
