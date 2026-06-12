"""Broker abstraction — decouples risk/enforcement from the data source.

A worker drives one account through a `BrokerClient`. In production the
implementation is `LocalMT5Client` (the `MetaTrader5` package bound to one
terminal, one per worker process — the terminal-farm model). Tests use
`MockBrokerClient`.

The existing `MT5Service` already provides these methods structurally, so it
satisfies this Protocol without changes.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Protocol, runtime_checkable

from backend.models import AccountInfo, HistoryDeal, OpenPosition


@runtime_checkable
class BrokerClient(Protocol):
    def get_account_info(self) -> AccountInfo: ...
    def get_open_positions(self) -> list[OpenPosition]: ...
    def get_today_deals(self) -> list[HistoryDeal]: ...
    def get_yesterday_deals(self) -> list[HistoryDeal]: ...
    def get_server_offset(self) -> timedelta: ...
    def close_all_positions(self) -> list[str]: ...
    def close_position(self, ticket: int) -> str: ...
