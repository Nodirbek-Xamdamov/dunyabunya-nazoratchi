"""Yordamchining agent sikli (app/services/assistant.py): qadamlar chegarasi, internet qidiruvi,
fikrlash rejimi, xato natijalar, kontekst va tool natijalarini modelga tayyorlash.

Barcha testlar mok'langan model va Odoo bilan ishlaydi — tarmoqqa chiqilmaydi.
"""
from __future__ import annotations

import json
import types

import pytest

import app.services.assistant as assistant
from app.rbac import Role
from app.services import agent_context, agent_memory


def text(value):
    return types.SimpleNamespace(type="text", text=value)


def tool(name, args=None, tid="t1"):
    return types.SimpleNamespace(type="tool_use", name=name, input=args or {}, id=tid)


def reply(*blocks, stop="end_turn"):
    return types.SimpleNamespace(stop_reason=stop, content=list(blocks))


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def agent(monkeypatch):
    """Model va bazasiz yordamchi: `agent.model(...)` — navbatdagi javoblar, `agent.calls` — so'rovlar,
    `agent.saved` — suhbat tarixiga yozilganlar, `agent.tools[name] = natija` — tool natijalari."""
    state = types.SimpleNamespace(calls=[], saved=[], history=[], tools={}, executed=[], responses=[])

    async def fake_recent(session, uid):
        return state.history

    async def fake_add(session, uid, role, content):
        state.saved.append((role, content))

    async def fake_call(**kwargs):
        # Xabarlar ro'yxati keyin o'zgaradi — so'rov paytidagi holati saqlanadi
        state.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        index = len(state.calls) - 1 - state.base
        item = state.responses[min(index, len(state.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    async def fake_exec(name, args, role=None):
        state.executed.append((name, args))
        result = state.tools.get(name, {"ok": True})
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

    def model(*responses):
        state.responses = list(responses)
        state.base = len(state.calls)  # javoblar navbati shu paytdan boshlab sanaladi

    state.base = 0
    state.model = model
    monkeypatch.setattr(assistant.repo, "get_recent_conversation", fake_recent)
    monkeypatch.setattr(assistant.repo, "add_conversation_message", fake_add)
    monkeypatch.setattr(assistant, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(assistant, "_call_model", fake_call)
    monkeypatch.setattr(assistant, "_exec_tool", fake_exec)
    return state


# ── Javob matni ─────────────────────────────────────────────────────────────

async def test_text_written_before_a_tool_call_is_not_the_answer(agent):
    """Ilgari qadamlar tugab qolsa, modelning oraliq izohi ("endi USD'ni tekshiraman...") javob
    bo'lib ketardi."""
    agent.model(reply(text("Avval sotuvni ko'raman."), tool("odoo_count"), stop="tool_use"),
                reply(text("Jami 12 ta buyurtma.")))
    result = await assistant.ask_with_files(1, "nechta buyurtma?")
    assert result.text == "Jami 12 ta buyurtma."
    assert agent.saved == [("user", "nechta buyurtma?"), ("assistant", "Jami 12 ta buyurtma.")]


async def test_last_step_forbids_tools_and_tells_the_model_to_wrap_up(agent, monkeypatch):
    monkeypatch.setattr(assistant.settings, "AGENT_MAX_STEPS", 3)
    agent.model(reply(tool("odoo_count"), stop="tool_use"), reply(tool("odoo_count"), stop="tool_use"),
                reply(text("Bor ma'lumotga ko'ra: 5 ta.")))
    result = await assistant.ask_with_files(1, "murakkab savol")

    assert result.text == "Bor ma'lumotga ko'ra: 5 ta." and len(agent.calls) == 3
    assert "tool_choice" not in agent.calls[0] and "tool_choice" not in agent.calls[1]
    assert agent.calls[2]["tool_choice"] == {"type": "none"}
    notice = agent.calls[2]["messages"][-1]["content"][-1]
    assert notice["type"] == "text" and "yakuniy javob" in notice["text"]


async def test_running_out_of_steps_without_an_answer_gives_a_clear_message(agent, monkeypatch):
    monkeypatch.setattr(assistant.settings, "AGENT_MAX_STEPS", 3)
    agent.model(reply(tool("odoo_count"), stop="tool_use"))
    result = await assistant.ask_with_files(1, "savol")
    assert "javob tayyorlab bo'lmadi" in result.text and len(agent.calls) == 3


async def test_mixed_script_word_endings_are_repaired():
    fixed = assistant._fix_mixed_script("Oktabr (1–6, hozirgача) nazarda tutilади. Доллар США va Стенка o'zgarmaydi.")
    assert fixed == "Oktabr (1–6, hozirgacha) nazarda tutiladi. Доллар США va Стенка o'zgarmaydi."


# ── Tool natijalari ─────────────────────────────────────────────────────────

async def test_error_results_are_flagged_and_created_tasks_are_reported(agent):
    agent.tools = {"odoo_count": {"error": "Bunday maydon yo'q"},
                   "create_task": {"yaratildi": True, "task_id": 175, "nomi": "Tekshirish"}}
    agent.model(reply(tool("odoo_count", tid="a"), tool("create_task", {"title": "Tekshirish"}, tid="b"), stop="tool_use"),
                reply(text("Vazifa yaratildi: #175")))
    result = await assistant.ask_with_files(1, "vazifa ber")

    sent = {r["tool_use_id"]: r for r in agent.calls[1]["messages"][-1]["content"]}
    assert sent["a"]["is_error"] is True and "is_error" not in sent["b"]
    assert result.task_ids == [175]


def test_large_floats_carry_a_ready_compact_form_so_the_model_cannot_misread_them():
    """Sinovda model 22043045.11 ni ikki marta "22 ming" deb o'qigan."""
    raw = assistant._serialize_tool_result({
        "jami": 22043045.11, "sotuv": 26674071239.71, "qoldiq": 829500.0, "manfiy": -193900000.0,
        "id": 97551, "soni": 13313, "kurs": 8.442380751371887e-05, "narx": 5.6013329999999995})
    assert json.loads(raw) == {
        "jami": "22 043 045.11 (22,04 mln)", "sotuv": "26 674 071 239.71 (26,67 mlrd)",
        "qoldiq": "829 500 (829,5 ming)", "manfiy": "-193 900 000 (-193,90 mln)",
        "id": 97551, "soni": 13313, "kurs": 8.44238e-05, "narx": 5.6}


def test_oversized_grouped_result_is_cut_by_whole_rows(monkeypatch):
    monkeypatch.setattr(assistant, "_TOOL_RESULT_MAX_CHARS", 2000)
    rows = [{"partner_id": [i, f"Mijoz {i:04d}"], "soni": i} for i in range(200)]
    raw = assistant._serialize_tool_result({"guruhlar": rows, "jami_guruhlar": 200, "jami": {"soni": 19900}})
    result = json.loads(raw)

    assert len(raw) <= 2000 and result["guruhlar"] == rows[:result["korsatilgan"]] and 0 < result["korsatilgan"] < 200
    assert result["jami"] == {"soni": 19900} and "odoo_read_group" in result["ogohlantirish"]


async def test_export_buttons_are_offered_only_when_company_data_was_read(agent):
    """ "Xodimlarga xat yoz, ro'yxat bilan" — matn ishi: ostiga Excel/HTML tugmalari qo'yilmaydi."""
    from app.telegram import delivery

    def offered(question, result):
        return delivery.offer_formats("t", question, result, chat_id=1, thread_id=None, user_key=1)

    agent.model(reply(text("Hurmatli hamkasblar! ...")))
    letter = await assistant.ask_with_files(1, "xodimlarga xat yoz, vazifalar ro'yxati bilan")
    assert letter.used_data is False and offered("xodimlarga xat yoz, vazifalar ro'yxati bilan", letter) is None

    agent.model(reply(tool("set_reminder"), stop="tool_use"), reply(text("Eslatma qo'yildi.")))
    reminder = await assistant.ask_with_files(1, "ro'yxatni ertaga eslat")
    assert reminder.used_data is False and offered("ro'yxatni ertaga eslat", reminder) is None

    agent.model(reply(tool("odoo_search_read"), stop="tool_use"), reply(text("6 ta xodim: ...")))
    data = await assistant.ask_with_files(1, "Sardor ismli xodimlar ro'yxati")
    assert data.used_data is True and offered("Sardor ismli xodimlar ro'yxati", data) is not None

    # Model o'zi format so'ragan bo'lsa — tugmalar har doim chiqadi
    asked = assistant.AssistantResult(text="Jami 67 ta yozuv topildi. Qaysi formatda tayyorlab beray?", used_data=False)
    assert offered("avtomobillar", asked) is not None


# ── _exec_tool: parametr va model nazorati ──────────────────────────────────

async def test_wrong_parameter_names_come_back_as_a_fixable_error(monkeypatch):
    async def fake_tool(partner_id: int, limit: int = 5):
        return {"partner_id": partner_id}

    monkeypatch.setitem(assistant.ALL_TOOLS, "check_partner_debt", fake_tool)
    raw = await assistant._exec_tool("check_partner_debt", {"partner": 5}, Role.CEO)
    assert "partner_id, limit" in json.loads(raw)["error"]


async def test_export_tools_cannot_read_secret_models_and_get_local_dates(monkeypatch):
    seen = {}

    async def fake_export(model="", domain=None, fields=None, title="Hisobot", limit=10000):
        seen["domain"] = domain
        return "http://x/reports/f.xlsx"

    async def fake_meta(model):
        return {"date_order": {"type": "datetime"}}

    monkeypatch.setitem(assistant.ALL_TOOLS, "export_excel", fake_export)
    monkeypatch.setattr(assistant.agent_tools.ot, "_fields_meta", fake_meta)

    blocked = await assistant._exec_tool("export_excel", {"model": "ir.config_parameter"}, Role.CEO)
    assert "error" in json.loads(blocked) and seen == {}

    await assistant._exec_tool("export_excel", {"model": "sale.order",
                                                "domain": [["date_order", ">=", "2026-10-01"]]}, Role.CEO)
    assert seen["domain"] == [["date_order", ">=", "2026-09-30 19:00:00"]]


# ── Model sozlamalari: fikrlash, internet, majburlash ───────────────────────

async def test_ceo_gets_thinking_and_web_search_managers_do_not_get_web(agent):
    agent.model(reply(text("Salom.")))
    await assistant.ask_with_files(1, "salom", role=Role.CEO)
    await assistant.ask_with_files(2, "salom", role=Role.HR)
    ceo, hr = agent.calls

    assert ceo["thinking"] == {"type": "adaptive"} and ceo["output_config"] == {"effort": "medium"}
    assert {t.get("type") for t in ceo["tools"]} >= {"web_search_20260209", "web_fetch_20260209"}
    assert not any(str(t.get("type", "")).startswith("web_") for t in hr["tools"])
    names = {t["name"] for t in hr["tools"]}
    assert {"odoo_search_read", "odoo_compare", "set_reminder"} <= names
    assert not names & {"odoo_models", "create_task", "get_group_history", "web_search"}


async def test_forced_file_export_runs_without_thinking_or_web(agent):
    agent.tools = {"export_excel": "http://x/reports/f.xlsx"}
    agent.model(reply(tool("export_excel", {"model": "hr.employee"}), stop="tool_use"), reply(text("Hisobot tayyorlandi")))
    await assistant.ask_with_files(1, "excel qil", force_tools=["export_excel"],
                                   tool_choice={"type": "tool", "name": "export_excel"})
    first, second = agent.calls
    assert first["thinking"] == {"type": "disabled"} and "output_config" not in first
    assert [t["name"] for t in first["tools"]] == ["export_excel"] and "tool_choice" not in second


class _BadRequest(Exception):
    pass


async def test_unsupported_thinking_or_web_search_is_switched_off_and_the_step_repeated(agent, monkeypatch):
    monkeypatch.setattr(assistant.anthropic, "BadRequestError", _BadRequest)
    agent.model(_BadRequest("thinking.type: adaptive is not supported for this model"),
                _BadRequest("tools.61: web_search_20260209 is not enabled for this organization"),
                reply(text("Salom.")))
    result = await assistant.ask_with_files(1, "salom")

    assert result.text == "Salom." and assistant._features == {"thinking": False, "web": False}
    final = agent.calls[2]
    assert "thinking" not in final and not any(str(t.get("type", "")).startswith("web_") for t in final["tools"])


async def test_other_bad_requests_are_not_swallowed(agent, monkeypatch):
    monkeypatch.setattr(assistant.anthropic, "BadRequestError", _BadRequest)
    agent.model(_BadRequest("messages.0: invalid role"))
    with pytest.raises(_BadRequest):
        await assistant.ask_with_files(1, "salom")


async def test_paused_web_search_turn_is_resumed_without_a_new_user_message(agent):
    paused = reply(types.SimpleNamespace(type="server_tool_use", name="web_search", id="s1"), stop="pause_turn")
    agent.model(paused, reply(text("Kurs 11 778 so'm.")))
    result = await assistant.ask_with_files(1, "dollar kursi?")

    assert result.text == "Kurs 11 778 so'm."
    assert [m["role"] for m in agent.calls[1]["messages"]] == ["user", "assistant"]


async def test_model_refusal_becomes_a_polite_answer(agent):
    agent.model(reply(stop="refusal"))
    assert "javob bera olmayman" in (await assistant.ask_with_files(1, "savol")).text


# ── Kontekst: kim, qachon, xotira, oldingi ish ──────────────────────────────

async def test_context_block_carries_time_person_notes_and_previous_queries(agent):
    agent_memory.remember("ceo", "Summalarni dollarda ham ko'rsat")
    agent.model(reply(tool("odoo_count", {"model": "res.partner", "domain": [["city", "ilike", "Toshkent"]]}),
                      stop="tool_use"), reply(text("1973 ta.")), reply(text("Hisobot tayyorlandi")))
    await assistant.ask_with_files(7, "Toshkentdagi mijozlar nechta?", user_name="Sardor Hasanxo'jayev")
    await assistant.ask_with_files(7, "shularni excelda ber", user_name="Sardor Hasanxo'jayev")

    stable, first_context = agent.calls[0]["system"]
    assert "Hozir: " in first_context["text"] and "Sardor Hasanxo'jayev" in first_context["text"]
    assert "(#1) Summalarni dollarda ham ko'rsat" in first_context["text"]
    assert "odoo_count" not in first_context["text"]  # birinchi savolda oldingi so'rovlar yo'q
    follow_up = agent.calls[2]["system"][1]["text"]
    # Davom savolida model oldingi filtrni ko'radi — "shularni" nimani anglatishini taxmin qilmaydi
    assert 'odoo_count {"model": "res.partner", "domain": [["city", "ilike", "Toshkent"]]}' in follow_up
    assert stable == agent.calls[2]["system"][0]  # keshlanadigan blok o'zgarmagan


async def test_tools_know_who_is_asking_and_where(agent, monkeypatch):
    seen = {}

    async def spy(name, args, role=None):
        seen["actor"] = agent_context.current.get()
        return "{}"

    monkeypatch.setattr(assistant, "_exec_tool", spy)
    agent.model(reply(tool("set_reminder"), stop="tool_use"), reply(text("Qo'yildi.")))
    await assistant.ask_with_files(-100500, "eslat", user_name="Ali", actor_id=42, chat_id=-100500, thread_id=9)

    who = seen["actor"]
    assert (who.user_key, who.telegram_user_id, who.chat_id, who.thread_id, who.name) == (-100500, 42, -100500, 9, "Ali")
    assert who.memory_owner == "ceo"


async def test_scheduled_question_cannot_schedule_more_and_must_not_ask_back(agent):
    agent.model(reply(text("Bugungi sotuv: 0.")))
    await assistant.ask_with_files(1, "bugungi sotuv?", scheduled=True)
    call = agent.calls[0]
    assert "set_reminder" not in {t.get("name") for t in call["tools"]}
    assert "rejalashtirilgan so'rov" in call["system"][1]["text"]


async def test_attachment_stays_visible_for_the_follow_up_question(agent):
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}}
    agent.model(reply(text("Bu — hisob-faktura.")), reply(text("Summasi 5 mln so'm.")))

    await assistant.ask_with_files(3, "bu nima?", attachments=[image])
    assert agent.calls[0]["messages"][-1]["content"][0] == image
    assert agent.saved[0] == ("user", "[ilova: 1 ta fayl/rasm] bu nima?")

    agent.history = [types.SimpleNamespace(role=r, content=c) for r, c in agent.saved]
    await assistant.ask_with_files(3, "summasi qancha?")
    first_user = agent.calls[1]["messages"][0]["content"]
    assert first_user[0] == image and "bu nima?" in first_user[1]["text"]  # rasm tarixga qayta biriktirildi
    assert isinstance(agent.calls[1]["messages"][-1]["content"], str)


# ── Ro'yxat uzunligi (format tugmalari uchun) ───────────────────────────────

async def test_list_length_is_taken_from_the_export_result(agent):
    url = assistant.settings.PUBLIC_BASE_URL.rstrip("/") + "/reports/xodimlar_abc.xlsx"
    agent.tools = {"export_excel": {"url": url, "rows_in_file": 132, "rows_total": 132}}
    agent.model(reply(tool("export_excel"), stop="tool_use"), reply(text(f"Hisobot tayyorlandi: {url}")))
    assert (await assistant.ask_with_files(1, "xodimlar ro'yxatini excelda ber")).list_rows == 132

    agent.tools = {"export_text": {"text_export": "yo'q", "rows_shown": 300, "rows_total": 84207}}
    agent.model(reply(tool("export_text"), stop="tool_use"), reply(text("Jami 84 207 ta.")))
    assert (await assistant.ask_with_files(1, "mahsulotlarni shu yerga yoz")).list_rows == 84207

    agent.model(reply(tool("odoo_count"), stop="tool_use"), reply(text("Jami 12 ta buyurtma.")))
    assert (await assistant.ask_with_files(1, "nechta buyurtma?")).list_rows is None


async def test_long_list_is_never_offered_as_chat_text(agent):
    """30 tadan uzun ro'yxat uchun "shu chatda beraymi" so'ralmaydi — model taklif qilib yuborsa ham."""
    agent.model(reply(tool("odoo_count"), stop="tool_use"),
                reply(text("132 ta xodim topildi. Qaysi formatda tayyorlab beray: Excel, HTML yoki matn (shu chatda)?")))
    result = await assistant.ask_with_files(1, "xodimlar ro'yxatini ber")
    assert result.text == "132 ta xodim topildi. Excel yoki HTML qilib beraymi?"
    assert agent.saved[-1] == ("assistant", result.text)  # suhbat tarixida ham shu ko'rinishda

    for untouched in ("Jami 18 ta yozuv topildi. Qaysi formatda tayyorlab beray: Excel, HTML yoki matn (shu chatda)?",
                      "Jami 132 ta yozuv topildi. Excel yoki HTML qilib beraymi?",
                      "Excel, HTML yoki matn — qaysi biri qulay?"):
        agent.model(reply(text(untouched)))
        assert (await assistant.ask_with_files(1, "ro'yxat")).text == untouched


def test_list_length_mentioned_in_an_answer():
    assert assistant.rows_in_answer("Jami 1 187 ta yozuv topildi.") == 1187
    assert assistant.rows_in_answer("38 ta xodim: Savdo bo'limi 9 ta, Shiribom 16 ta") == 38
    assert assistant.rows_in_answer("2026-10-06 holatiga 30 tadan ko'p emas") is None
    assert assistant.rows_in_answer("Telefon: +998 97 700 65 90") is None


def test_company_name_stays_lowercase_and_technical_notes_are_dropped():
    tidy = assistant._tidy_answer
    assert tidy("Dunyabunya® (company_id=1) kompaniyasida 35 kishi.") == "dunyabunya® kompaniyasida 35 kishi."
    assert tidy("dunyabunya logistika (company_id = 2) — 56 kishi") == "dunyabunya logistika — 56 kishi"
    assert tidy("Savdo bo'limi (9 kishi), dunyabunya®") == "Savdo bo'limi (9 kishi), dunyabunya®"
