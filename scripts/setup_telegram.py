"""Interactive helper to finish Telegram setup.

Usage (from project root, venv active):
    python -m scripts.setup_telegram <BOT_TOKEN>

It will:
  1. Validate the token (getMe).
  2. Find your chat id from recent messages (getUpdates) — so FIRST send any
     message to your bot in Telegram, then run this.
  3. Write TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID into .env.
  4. Send a confirmation message to that chat.

No external deps beyond what's already installed (httpx).
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

API = "https://api.telegram.org"
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def _get(token: str, method: str, **params):
    resp = httpx.get(f"{API}/bot{token}/{method}", params=params, timeout=15.0)
    data = resp.json()
    if not data.get("ok"):
        raise SystemExit(f"Telegram API error on {method}: {data}")
    return data["result"]


def find_chat_id(token: str) -> str | None:
    updates = _get(token, "getUpdates")
    chat_id = None
    for upd in updates:
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if "id" in chat:
            chat_id = str(chat["id"])  # take the most recent
    return chat_id


def update_env(token: str, chat_id: str) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    out = []
    seen_token = seen_chat = False
    for line in lines:
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            out.append(f"TELEGRAM_BOT_TOKEN={token}")
            seen_token = True
        elif line.startswith("TELEGRAM_CHAT_ID="):
            out.append(f"TELEGRAM_CHAT_ID={chat_id}")
            seen_chat = True
        else:
            out.append(line)
    if not seen_token:
        out.append(f"TELEGRAM_BOT_TOKEN={token}")
    if not seen_chat:
        out.append(f"TELEGRAM_CHAT_ID={chat_id}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m scripts.setup_telegram <BOT_TOKEN>")
    token = sys.argv[1].strip()

    me = _get(token, "getMe")
    print(f"Token OK — bot: @{me.get('username')} ({me.get('first_name')})")

    chat_id = find_chat_id(token)
    if not chat_id:
        raise SystemExit(
            "No chat found. Open Telegram, send any message to your bot "
            "(e.g. 'hi'), then run this command again."
        )
    print(f"Found chat id: {chat_id}")

    update_env(token, chat_id)
    print(f"Wrote TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to {ENV_PATH}")

    httpx.post(
        f"{API}/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": "✅ Zanzer is connected to Telegram."},
        timeout=15.0,
    )
    print("Sent a confirmation message — check your Telegram!")


if __name__ == "__main__":
    main()
