"""W1-01 offline tests (no network, no token). Run from apps/w1-tg-ingest:
    .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import ast
import asyncio
import logging
import re
import sys
import unittest
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from w1ingest.allowlist import chat_kind  # noqa: E402
from w1ingest.listen import make_handler  # noqa: E402
from w1ingest.readonly import ALLOWED, ReadOnlyClient  # noqa: E402

WRITE_METHODS = ["send_message", "send_file", "edit_message", "delete_messages", "forward_messages", "pin_message",
                 "unpin_message", "send_read_acknowledge", "kick_participant", "edit_permissions", "edit_admin",
                 "send_reaction", "action", "edit_folder", "delete_dialog", "upload_file", "__call__"]
FORBIDDEN_RE = re.compile(r"\.(send_message|send_file|edit_message|delete_messages|forward_messages|pin_message|"
                          r"unpin_message|send_read_acknowledge|kick_participant|edit_permissions|edit_admin|send_reaction)\s*\(|"
                          r"\b(SendMessageRequest|SendMediaRequest|EditMessageRequest|DeleteMessagesRequest|ForwardMessagesRequest|"
                          r"SendReactionRequest|UpdatePinnedMessageRequest|SetTypingRequest)\b")


class FakeTelethon:
    def __getattr__(self, name):
        return lambda *a, **k: f"called {name}"


class ReadOnlyTests(unittest.TestCase):
    def test_write_methods_are_unreachable(self):
        c = ReadOnlyClient(FakeTelethon())
        for name in WRITE_METHODS:
            with self.assertRaises(AttributeError, msg=name):
                getattr(c, name)
        self.assertEqual(c.get_me(), "called get_me")
        self.assertTrue({"get_me", "add_event_handler"} <= ALLOWED)
        self.assertFalse(set(WRITE_METHODS) & ALLOWED)

    def test_wrapper_cannot_be_patched(self):
        c = ReadOnlyClient(FakeTelethon())
        with self.assertRaises(AttributeError):
            c.send_message = print

    def test_no_write_calls_in_source(self):
        hits = []
        for f in (HERE / "w1ingest").rglob("*.py"):
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if FORBIDDEN_RE.search(line):
                    hits.append(f"{f.name}:{n}: {line.strip()}")
        self.assertEqual(hits, [])

    def test_no_update_or_logout_calls(self):
        """D-36: the legacy bot polls the same token, so W1 never calls Bot API getUpdates/setWebhook/
        deleteWebhook/logOut/close and never logs the MTProto session out."""
        for name in ("log_out", "catch_up_all", "__call__"):
            self.assertNotIn(name, ALLOWED)
        bad = re.compile(r"getUpdates|setWebhook|deleteWebhook|logOut|/close\b|\blog_out\b|LogOutRequest|"
                         r"GetUpdatesRequest|ResetAuthorizationsRequest", re.I)
        hits = []
        for f in (HERE / "w1ingest").rglob("*.py"):
            tree = ast.parse(f.read_text(encoding="utf-8"))
            docs = {id(n.body[0].value) for n in ast.walk(tree)
                    if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
            for node in ast.walk(tree):  # names, attributes and string literals (URLs), docstrings excluded
                text = (node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute)
                        else node.value if isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and id(node) not in docs else "")
                if bad.search(text):
                    hits.append(f"{f.name}:{node.lineno}: {text}")
        self.assertEqual(hits, [])


class FakeAllow:
    def __init__(self, ids):
        self.ids = set(ids)
        self.noted = []

    def allowed(self, chat_id):
        return chat_id in self.ids

    def note_unknown(self, chat_id, title, kind):
        self.noted.append((chat_id, title, kind))
        return True


class Megagroup:
    megagroup, broadcast, title = True, False, "Begona guruh"


class FakeEvent:
    def __init__(self, chat_id, msg_id=1):
        self.chat_id, self.id, self.sender_id, self.media = chat_id, msg_id, 555, None

    @property
    def raw_text(self):
        raise AssertionError("message text must not be read")

    text = message = raw_text

    async def get_chat(self):
        return Megagroup()


class AllowlistTests(unittest.TestCase):
    def test_only_allowlisted_chats_are_accepted_and_text_is_never_read(self):
        allow, stats = FakeAllow({-1001}), Counter()
        handler = make_handler(allow, stats, logging.getLogger("test"))
        asyncio.run(handler(FakeEvent(-1001, 10)))
        asyncio.run(handler(FakeEvent(-1002, 11)))
        asyncio.run(handler(FakeEvent(None, 12)))
        self.assertEqual(stats, Counter({"accepted": 1, "dropped": 2}))
        self.assertEqual(allow.noted[0], (-1002, "Begona guruh", "supergroup"))

    def test_chat_kind(self):
        self.assertEqual(chat_kind(Megagroup()), "supergroup")


if __name__ == "__main__":
    unittest.main()
