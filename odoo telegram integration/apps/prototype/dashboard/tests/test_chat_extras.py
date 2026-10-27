"""Pictures in the chat (core.images, state, both providers, hand-over to the agents) and the history
views (change descriptions, timeline and conversation dialogs). No network: scripted fake clients."""

from __future__ import annotations

import io
import sqlite3

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from assistant import llm, orchestrator
from core import images, state
from core.settings import load_settings
from tests.conftest import DASHBOARD_DIR
from tests.test_agents import FakeAnthropic, FakeOpenAI, call, context, say
from ui import chat, layout
from ui.i18n import t


def _png(width: int = 300, height: int = 200, color=(79, 70, 229)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------- pictures


def test_pictures_are_checked_scaled_stored_and_loaded():
    settings = load_settings()
    data, extension = images.normalize(_png(4000, 1000))
    with Image.open(io.BytesIO(data)) as scaled:
        assert max(scaled.size) == images.MAX_SIDE
    assert extension == "png"
    picture = images.store(settings, _png())
    assert images.NAME_RE.match(picture.name) and picture.media_type == "image/png"
    assert images.load(settings, picture.name).data == picture.data
    assert picture.base64 and images.load(settings, "nope.png") is None
    for bad_name in ("../state.db", "..\\x.png", "a" * 32 + ".png/../x"):
        assert images.load(settings, bad_name) is None
    for raw in (b"", b"not a picture at all", b"\x89PNG\r\n\x1a\n broken"):
        with pytest.raises(images.ImageError):
            images.normalize(raw)


def test_chat_messages_keep_their_pictures_and_old_databases_get_the_column(tmp_path, monkeypatch):
    settings = load_settings()
    state.chat_add(settings, "user", "Shu joy", files=["a" * 32 + ".png"])
    assert state.chat_recent(settings, 1)[0]["files"] == ["a" * 32 + ".png"]

    old = tmp_path / "old_state.db"
    conn = sqlite3.connect(old)
    conn.execute("CREATE TABLE chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, "
                 "role TEXT NOT NULL, text TEXT NOT NULL, agents TEXT NOT NULL DEFAULT '', "
                 "changes_json TEXT NOT NULL DEFAULT '[]', request_ids TEXT NOT NULL DEFAULT '', "
                 "input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0)")
    conn.execute("INSERT INTO chat_messages (created_at, role, text) VALUES ('2026-10-17 07:00:00', 'user', 'eski')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("DASHBOARD_STATE_DB_PATH", str(old))
    load_settings.cache_clear()
    try:
        old_settings = load_settings()
        messages = state.chat_recent(old_settings, 5)
        assert messages[0]["text"] == "eski" and messages[0]["files"] == []
        state.chat_add(old_settings, "user", "yangi", files=["b" * 32 + ".jpg"])
        assert state.chat_recent(old_settings, 1)[0]["files"] == ["b" * 32 + ".jpg"]
    finally:
        load_settings.cache_clear()


def test_providers_send_pictures_in_their_own_format():
    settings = load_settings()
    picture = images.store(settings, _png())
    message = llm.user_message("Shu joyini o'zgartir", [picture])
    openai_message = llm.OpenAIProvider(None, settings).start("sys", [message], "")["messages"][1]
    assert openai_message["content"][0] == {"type": "text", "text": "Shu joyini o'zgartir"}
    assert openai_message["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    anthropic_message = llm.AnthropicProvider(None, settings).start("sys", [message], "")["messages"][0]
    assert anthropic_message["content"][0]["type"] == "image"
    assert anthropic_message["content"][0]["source"]["media_type"] == "image/png"
    assert anthropic_message["content"][-1] == {"type": "text", "text": "Shu joyini o'zgartir"}
    plain = llm.OpenAIProvider(None, settings).start("sys", [llm.user_message("salom")], "")["messages"][1]
    assert plain == {"role": "user", "content": "salom"}


@pytest.mark.parametrize("fake", [FakeOpenAI, FakeAnthropic])
def test_the_view_agent_sees_the_screenshot(fake, monkeypatch):
    monkeypatch.setenv("DASHBOARD_CHAT_PROVIDER", fake.provider)
    load_settings.cache_clear()
    try:
        settings = load_settings()
        picture = images.store(settings, _png())
        client = fake(call(("o1", "change_view", {"instruction": "Rasmdagi soatlar xaritasini qo'sh"})),
                      call(("v1", "set_visible", {"page": "overview", "visible": True,
                                                  "targets": [{"block_id": "ov.hours", "item_ids": None}]})),
                      say("Qo'shildi."))
        reply = orchestrator.handle(settings, "Shu joyini qo'sh", [], context(), client=client, pictures=[picture])
        assert reply.changes and state.current_layout(settings)["pages"]["overview"]["shown"] == ["ov.hours"]
        for request in client.requests[:2]:  # the orchestrator and the view agent both got the picture
            user = [m for m in request["messages"] if m["role"] == "user"][-1 if request is client.requests[0] else 0]
            kinds = [part.get("type") for part in user["content"]] if isinstance(user["content"], list) else []
            assert ("image_url" in kinds) or ("image" in kinds), kinds
    finally:
        load_settings.cache_clear()


def test_history_mentions_earlier_pictures():
    messages = orchestrator.history_messages(
        [{"role": "user", "text": "Shu joy", "agents": "", "changes": [], "files": ["a" * 32 + ".png"]}], "Qaytar")
    assert "1 ta rasm biriktirilgan edi" in messages[0]["content"]


# --------------------------------------------------------------------------- describing changes


def test_change_descriptions():
    added = chat.describe("Umumiy: qo'shildi — Hafta kuni × soat xaritasi, Guruhlar reytingi jadvali (Mijoz, 7 kun)")
    assert added == {"kind": "add", "page": "Umumiy", "verb": "qo'shildi",
                     "items": ["Hafta kuni × soat xaritasi", "Guruhlar reytingi jadvali (Mijoz, 7 kun)"]}
    assert chat.describe("Umumiy: olib tashlandi — X")["kind"] == "remove"
    assert chat.describe("Umumiy: yashirildi — X")["kind"] == "remove"  # older wording
    assert chat.describe("Umumiy: ko'chirildi — X")["kind"] == "move"
    assert chat.describe(t("layout.page_shown", page="Tizim va ma'lumot sifati"))["kind"] == "add"
    undo = chat.describe(t("layout.undone", summary="Umumiy: qo'shildi — X; Umumiy: qo'shildi — Y"))
    assert undo["kind"] == "undo" and undo["items"] == ["Umumiy: qo'shildi — X", "Umumiy: qo'shildi — Y"]
    assert chat.describe(t("layout.reset_all"))["kind"] == "reset"
    assert chat.describe(t("layout.restored", time="17-oktabr, 11:38"))["kind"] == "restore"
    assert chat.describe("nimadir boshqa")["kind"] == "other"
    assert chat.describe(t("layout.page_created", page="Nazorat"))["kind"] == "add"
    assert chat.describe(t("layout.page_deleted", page="Nazorat"))["kind"] == "remove"
    assert chat.describe(t("layout.page_renamed", old="Guruhlar", new="Mijozlar"))["kind"] == "rename"
    assert chat.describe("Umumiy: nomi o'zgartirildi — So'nggi shikoyatlar → Bugun")["kind"] == "rename"
    assert chat.describe("Umumiy: yangi bo'lim qo'shildi — Toifalar")["kind"] == "add"
    assert chat.describe("Umumiy: bo'lim o'zgartirildi — Toifalar")["kind"] == "edit"
    # a design change stored before the design agent was removed (2026-10-22) is still described
    assert chat.describe("Dizayn: o'zgartirildi — asosiy rang, shrift")["kind"] == "other"
    assert chat.describe("Dizayn asl holatiga qaytarildi", "reset")["kind"] == "reset"


# --------------------------------------------------------------------------- dialogs render


def _seed_history() -> None:
    settings = load_settings()
    state.update_layout(settings, lambda o: layout.set_visible(o, "overview", [{"block_id": "ov.hours"}], True),
                        group_id="g1", source="chat")
    state.update_layout(settings, lambda o: layout.set_page_visible(o, "system", True), group_id="g2", source="chat")
    state.undo(settings, group_id="u1")
    for number, change in enumerate((lambda o: layout.rename(o, "overview", "ov.latest", "Bugun"),
                                     lambda o: layout.create_page(o, "p-0000000001", "Nazorat"))):
        state.update_layout(settings, change, group_id=f"g{number + 3}", source="chat")
    picture = images.store(settings, _png())
    state.chat_add(settings, "user", "Shu joyini qo'sh", files=[picture.name])
    state.chat_add(settings, "assistant", "Qo'shildi.", agents="view", changes=["Umumiy: qo'shildi — Hafta kuni × soat"])


@pytest.mark.parametrize("button", ["chat_history", "chat_expand", "chat_reset_open"])
def test_chat_dialogs_render(button, monkeypatch):
    _seed_history()
    monkeypatch.setenv("DASHBOARD_OPENAI_API_KEY", "test-key")
    load_settings.cache_clear()
    try:
        app = AppTest.from_file(str(DASHBOARD_DIR / "app.py"), default_timeout=120)
        app.run()
        assert not app.exception
        app.button(key=button).click().run()
        assert not app.exception, [item.value for item in app.exception]
    finally:
        monkeypatch.setenv("DASHBOARD_OPENAI_API_KEY", "")
        load_settings.cache_clear()
