"""W1-01 connection check. Prints no secrets.

    .venv\\Scripts\\python.exe -m w1ingest.check_bot

1. Bot API getMe (token only; getMe does not consume updates). No other Bot API method is ever called (D-36:
   no getUpdates, setWebhook, deleteWebhook, logOut, close).
2. MTProto login as the bot (needs telegram_api.env) + get_me. The first run creates secrets/w1_bot.session; later
   runs reuse it and do not log in again.
The legacy bot (same token) is not inspected here: its files, logs and process are off limits (D-36).
"""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

from genesis_common import SecretError, load_config, read_secret, redact

from . import APP
from .readonly import open_client, session_path


def bot_api_getme(token: str) -> dict:
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=20) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": redact(f"HTTP {e.code} {e.reason}")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": redact(f"{type(e).__name__}: {e}")}
    res = data.get("result", {})
    keep = ("id", "is_bot", "username", "first_name", "can_join_groups", "can_read_all_group_messages")
    return {"ok": data.get("ok", False), **{k: res.get(k) for k in keep}}


async def mtproto_getme(cfg) -> dict:
    existed = session_path(cfg).with_suffix(".session").is_file()
    client = await open_client(cfg)
    try:
        me = await client.get_me()
        return {"ok": True, "id": me.id, "username": me.username, "bot": me.bot, "session_reused": existed}
    finally:
        await client.disconnect()


def main() -> None:
    cfg = load_config(APP)
    bot = cfg["app"]["bot"]
    out: dict = {}
    try:
        out["bot_api_getMe"] = bot_api_getme(read_secret(bot["token_secret"], cfg))
    except SecretError as e:
        out["bot_api_getMe"] = {"ok": False, "error": str(e)}
    try:
        out["mtproto_get_me"] = asyncio.run(mtproto_getme(cfg))
    except SecretError as e:
        out["mtproto_get_me"] = {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        out["mtproto_get_me"] = {"ok": False, "error": redact(f"{type(e).__name__}: {e}")}
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
