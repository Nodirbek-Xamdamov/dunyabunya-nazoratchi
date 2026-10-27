"""Read-only listener skeleton (W1-01). Logs metadata of messages from allowed chats; does not store them yet
(that is W1-02). Messages from other chats are dropped without reading their text.

    .venv\\Scripts\\python.exe -m w1ingest.listen [--seconds 120]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from collections import Counter

from genesis_common import load_config, setup_logging
from genesis_queue import connect

from . import APP
from .allowlist import Allowlist, chat_kind
from .readonly import open_client


def make_handler(allow: Allowlist, stats: Counter, log: logging.Logger):
    async def on_message(event) -> None:
        chat_id = event.chat_id
        if not allow.allowed(chat_id):
            stats["dropped"] += 1
            log.info("dropped chat=%s id=%s", chat_id, event.id)  # ids only: proves receipt, text is not read
            if chat_id is None:
                return
            chat = await event.get_chat()
            if allow.note_unknown(chat_id, getattr(chat, "title", None), chat_kind(chat)):
                log.info("unknown chat noted (enabled=false): %s %r", chat_id, getattr(chat, "title", None))
            return  # the message itself is not touched
        stats["accepted"] += 1
        log.info("message chat=%s id=%s sender=%s media=%s", chat_id, event.id, event.sender_id,
                 type(event.media).__name__ if event.media else None)
    return on_message


async def run(seconds: float | None) -> Counter:
    from telethon import events

    cfg = load_config(APP)
    log = setup_logging(APP, cfg)
    allow = Allowlist(connect(cfg["app"]["bot"]["db_role"]))
    client = await open_client(cfg)
    me = await client.get_me()
    log.info("connected as @%s (id %s); allowed chats: %d", me.username, me.id, len(allow.ids))
    stats: Counter = Counter()
    client.add_event_handler(make_handler(allow, stats, log), events.NewMessage())
    try:
        if seconds:
            await asyncio.sleep(seconds)
        else:
            await client.run_until_disconnected()
    finally:
        await client.disconnect()
    log.info("stopped: %s", dict(stats))
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=None)
    print(dict(asyncio.run(run(ap.parse_args().seconds))))


if __name__ == "__main__":
    main()
