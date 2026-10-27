"""Read-only wrapper around the Telethon client (rule 4, P-04).

Only the attributes in ALLOWED are reachable; everything else (send_message, edit_message, delete_messages,
forward_messages, reactions, pins, admin actions, ...) raises AttributeError. W1 code gets a ReadOnlyClient,
never the raw TelegramClient.
"""
from __future__ import annotations

from pathlib import Path

from genesis_common import Config, read_secret, read_secret_env

ALLOWED = frozenset({
    "connect", "disconnect", "is_connected", "run_until_disconnected", "catch_up", "loop",
    "get_me", "get_entity",
    "add_event_handler", "remove_event_handler", "list_event_handlers",
    "download_media",   # W1-03
    "get_messages",     # W1-05: fetch missed messages by id
})


class ReadOnlyClient:
    __slots__ = ("_client",)

    def __init__(self, client) -> None:
        object.__setattr__(self, "_client", client)

    def __getattr__(self, name: str):
        if name not in ALLOWED:
            raise AttributeError(f"ReadOnlyClient: '{name}' is not available (W1 only reads, rule 4)")
        return getattr(self._client, name)

    def __setattr__(self, name: str, value) -> None:
        raise AttributeError("ReadOnlyClient is immutable")


def session_path(cfg: Config) -> Path:
    return cfg.path("paths.secrets") / cfg["app"]["bot"]["session_name"]


async def open_client(cfg: Config) -> ReadOnlyClient:
    """Log in as the Genesis bot over MTProto. Secrets are read here and nowhere else."""
    from telethon import TelegramClient  # imported late: offline tests do not need a network stack

    bot = cfg["app"]["bot"]
    token = read_secret(bot["token_secret"], cfg)
    api = read_secret_env(bot["api_secret"], cfg)
    client = TelegramClient(str(session_path(cfg)), int(api["TG_API_ID"]), api["TG_API_HASH"])
    await client.start(bot_token=token)
    return ReadOnlyClient(client)
