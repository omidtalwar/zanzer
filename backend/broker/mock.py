"""In-memory broker for tests and local development without a terminal.

Lets us simulate account state, positions, and deal history, and records when
close_all_positions is called so tests can assert enforcement behaviour.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.models import AccountInfo, HistoryDeal, OpenPosition


class MockBrokerClient:
    def __init__(
        self,
        account: AccountInfo,
        positions: list[OpenPosition] | None = None,
        deals: list[HistoryDeal] | None = None,
    ) -> None:
        self._account = account
        self._positions = positions or []
        self._deals = deals or []
        self.close_all_calls = 0
        self.closed_tickets: list[int] = []

    def get_account_info(self) -> AccountInfo:
        return self._account

    def get_open_positions(self) -> list[OpenPosition]:
        return list(self._positions)

    def get_today_deals(self) -> list[HistoryDeal]:
        return list(self._deals)

    def get_yesterday_deals(self) -> list[HistoryDeal]:
        return []

    def get_server_offset(self) -> timedelta:
        return timedelta(0)

    def close_all_positions(self) -> list[str]:
        self.close_all_calls += 1
        closed = [f"closed {p.symbol} {p.volume}" for p in self._positions]
        self._positions = []
        return closed

    def close_position(self, ticket: int) -> str:
        self.closed_tickets.append(ticket)
        self._positions = [p for p in self._positions if p.ticket != ticket]
        return f"closed position {ticket}"


def make_account(balance: float = 1000.0, equity: float | None = None,
                 margin: float = 0.0, profit: float = 0.0) -> AccountInfo:
    return AccountInfo(
        login=1, broker="Mock", server="Mock", currency="USD",
        balance=balance, equity=equity if equity is not None else balance,
        margin=margin, margin_free=(equity if equity is not None else balance) - margin,
        profit=profit,
    )


def make_deal(position_id: int, entry: str, profit: float = 0.0) -> HistoryDeal:
    return HistoryDeal(
        ticket=position_id, position_id=position_id, symbol="EURUSD",
        direction="BUY", entry=entry, volume=0.1, price=1.1, profit=profit,
        time=datetime.now(tz=timezone.utc),
    )
