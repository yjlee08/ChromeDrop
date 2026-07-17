#!/usr/bin/env python3
"""
One-time Telegram setup helper.

Finds your CHAT_ID and sends a test message, so you can confirm the bot can
reach you before running the monitor 24/7.

Usage:
  1. Create a bot: message @BotFather on Telegram, send /newbot, copy the token.
  2. Put the token in .env as BOT_TOKEN (see .env.example).
  3. Open Telegram and send your new bot ANY message (e.g. "hi").
  4. Run:  python tg_setup.py

It will print the chat id(s) that have messaged the bot. Copy the right one
into .env as CHAT_ID, then run it again to receive a test message.
"""

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def die(msg: str) -> None:
    print(msg)
    sys.exit(1)


def main() -> None:
    if not BOT_TOKEN:
        die("BOT_TOKEN is not set. Add it to .env (see .env.example), then re-run.")

    # Sanity-check the token.
    me = requests.get(f"{API}/getMe", timeout=20).json()
    if not me.get("ok"):
        die(f"Token rejected by Telegram: {me.get('description')}")
    print(f"Bot OK: @{me['result'].get('username')}")

    if CHAT_ID:
        r = requests.post(
            f"{API}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": "✅ Chrome Hearts drop monitor is linked. "
                        "You'll get alerts here for new drops and restocks.",
                "parse_mode": "HTML",
            },
            timeout=20,
        ).json()
        if r.get("ok"):
            print(f"Test message sent to CHAT_ID={CHAT_ID}. Check Telegram!")
        else:
            print(f"Could not send to CHAT_ID={CHAT_ID}: {r.get('description')}")
            print("Double-check the id below, or message the bot first.")
        # Still show discovered chats to help debugging.

    updates = requests.get(f"{API}/getUpdates", timeout=20).json()
    if not updates.get("ok"):
        die(f"getUpdates failed: {updates.get('description')}")

    chats = {}
    for u in updates.get("result", []):
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name")

    if not chats:
        print("\nNo chats found yet. Open Telegram, send your bot a message "
              "(e.g. 'hi'), then run this again.")
        return

    print("\nChats that have messaged your bot:")
    for cid, name in chats.items():
        print(f"  CHAT_ID={cid}   ({name})")
    if not CHAT_ID:
        print("\nCopy the correct CHAT_ID into .env, then run `python tg_setup.py` "
              "again to get a test message.")


if __name__ == "__main__":
    main()
