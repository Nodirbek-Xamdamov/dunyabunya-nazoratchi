"""Telegram bots of the agents created on the «AI agentlar» page.

Runs next to the dashboard (run_dashboard.ps1 starts and stops it): ``.venv\\Scripts\\python.exe
agent_bots.py`` from the dashboard folder. Every 15 seconds it reads which agents have a working bot
connection and keeps one reader thread per bot. A reader long-polls Telegram (getUpdates), answers
private messages from the people allowed on the page with the agent (assistant.team) and keeps the last
messages of each chat (core.state.bot_messages) so the agent remembers the conversation.

A bot that another program also reads (Telegram answers 409) or whose token stopped working is
stopped and marked on the agent's page -- never fought over, because two readers of one bot steal each
other's messages. Only one copy of this program runs (a local port is held as a lock). Message texts
and tokens are never logged."""

from __future__ import annotations

import logging
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

from assistant import llm, registry, team  # noqa: E402
from core import secrets, state  # noqa: E402
from core import telegram as tg  # noqa: E402
from core.settings import Settings, load_settings  # noqa: E402
from ui.i18n import t  # noqa: E402

log = logging.getLogger("dashboard.bots")

SYNC_SECONDS = 15
POLL_TIMEOUT = 25
LOCK_PORT = 47502  # held while this program runs: a second copy exits
DENIED_REPEAT = 3600.0  # tell a stranger their ID at most once an hour


class BotReader(threading.Thread):
    """Reads one bot and answers with one agent."""

    def __init__(self, settings: Settings, agent_id: str, connection_id: str, token: str) -> None:
        super().__init__(name=f"bot-{connection_id}", daemon=True)
        self.settings, self.agent_id, self.connection_id, self.token = settings, agent_id, connection_id, token
        self.stopping = threading.Event()
        self._told: dict[int, float] = {}

    def stop(self) -> None:
        self.stopping.set()

    def _fail(self, status: str, text: str) -> None:
        state.set_connection_status(self.settings, self.connection_id, status, text)
        log.warning("bot %s (agent %s) stopped: %s", self.connection_id, self.agent_id, status)
        self.stop()

    def send(self, chat_id: int, text: str) -> None:
        for part in tg.split_text(text):
            tg.call(self.token, "sendMessage", {"chat_id": chat_id, "text": part, "disable_web_page_preview": True},
                    timeout=20)

    def run(self) -> None:
        log.info("bot %s (agent %s): reading", self.connection_id, self.agent_id)
        offset, wait = state.bot_offset(self.settings, self.connection_id), 5.0
        while not self.stopping.is_set():
            try:
                updates = tg.call(self.token, "getUpdates", {"offset": offset, "timeout": POLL_TIMEOUT,
                                                             "allowed_updates": ["message"]}, timeout=POLL_TIMEOUT + 10)
                wait = 5.0
            except tg.TelegramError as exc:
                if exc.code == 409:
                    self._fail("conflict", t("ag.tg.conflict"))
                elif exc.code in (401, 403, 404):
                    self._fail("error", t("ag.tg.invalid"))
                else:
                    log.warning("bot %s: Telegram unreachable (%s), retry in %.0f s", self.connection_id, exc.code, wait)
                    self.stopping.wait(wait)
                    wait = min(wait * 2, 120.0)
                continue
            for update in updates or []:
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                state.set_bot_offset(self.settings, self.connection_id, offset)  # first: a crash never answers twice
                try:
                    self.handle(update.get("message") or {})
                except Exception:  # noqa: BLE001 -- one bad message must not stop the bot
                    log.exception("bot %s: a message failed", self.connection_id)
        log.info("bot %s: stopped", self.connection_id)

    def _allowed(self, config: dict, sender: dict) -> bool:
        username = str(sender.get("username") or "").lower()
        return sender.get("id") in (config.get("allowed_ids") or []) or (
            bool(username) and username in (config.get("allowed_names") or []))

    def handle(self, message: dict) -> None:
        chat, sender = message.get("chat") or {}, message.get("from") or {}
        if chat.get("type") != "private" or not sender or sender.get("is_bot"):
            return  # private chats with people only
        chat_id = int(chat["id"])
        row = state.connection(self.settings, self.connection_id)  # fresh: the allowed people may have changed
        if row is None or row["status"] != "ok":
            self.stop()
            return
        if not self._allowed(row["config"], sender):
            now = time.monotonic()
            if now - self._told.get(chat_id, -DENIED_REPEAT) >= DENIED_REPEAT:
                self._told[chat_id] = now
                self.send(chat_id, t("bot.denied", id=sender.get("id")))
            log.info("bot %s: a message from someone who is not allowed", self.connection_id)
            return
        agents = registry.load(self.settings)
        agent = agents.get(self.agent_id)
        if agent is None:
            self.stop()
            return
        text = str(message.get("text") or "").strip()
        if text.startswith("/start"):
            self.send(chat_id, t("bot.start", name=agent.name, summary=agent.role))
            return
        if text.startswith("/new"):
            state.clear_bot_history(self.settings, self.connection_id, chat_id)
            self.send(chat_id, t("bot.new"))
            return
        if not text:
            self.send(chat_id, t("bot.text_only"))
            return
        limit = self.settings.agents.bot_daily_limit
        if limit and state.bot_questions_since(self.settings, self.connection_id,
                                               state.local_midnight_utc(self.settings.tz)) >= limit:
            self.send(chat_id, t("bot.limit"))
            return
        try:
            tg.call(self.token, "sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)
        except tg.TelegramError:
            pass
        self.send(chat_id, self.answer(agents, agent, chat_id, sender, text))

    def answer(self, agents: dict, agent: registry.Agent, chat_id: int, sender: dict, text: str) -> str:
        history = state.bot_history(self.settings, self.connection_id, chat_id, self.settings.agents.bot_history)
        state.add_bot_message(self.settings, self.connection_id, chat_id, "user", text)
        name = " ".join(part for part in (sender.get("first_name"), sender.get("last_name")) if part) or "?"
        who = f"{name} (@{sender['username']})" if sender.get("username") else name
        context = {"group_id": state.new_group("tg"), "page_id": None, "period": None,
                   "now": datetime.now(self.settings.tz), "channel": {"kind": "telegram", "user": who}}
        call = team.Call(self.settings, llm.pool(), context, agents=agents)
        try:
            reply = team.run_custom(call, agent, text, history)
        except llm.AgentError as exc:
            reply = str(exc)
        except Exception:  # noqa: BLE001
            log.exception("bot %s: the agent failed", self.connection_id)
            reply = t("bot.error")
        state.add_bot_message(self.settings, self.connection_id, chat_id, "assistant", reply)
        log.info("bot %s: answered (agents=%s, tokens in=%d out=%d)", self.connection_id, ",".join(call.used),
                 call.usage.input_tokens, call.usage.output_tokens)
        return reply


def wanted(settings: Settings) -> dict[str, tuple[str, str]]:
    """connection id -> (agent id, token) for every created agent with a working bot."""
    result = {}
    for row in state.custom_agents(settings):
        connection_id = row.get("telegram_connection")
        connection = state.connection(settings, connection_id)
        if not connection or connection["status"] != "ok" or not connection["secret"]:
            continue
        try:
            result[connection_id] = (row["id"], secrets.unprotect(connection["secret"]))
        except secrets.SecretsUnavailable as exc:
            state.set_connection_status(settings, connection_id, "error", str(exc))
    return result


def sync(settings: Settings, readers: dict[str, BotReader], stopping: dict[str, BotReader]) -> None:
    """Starts and stops readers so they match the page. A bot is never read by two readers: a new
    reader for a connection starts only after the old one has finished its last long poll."""
    want = wanted(settings)
    for connection_id, reader in list(readers.items()):
        if (connection_id not in want or want[connection_id] != (reader.agent_id, reader.token)
                or not reader.is_alive()):
            reader.stop()
            stopping[connection_id] = readers.pop(connection_id)
    for connection_id in [key for key, reader in stopping.items() if not reader.is_alive()]:
        stopping.pop(connection_id)
    for connection_id, (agent_id, token) in want.items():
        if connection_id not in readers and connection_id not in stopping:
            readers[connection_id] = BotReader(settings, agent_id, connection_id, token)
            readers[connection_id].start()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        log.error("agent_bots is already running (port %d is held) -- this copy exits", LOCK_PORT)
        return
    settings = load_settings()
    readers: dict[str, BotReader] = {}
    stopping: dict[str, BotReader] = {}
    log.info("agent_bots started")
    while True:
        try:
            sync(settings, readers, stopping)
        except Exception:  # noqa: BLE001 -- keep running; the next round tries again
            log.exception("agent_bots: sync failed")
        time.sleep(SYNC_SECONDS)


if __name__ == "__main__":
    main()
