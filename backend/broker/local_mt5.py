"""LocalMT5Client — the production BrokerClient for the terminal farm.

It's just an MT5Service bound to a specific account/terminal. One instance per
worker process (the MetaTrader5 package is one-terminal-per-process).
"""
from __future__ import annotations

from backend.services.mt5_service import MT5Service


def make_local_client(
    *, login: int, password: str, server: str, terminal_path: str | None
) -> MT5Service:
    return MT5Service(
        login=login, password=password, server=server, path=terminal_path
    )
