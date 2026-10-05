""""Matn (shu chatda)" formati uchun testlar: export_text tool'i, natijaning
Telegram'ga yetkazilishi va "Matn" tugmasi.

CEO talabi: ma'lumot faylda so'ralganda (Excel/HTML) Telegram'ning o'zida matn
ko'rinishida olish ham taklif qilinishi kerak.

Barcha testlar mok'langan — tarmoqqa chiqilmaydi.
"""
from __future__ import annotations

import json
import types

import pytest

import app.services.assistant as assistant_module
import app.services.odoo_tools as odoo_tools
import app.telegram.delivery as delivery
from app.rbac import Role
from app.services.assistant import AssistantResult, FileOut


_EMPLOYEES = [
    {"id": 1, "name": "Aziz Karimov", "job_id": [5, "Sotuv menejeri"], "department_id": [2, "Sotuv"], "work_phone": False},
    {"id": 2, "name": "Dilnoza <Test> & Co", "job_id": False, "department_id": [3, "Moliya"], "work_phone": "+998901234567"},
    {"id": 3, "name": "Bobur Aliyev", "job_id": [6, "Kassir"], "department_id": False, "work_phone": False},
]


@pytest.fixture
def fake_odoo(monkeypatch):
    calls = []

    async def fake_execute_kw(model, method, args, kwargs=None):
        calls.append((model, method, args, kwargs))
        if method == "search_read":
            return _EMPLOYEES
        if method == "search_count":
            return len(_EMPLOYEES)
        if method == "fields_get":
            return {"name": {"string": "Ism"}, "job_id": {"string": "Lavozim"},
                    "department_id": {"string": "Bo'lim"}, "work_phone": {"string": "Telefon"}}
        raise AssertionError(f"unexpected call: {model}.{method}")

    monkeypatch.setattr(odoo_tools._odoo, "execute_kw", fake_execute_kw)
    return calls


# ── export_text ─────────────────────────────────────────────────────────────

async def test_export_text_renders_every_row_with_labels(fake_odoo):
    result = await odoo_tools.export_text(
        model="hr.employee", fields=["name", "job_id", "work_phone"], title="Xodimlar",
    )
    assert result["rows_shown"] == 3 and result["rows_total"] == 3
    text = odoo_tools.pop_text_export(result["text_export"])

    assert text.startswith("<b>Xodimlar</b> — 3 ta yozuv")
    assert "1. <b>Aziz Karimov</b> — Lavozim: Sotuv menejeri" in text
    # Bo'sh qiymatlar tashlab ketiladi, maxsus belgilar HTML uchun ekranlanadi
    assert "2. <b>Dilnoza &lt;Test&gt; &amp; Co</b> — Telefon: +998901234567" in text
    assert "3. <b>Bobur Aliyev</b> — Lavozim: Kassir" in text
    # Matn faqat bir marta olinadi (xotirada qolib ketmaydi)
    assert odoo_tools.pop_text_export(result["text_export"]) is None


async def test_export_text_groups_rows(fake_odoo):
    result = await odoo_tools.export_text(
        model="hr.employee", fields=["name", "job_id"], group_by="department_id",
    )
    text = odoo_tools.pop_text_export(result["text_export"])
    lines = text.split("\n")

    assert "<b>Moliya</b> (1 ta)" in lines
    assert "<b>Sotuv</b> (1 ta)" in lines
    # Bo'limi ko'rsatilmaganlar oxirida, raqamlash har guruhda 1 dan
    assert lines.index("<b>Ko'rsatilmagan</b> (1 ta)") > lines.index("<b>Sotuv</b> (1 ta)")
    assert lines[lines.index("<b>Sotuv</b> (1 ta)") + 1] == "1. <b>Aziz Karimov</b> — Lavozim: Sotuv menejeri"


async def test_export_text_caps_rows_and_says_so(monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        if method == "search_read":
            assert kwargs["limit"] == odoo_tools.TEXT_EXPORT_MAX_ROWS  # 10000 so'ralsa ham
            return [{"id": i, "name": f"S{i:05d}"} for i in range(kwargs["limit"])]
        if method == "search_count":
            return 84207
        return {}

    monkeypatch.setattr(odoo_tools._odoo, "execute_kw", fake_execute_kw)
    result = await odoo_tools.export_text(model="sale.order", fields=["name"], limit=10000)
    text = odoo_tools.pop_text_export(result["text_export"])

    assert result["rows_shown"] == 300 and result["rows_total"] == 84207
    assert "Jami 84,207 ta yozuvdan dastlabki 300 tasi ko'rsatildi" in text
    assert "Excel" in text


async def test_export_text_empty_result(monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        return [] if method == "search_read" else 0

    monkeypatch.setattr(odoo_tools._odoo, "execute_kw", fake_execute_kw)
    assert "topilmadi" in await odoo_tools.export_text(model="hr.employee", fields=["name"])


async def test_export_text_respects_role_model_restriction(monkeypatch):
    """export_text `model`ni erkin qabul qiladi — Sotuv menejeri u orqali Moliya
    ma'lumotini o'qiy olmasligi kerak."""
    async def fake_execute_kw(model, method, args, kwargs=None):
        raise AssertionError(f"Odoo'ga chiqilmasligi kerak edi (model={model})")

    monkeypatch.setattr(odoo_tools._odoo, "execute_kw", fake_execute_kw)
    raw = await assistant_module._exec_tool(
        "export_text", {"model": "account.move", "fields": ["name"]}, Role.SOTUV,
    )
    assert "error" in json.loads(raw)


def test_every_role_with_excel_export_also_has_text_export():
    for role in (Role.CEO, Role.MOLIYA, Role.OMBOR, Role.SOTUV, Role.HR):
        names = {t["name"] for t in assistant_module._get_tools_for_role(role)}
        assert "export_excel" in names and "export_text" in names


# ── ask_with_files: export_text natijasi AssistantResult.texts'ga tushadi ───

async def test_ask_with_files_collects_text_exports(monkeypatch, fake_odoo):
    async def fake_get_recent(session, uid):
        return []

    async def fake_add(*a, **kw):
        return None

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(assistant_module.repo, "get_recent_conversation", fake_get_recent)
    monkeypatch.setattr(assistant_module.repo, "add_conversation_message", fake_add)
    monkeypatch.setattr(assistant_module, "SessionLocal", lambda: _FakeSession())

    responses = [
        types.SimpleNamespace(stop_reason="tool_use", content=[types.SimpleNamespace(
            type="tool_use", id="t1", name="export_text",
            input={"model": "hr.employee", "fields": ["name", "job_id"], "title": "Xodimlar"},
        )]),
        types.SimpleNamespace(stop_reason="end_turn",
                              content=[types.SimpleNamespace(type="text", text="Jami 3 ta xodim.")]),
    ]
    calls = []

    async def fake_create(**kwargs):
        calls.append(kwargs)
        return responses[len(calls) - 1]

    monkeypatch.setattr(assistant_module._client.messages, "create", fake_create)

    result = await assistant_module.ask_with_files(
        1, "xodimlar ro'yxati", role=Role.CEO, exclude_tools=assistant_module.FILE_TOOLS,
    )
    assert result.text == "Jami 3 ta xodim."
    assert len(result.texts) == 1 and "1. <b>Aziz Karimov</b>" in result.texts[0]
    # "Matn" rejimida fayl yaratuvchi tool'lar modelga umuman berilmaydi
    offered = {t["name"] for t in calls[0]["tools"]}
    assert "export_text" in offered
    assert not offered & assistant_module.FILE_TOOLS


# ── delivery: tugmalar va yetkazish ─────────────────────────────────────────

class _FakeBot:
    def __init__(self):
        self.sent = []
        self.docs = []
        self.chat_actions = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        msg = types.SimpleNamespace(message_id=len(self.sent))

        async def delete():
            self.sent.remove(kwargs)

        msg.delete = delete
        return msg

    async def send_document(self, **kwargs):
        self.docs.append(kwargs)

    async def send_chat_action(self, **kwargs):
        self.chat_actions.append(kwargs)


def _callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _file(tmp_path, name="hisobot_abc12345.html") -> FileOut:
    path = tmp_path / name
    path.write_text("<html></html>", encoding="utf-8")
    return FileOut(kind="html", filename=name, path=str(path), url=f"http://x/reports/{name}")


@pytest.fixture(autouse=True)
def _clean_entries(monkeypatch):
    monkeypatch.setattr(delivery, "_entries", {})


def _keys(markup) -> list[str]:
    """Tugmalardagi (format, kalit) juftliklaridan kalitlar — har javobning o'z kaliti bor."""
    return [c.split(":", 2)[2] for c in _callbacks(markup)]


def test_list_question_offers_all_three_formats():
    markup = delivery.offer_formats(
        "dm-1", "xodimlar ro'yxatini ber", AssistantResult(text="Jami 187 ta."),
        chat_id=1, thread_id=None, user_key=1,
    )
    (key,) = set(_keys(markup))
    assert key.startswith("dm-1-")
    assert _callbacks(markup) == [f"fmt:excel:{key}", f"fmt:html:{key}", f"fmt:text:{key}"]
    assert delivery._entries[key]["question"] == "xodimlar ro'yxatini ber"


def test_file_answer_offers_text_version(tmp_path):
    """Statistika so'rovi avtomatik HTML fayl bo'lib keladi — ostida "Matn" taklifi bo'lishi shart."""
    result = AssistantResult(text="Hisobot tayyorlandi", files=[_file(tmp_path)])
    markup = delivery.offer_formats("grp-main", "kompaniya statistikasi", result,
                                    chat_id=-100, thread_id=None, user_key=-100)
    (callback_data,) = _callbacks(markup)
    assert callback_data.startswith("fmt:text:grp-main-")


async def test_button_under_an_older_answer_reruns_that_older_question(monkeypatch):
    """Ilgari kalit faqat chat edi: ikkinchi savol birinchisini bosib ketar va BIRINCHI javob
    ostidagi "Excel" bosilganda IKKINCHI savolning ma'lumoti kelardi."""
    kw = dict(chat_id=1, thread_id=None, user_key=1)
    first = delivery.offer_formats("dm-1", "xodimlar ro'yxati", AssistantResult(text="Jami 187 ta."), **kw)
    second = delivery.offer_formats("dm-1", "mahsulotlar ro'yxati", AssistantResult(text="Jami 31 678 ta."), **kw)
    assert _keys(first)[0] != _keys(second)[0]

    asked = []

    async def fake_ask_with_files(user_key, question, **kwargs):
        asked.append(question)
        return AssistantResult(text="Tayyor.")

    monkeypatch.setattr(delivery, "ask_with_files", fake_ask_with_files)
    callback, _ = _callback(_callbacks(first)[0])
    await delivery.on_format_choice(callback, _FakeBot())
    assert asked == ["xodimlar ro'yxati"]


async def test_group_format_button_is_ceo_only(monkeypatch):
    """Guruhda bot faqat CEO'ga xizmat qiladi — boshqa a'zo tugmani bossa hech narsa qayta yaratilmaydi."""
    monkeypatch.setattr(delivery.ceo_state.settings, "CEO_TELEGRAM_USER_ID", 777)
    monkeypatch.setattr(delivery.ceo_state.settings, "CEO_TELEGRAM_USERNAME", "")

    async def fail_ask(*a, **kw):
        raise AssertionError("CEO bo'lmagan bosuvchi uchun so'rov bajarilmasligi kerak")

    monkeypatch.setattr(delivery, "ask_with_files", fail_ask)
    markup = delivery.offer_formats("grp-main", "xodimlar ro'yxati", AssistantResult(text="Jami 187 ta."),
                                    chat_id=-100123, thread_id=None, user_key=-100123)
    key = _keys(markup)[0]

    alerts = []

    async def answer(*a, **kw):
        alerts.append((a, kw))

    stranger = types.SimpleNamespace(
        data=f"fmt:excel:{key}", answer=answer, message=None,
        from_user=types.SimpleNamespace(id=5, username="someone"),
    )
    await delivery.on_format_choice(stranger, _FakeBot())

    assert alerts and alerts[0][1].get("show_alert") is True
    assert key in delivery._entries  # tanlov CEO uchun saqlanib qoladi


def test_plain_chat_and_odoo_failure_get_no_buttons(tmp_path):
    kw = dict(chat_id=1, thread_id=None, user_key=1)
    assert delivery.offer_formats("dm-1", "salom", AssistantResult(text="Salom."), **kw) is None
    failed = AssistantResult(text="Odoo serveri bilan aloqa yo'q", odoo_error="dns")
    assert delivery.offer_formats("dm-1", "xodimlar ro'yxati", failed, **kw) is None
    assert delivery._entries == {}


async def test_send_result_puts_buttons_under_the_file(tmp_path):
    f = _file(tmp_path)
    result = AssistantResult(text=f"Hisobot tayyorlandi: {f.url}", files=[f])
    bot = _FakeBot()
    await delivery.send_result(bot, 1, None, result, reply_markup=delivery.text_keyboard("dm-1"))

    assert bot.sent == []  # havola olib tashlangach matn bo'sh — alohida xabar yuborilmaydi
    assert len(bot.docs) == 1
    assert _callbacks(bot.docs[0]["reply_markup"]) == ["fmt:text:dm-1"]


async def test_send_result_sends_text_export_as_is():
    """export_text matni Markdown o'girishidan o'tmaydi — "*" va "_" li nomlar buzilmaydi."""
    block = "<b>Mahsulotlar</b> — 1 ta yozuv\n\n1. <b>Paket 10*20 *maxsus* a_b_c</b>"
    result = AssistantResult(text="Jami 1 ta mahsulot.", texts=[block])
    bot = _FakeBot()
    await delivery.send_result(bot, 1, None, result)

    assert [m["text"] for m in bot.sent] == ["Jami 1 ta mahsulot.", block]
    assert bot.sent[1]["parse_mode"] == "HTML"


async def test_long_text_export_is_split_into_several_messages():
    block = "\n".join(f"{n}. <b>Xodim {n}</b> — Lavozim: Sotuv menejeri" for n in range(1, 301))
    bot = _FakeBot()
    await delivery.send_result(bot, 1, None, AssistantResult(text="Jami 300 ta.", texts=[block]))

    parts = [m["text"] for m in bot.sent[1:]]
    assert len(parts) > 1 and all(len(p) <= delivery.TG_LIMIT for p in parts)
    assert "\n".join(parts) == block  # hech bir qator yo'qolmagan


def _callback(data: str):
    answered = []

    async def answer(*a, **kw):
        answered.append(True)

    async def edit_reply_markup(**kw):
        return None

    async def msg_answer(text, **kw):
        answered.append(text)

    message = types.SimpleNamespace(edit_reply_markup=edit_reply_markup, answer=msg_answer)
    return types.SimpleNamespace(data=data, answer=answer, message=message), answered


async def test_text_button_reasks_without_file_tools(monkeypatch):
    seen = {}

    async def fake_ask_with_files(user_key, question, **kw):
        seen.update(kw, question=question)
        return AssistantResult(text="Jami 3 ta xodim.", texts=["1. <b>Aziz</b>"])

    monkeypatch.setattr(delivery, "ask_with_files", fake_ask_with_files)
    delivery.remember("dm-1", chat_id=1, thread_id=None, question="xodimlar ro'yxati", user_key=1)
    bot = _FakeBot()
    callback, _ = _callback("fmt:text:dm-1")
    await delivery.on_format_choice(callback, bot)

    assert seen["question"] == "xodimlar ro'yxati"
    assert seen["exclude_tools"] == delivery.FILE_TOOLS
    assert seen["tool_choice"] is None and seen["force_tools"] is None
    assert "export_text" in seen["instruction"]
    # "Tayyorlanmoqda" o'chirilgan; fayl kutilmagani uchun "Fayl yaratishning iloji bo'lmadi" yo'q
    assert [m["text"] for m in bot.sent] == ["Jami 3 ta xodim.", "1. <b>Aziz</b>"]


async def test_excel_button_then_offers_text_under_the_file(monkeypatch, tmp_path):
    f = _file(tmp_path, "xodimlar_abc12345.xlsx")

    async def fake_ask_with_files(user_key, question, **kw):
        assert kw["tool_choice"] == {"type": "tool", "name": "export_excel"}
        return AssistantResult(text=f"Hisobot tayyorlandi: {f.url}", files=[f])

    monkeypatch.setattr(delivery, "ask_with_files", fake_ask_with_files)
    delivery.remember("dm-1", chat_id=1, thread_id=None, question="xodimlar ro'yxati", user_key=1)
    bot = _FakeBot()
    callback, _ = _callback("fmt:excel:dm-1")
    await delivery.on_format_choice(callback, bot)

    assert _callbacks(bot.docs[0]["reply_markup"]) == ["fmt:text:dm-1"]
    assert "dm-1" in delivery._entries  # "Matn" tugmasi bosilganda savol qayta ishlatiladi


async def test_format_button_with_unreachable_odoo_shows_reason_once(monkeypatch):
    async def fake_ask_with_files(user_key, question, **kw):
        return AssistantResult(text="⚠️ Odoo serveri bilan aloqa yo'q.\n\nSabab: DNS xatosi.",
                               odoo_error="dns", file_error="export_excel: DNS xatosi")

    monkeypatch.setattr(delivery, "ask_with_files", fake_ask_with_files)
    delivery.remember("dm-1", chat_id=1, thread_id=None, question="xodimlar ro'yxati", user_key=1)
    bot = _FakeBot()
    callback, _ = _callback("fmt:excel:dm-1")
    await delivery.on_format_choice(callback, bot)

    texts = [m["text"] for m in bot.sent]
    assert len(texts) == 1 and "Sabab: DNS xatosi" in texts[0]


async def test_send_result_keeps_the_rest_of_the_answer_when_link_is_removed(tmp_path):
    """Havola olib tashlangach "Hisobot tayyorlandi:" yarim jumla bo'lib osilib qolmasligi kerak."""
    f = _file(tmp_path)
    result = AssistantResult(text=f"Hisobot tayyorlandi: {f.url}\n\nJami 188 ta xodim.", files=[f])
    bot = _FakeBot()
    await delivery.send_result(bot, 1, None, result)

    assert [m["text"] for m in bot.sent] == ["Hisobot tayyorlandi.\n\nJami 188 ta xodim."]
    assert len(bot.docs) == 1