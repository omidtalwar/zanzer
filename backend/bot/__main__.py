"""Allow `python -m backend.bot` to start the bot."""
from __future__ import annotations

import asyncio

from backend.bot.app import run

if __name__ == "__main__":
    asyncio.run(run())
