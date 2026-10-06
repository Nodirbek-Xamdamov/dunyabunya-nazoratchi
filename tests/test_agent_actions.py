"""Yordamchining amallari: vazifa berish/yangilash, eslatmalar, uzoq muddatli xotira va
xabarga ilova qilingan rasm/hujjatlarni o'qish.

Barcha testlar mok'langan Odoo/Telegram bilan ishlaydi — tarmoqqa chiqilmaydi.
"""
from __future__ import annotations

import io
import types
from datetime import datetime

import pytest

from app import runtime
from app.services import agent_context, agent_memory, reminders, task_tools
from app.services.agent_tools import ToolInputError
from app.telegram import attachments


def as_actor(**kwargs):
    defaults = dict(user_key=42, role="ceo", telegram_user_id=42, name="Sardor", chat_id=42, thread_id=None)
    agent_context.current.set(agent_context.Actor(**{**defaults, **kwargs}))


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


# ═══ Vazifalar ═══════════════════════════════════════════════════════════════

TOPICS = [types.SimpleNamespace(name=n, odoo_project_id=i, telegram_topic_id=100 + i)
          for i, n in enumerate(["Sotuv", "Moliya", "Ombor", "HR"], start=33)]


@pytest.fixture
def tasks(monkeypatch):
    """Odoo va bazasiz vazifa tool'lari. `tasks.users` / `tasks.employees` — ism bo'yicha topiladiganlar."""
    state = types.SimpleNamespace(users=[], employees=[], created=[], written=[], stages=[], logged=[],
                                  notified=[], posted=[])

    async def list_topics(session):
        return TOPICS

    async def save_task_log(session, **kwargs):
        state.logged.append(kwargs)

    async def find_users(name):
        return state.users

    async def find_employees(name):
        return state.employees

    async def create_task(**kwargs):
        state.created.append(kwargs)
        return 501

    async def update_stage(task_id, *, done):
        state.stages.append((task_id, done))

    async def ensure_stage(project_id):
        return 77

    async def execute_kw(model, method, args, kwargs=None):
        if model == "res.users" and method == "read":
            return [{"id": args[0][0], "name": "KADIROV SARDOR"}] if args[0][0] == 93 else []
        if model == "project.task" and method == "read":
            return [{"id": args[0][0], "name": "Eski nom", "project_id": [35, "Ombor"]}] if args[0][0] == 501 else []
        if method == "write":
            state.written.append((args[0], args[1]))
            return True
        if method == "message_post":
            state.posted.append((args[0], kwargs))
            return 9
        raise AssertionError(f"kutilmagan chaqiruv: {model}.{method}")

    async def no_local_employees(name):
        return []

    async def notifier(**kwargs):
        state.notified.append(kwargs)
        return "yuborildi"

    monkeypatch.setattr(task_tools, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(task_tools.repo, "list_topics", list_topics)
    monkeypatch.setattr(task_tools.repo, "save_task_log", save_task_log)
    monkeypatch.setattr(task_tools.employee_store, "get_employees_by_name", no_local_employees)
    for name, func in [("find_users_by_name", find_users), ("find_employees_by_name", find_employees),
                       ("create_task", create_task), ("update_task_stage", update_stage),
                       ("_ensure_working_stage", ensure_stage), ("execute_kw", execute_kw)]:
        monkeypatch.setattr(task_tools._odoo, name, func)
    monkeypatch.setattr(runtime, "task_notifier", notifier)
    as_actor()
    return state


async def test_task_is_created_assigned_logged_and_the_assignee_notified(tasks):
    tasks.users = [{"id": 93, "name": "KADIROV SARDOR", "score": 96.0}]
    result = await task_tools.create_task("  Ombor qoldig'ini   tekshirish ", "ombor", assignee="Kadirov Sardor",
                                          deadline="2026-10-07 10:00")

    assert tasks.created == [dict(project_id=35, name="Ombor qoldig'ini tekshirish", description="",
                                  user_ids=[93], date_deadline="2026-10-07 05:00:00")]  # Toshkent 10:00 = UTC 05:00
    assert result["yaratildi"] and result["task_id"] == 501 and result["bolim"] == "Ombor"
    assert result["masul"] == "KADIROV SARDOR" and result["xodimga_xabar"] == "yuborildi"
    assert tasks.notified[0]["odoo_user_id"] == 93 and tasks.notified[0]["topic_name"] == "Ombor"
    # Jurnal: "bajarildi" xabari vazifa so'ralgan chatga qaytishi uchun chat va so'ragan odam yoziladi
    assert tasks.logged[0]["telegram_chat_id"] == 42 and tasks.logged[0]["created_by_telegram_user_id"] == 42
    assert tasks.logged[0]["odoo_task_id"] == 501


async def test_ambiguous_assignee_creates_nothing_and_asks_which_one(tasks):
    tasks.users = [{"id": 2, "name": "HASANXO'JAYEV SARDOR", "score": 90.0},
                   {"id": 93, "name": "KADIROV SARDOR", "score": 88.0}]
    result = await task_tools.create_task("Tekshirish", "Ombor", assignee="Sardor")

    assert result["aniqlashtirish_kerak"] is True and tasks.created == []
    assert result["nomzodlar"] == [{"ism": "HASANXO'JAYEV SARDOR", "assignee_user_id": 2},
                                   {"ism": "KADIROV SARDOR", "assignee_user_id": 93}]

    chosen = await task_tools.create_task("Tekshirish", "Ombor", assignee="Sardor", assignee_user_id=93)
    assert chosen["yaratildi"] and tasks.created[0]["user_ids"] == [93]


async def test_clear_best_match_is_taken_without_asking(tasks):
    tasks.users = [{"id": 93, "name": "KADIROV SARDOR", "score": 97.0}, {"id": 2, "name": "SARDORBEK", "score": 80.0}]
    assert (await task_tools.create_task("Tekshirish", "Ombor", assignee="Kadirov Sardor"))["masul"] == "KADIROV SARDOR"


async def test_employee_without_odoo_login_gets_the_task_unassigned_but_is_still_notified(tasks):
    tasks.employees = [{"id": 809, "name": "BADALOV SARDOR", "score": 95.0}]
    result = await task_tools.create_task("Yuk tashish", "Ombor", assignee="Badalov")

    assert tasks.created[0]["user_ids"] is None and "login hisobi yo'q" in result["masul_izoh"]
    assert tasks.notified[0]["odoo_user_id"] is None and tasks.notified[0]["assignee_name"] == "BADALOV SARDOR"


async def test_unknown_person_or_department_stops_before_writing_to_odoo(tasks):
    missing = await task_tools.create_task("Tekshirish", "Ombor", assignee="Falonchi")
    assert missing["aniqlashtirish_kerak"] and "topilmadi" in missing["xabar"]

    wrong = await task_tools.create_task("Tekshirish", "Kosmonavtika")
    assert wrong["mavjud_bolimlar"] == ["Sotuv", "Moliya", "Ombor", "HR"]

    with pytest.raises(ToolInputError):
        await task_tools.create_task("Tekshirish", "Ombor", deadline="ertaga")
    with pytest.raises(ToolInputError):
        await task_tools.create_task("   ", "Ombor")
    assert tasks.created == []


async def test_task_without_assignee_is_created_as_is(tasks):
    result = await task_tools.create_task("Hisobot tayyorlash", "Moliya", deadline="2026-10-10")
    assert tasks.created[0]["user_ids"] is None and tasks.created[0]["date_deadline"] == "2026-10-10"
    assert "masul" not in result and tasks.notified == []


async def test_update_task_changes_fields_and_status(tasks):
    result = await task_tools.update_task(501, deadline="2026-10-08 10:00", status="done", title="Yangi nom")
    assert tasks.written == [([501], {"name": "Yangi nom", "date_deadline": "2026-10-08 05:00:00"})]
    assert tasks.stages == [(501, True)] and result["ozgardi"] == ["nomi", "muddati", "holati: bajarildi"]

    await task_tools.update_task(501, status="reopen")
    assert tasks.written[-1] == ([501], {"state": "01_in_progress", "stage_id": 77})

    assert "error" in await task_tools.update_task(501)
    with pytest.raises(ToolInputError):
        await task_tools.update_task(999, title="x")      # bunday vazifa yo'q
    with pytest.raises(ToolInputError):
        await task_tools.update_task(501, status="archive")


async def test_comment_goes_to_the_task_chatter(tasks):
    result = await task_tools.add_task_comment(501, "Bugun tekshirildi")
    assert tasks.posted == [([501], {"body": "Bugun tekshirildi"})] and result["izoh_qoshildi"]


# ═══ Eslatmalar ══════════════════════════════════════════════════════════════

@pytest.fixture
def clock(monkeypatch):
    now = {"value": datetime(2026, 10, 6, 14, 30)}
    monkeypatch.setattr(reminders, "_now", lambda: now["value"])
    return now


async def test_reminder_lifecycle_is_scoped_to_the_chat(clock):
    as_actor(chat_id=42)
    first = await reminders.set_reminder("2026-10-07 09:00", "  Bank bilan   uchrashuv ")
    daily = await reminders.set_reminder("2026-10-06T18:00", "Bugungi sotuv qancha?", repeat="daily", run_as_question=True)
    assert first == {"qoyildi": True, "id": 1, "vaqt": "2026-10-07 09:00", "matn": "Bank bilan uchrashuv",
                     "takrorlanish": "bir marta", "turi": "eslatma"}
    assert daily["takrorlanish"] == "har kuni" and daily["turi"].startswith("so'rov")
    assert [r["id"] for r in (await reminders.list_reminders())["eslatmalar"]] == [2, 1]  # vaqt tartibida

    as_actor(user_key=7, telegram_user_id=7, chat_id=7)  # boshqa odamning chati
    assert (await reminders.list_reminders())["jami"] == 0
    assert "error" in await reminders.cancel_reminder(1)

    as_actor(chat_id=42)
    assert (await reminders.cancel_reminder(1))["bekor_qilindi"] and (await reminders.list_reminders())["jami"] == 1


async def test_reminder_input_is_validated(clock):
    as_actor()
    for when, text, repeat in [("2026-10-06 14:00", "o'tgan vaqt", ""), ("ertaga 9 da", "format", ""),
                               ("2026-10-07 09:00", "   ", ""), ("2026-10-07 09:00", "matn", "hourly"),
                               ("2029-01-01 09:00", "juda uzoq", "")]:
        with pytest.raises(ToolInputError):
            await reminders.set_reminder(when, text, repeat)


def test_next_due_handles_weekends_and_month_ends():
    assert reminders.next_due(datetime(2026, 10, 9, 18, 0), "weekdays") == datetime(2026, 10, 12, 18, 0)  # juma -> dushanba
    assert reminders.next_due(datetime(2026, 1, 31, 9, 0), "monthly") == datetime(2026, 2, 28, 9, 0)
    assert reminders.next_due(datetime(2026, 12, 15, 9, 0), "monthly") == datetime(2027, 1, 15, 9, 0)
    assert reminders.next_due(datetime(2026, 10, 6, 9, 0), "weekly") == datetime(2026, 10, 13, 9, 0)
    assert reminders.next_due(datetime(2026, 10, 6, 9, 0), "") is None


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


async def test_due_reminders_are_sent_once_and_repeating_ones_move_forward(clock):
    as_actor(chat_id=-100500, thread_id=9)
    await reminders.set_reminder("2026-10-06 15:00", "Majlis")
    await reminders.set_reminder("2026-10-06 15:00", "Hisobotni tekshir", repeat="daily")
    await reminders.set_reminder("2026-10-08 09:00", "Keyinroq")
    bot = _Bot()

    assert await reminders.run_due(bot, datetime(2026, 10, 6, 14, 59)) == 0
    assert await reminders.run_due(bot, datetime(2026, 10, 6, 15, 0)) == 2
    assert [m["text"] for m in bot.sent] == ["⏰ Eslatma:\nMajlis", "⏰ Eslatma:\nHisobotni tekshir"]
    assert bot.sent[0]["chat_id"] == -100500 and bot.sent[0]["message_thread_id"] == 9
    assert await reminders.run_due(bot, datetime(2026, 10, 6, 15, 1)) == 0      # ikkinchi marta ketmaydi

    left = {i["text"]: i["due"] for i in reminders._read()}
    assert left == {"Hisobotni tekshir": "2026-10-07 15:00", "Keyinroq": "2026-10-08 09:00"}


async def test_reminders_missed_while_the_bot_was_down_arrive_late_and_skip_ahead(clock):
    as_actor()
    await reminders.set_reminder("2026-10-06 15:00", "Har kungi", repeat="daily")
    bot = _Bot()
    assert await reminders.run_due(bot, datetime(2026, 10, 9, 11, 0)) == 1       # bot 3 kun o'chiq turgan
    assert "belgilangan vaqt: 2026-10-06 15:00" in bot.sent[0]["text"]
    assert reminders._read()[0]["due"] == "2026-10-09 15:00"                     # o'tib ketgan kunlar takrorlanmaydi


async def test_scheduled_question_is_answered_by_the_assistant(clock, monkeypatch):
    as_actor(chat_id=42, role="ceo")
    await reminders.set_reminder("2026-10-06 18:00", "Bugungi sotuv qancha?", repeat="daily", run_as_question=True)
    seen = {}

    async def fake_ask(user_key, question, **kwargs):
        seen.update(question=question, **kwargs)
        return types.SimpleNamespace(text="Bugungi sotuv: 120 mln so'm.")

    async def fake_send_result(bot, chat_id, thread_id, result, reply_markup=None):
        seen["delivered"] = (chat_id, result.text)

    import app.services.assistant as assistant
    from app.telegram import delivery
    monkeypatch.setattr(assistant, "ask_with_files", fake_ask)
    monkeypatch.setattr(delivery, "send_result", fake_send_result)

    bot = _Bot()
    assert await reminders.run_due(bot, datetime(2026, 10, 6, 18, 0)) == 1
    assert "Rejalashtirilgan so'rov" in bot.sent[0]["text"]
    assert seen["question"] == "Bugungi sotuv qancha?" and seen["scheduled"] is True and seen["role"].value == "ceo"
    assert seen["delivered"] == (42, "Bugungi sotuv: 120 mln so'm.")


# ═══ Xotira ══════════════════════════════════════════════════════════════════

def test_notes_are_kept_per_owner_deduplicated_and_removable():
    first = agent_memory.remember("ceo", "  Summalarni   dollarda ham ko'rsat ")
    again = agent_memory.remember("ceo", "summalarni dollarda ham ko'rsat")
    agent_memory.remember("ceo", "Asosiy mijoz — DAVRON 9896")
    agent_memory.remember("user-7", "Boshqa odamning qaydi")

    assert first == {"saqlandi": True, "id": 1} and again["id"] == 1 and "avvaldan" in again["izoh"]
    assert agent_memory.render("ceo") == "- (#1) Summalarni dollarda ham ko'rsat\n- (#2) Asosiy mijoz — DAVRON 9896"
    assert agent_memory.forget("ceo", 1) == {"ochirildi": True, "id": 1}
    assert "error" in agent_memory.forget("ceo", 1) and "error" in agent_memory.remember("ceo", "  ")
    assert agent_memory.render("ceo") == "- (#2) Asosiy mijoz — DAVRON 9896"
    assert agent_memory.render("user-7") == "- (#1) Boshqa odamning qaydi"


def test_memory_has_a_size_limit(monkeypatch):
    monkeypatch.setattr(agent_memory, "MAX_NOTES", 2)
    agent_memory.remember("ceo", "bir")
    agent_memory.remember("ceo", "ikki")
    assert "to'la" in agent_memory.remember("ceo", "uch")["error"]
    assert len(agent_memory.remember("ceo", "x" * 1000)["error"]) > 0  # hali ham to'la


async def test_group_history_combines_fresh_messages_and_daily_digests(monkeypatch, tmp_path):
    from datetime import timedelta, timezone

    today = datetime.now(timezone.utc)
    recent, old = (today - timedelta(days=2)).date().isoformat(), (today - timedelta(days=40)).date().isoformat()
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "CLAUDE.md").write_text(
        f"## {old}\n\nEski gap.\n\n---\n## {recent}\n\nOmbor inventarizatsiyasi topshirildi.\n\n---\n", encoding="utf-8")

    async def fresh(session, chat_id):
        return [types.SimpleNamespace(created_at=today, from_user_name="Sardor", text="Ertaga majlis soat 10 da"),
                types.SimpleNamespace(created_at=today, from_user_name="Sardor", text="Sement narxini tekshiring")]

    monkeypatch.setattr(agent_memory, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(agent_memory.repo, "get_all_group_messages", fresh)

    everything = await agent_memory.group_history(days=7)
    assert len(everything["hali_xulosalanmagan_xabarlar"]) == 2
    assert everything["kunlik_xulosalar"] == [{"sana": recent, "xulosa": "Ombor inventarizatsiyasi topshirildi."}]

    filtered = await agent_memory.group_history(days=90, query="sement")
    assert len(filtered["hali_xulosalanmagan_xabarlar"]) == 1 and filtered["kunlik_xulosalar"] == []


# ═══ Ilovalar (rasm, PDF, Excel, Word, matn) ═════════════════════════════════

class _TgBot:
    def __init__(self, files):
        self.files = files

    async def download(self, file, destination):
        data = self.files[file.file_id]
        if isinstance(data, Exception):
            raise data
        destination.write(data)


def _doc(file_id, name, mime, size=1000):
    return types.SimpleNamespace(photo=None, document=types.SimpleNamespace(
        file_id=file_id, file_name=name, mime_type=mime, file_size=size))


def _photo(file_id):
    sizes = [types.SimpleNamespace(file_id="small", file_size=100), types.SimpleNamespace(file_id=file_id, file_size=5000)]
    return types.SimpleNamespace(photo=sizes, document=None)


def _xlsx_bytes():
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Sotuv"
    sheet.append(["Mijoz", "Summa"])
    sheet.append(["DAVRON 9896", 2602529811])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


async def test_photo_pdf_and_office_files_become_model_content():
    bot = _TgBot({"p1": b"\xff\xd8jpeg", "pdf": b"%PDF-1.4 ...", "xl": _xlsx_bytes(), "txt": "Салом dunyo".encode("cp1251")})
    blocks, problems = await attachments.extract(
        [_photo("p1"), _doc("pdf", "shartnoma.pdf", "application/pdf"),
         _doc("xl", "sotuv.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
         _doc("txt", "izoh.txt", "text/plain")], bot)

    assert problems == []
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/jpeg"  # eng katta o'lcham olingan
    assert blocks[1]["type"] == "document" and blocks[1]["source"]["media_type"] == "application/pdf"
    assert '<attached_file name="sotuv.xlsx">' in blocks[2]["text"] and "DAVRON 9896\t2602529811" in blocks[2]["text"]
    assert "Салом dunyo" in blocks[3]["text"]                                                  # cp1251 ham o'qiladi


async def test_unreadable_attachments_are_reported_not_fatal():
    bot = _TgBot({"ok": b"png", "net": RuntimeError("telegram timeout")})
    blocks, problems = await attachments.extract(
        [_doc("zip", "arxiv.zip", "application/zip"), _doc("big", "katta.pdf", "application/pdf", size=30 * 1024 * 1024),
         _doc("net", "a.pdf", "application/pdf"), _doc("ok", "rasm.png", "image/png")], bot)

    assert len(blocks) == 1 and blocks[0]["source"]["media_type"] == "image/png"
    assert len(problems) == 3 and "o'qiy olmayman" in problems[0] and "juda katta" in problems[1]
    assert "yuklab bo'lmadi" in problems[2]


async def test_attachment_count_is_capped():
    bot = _TgBot({"p": b"jpeg"})
    blocks, problems = await attachments.extract([_photo("p")] * (attachments.MAX_ATTACHMENTS + 2), bot)
    assert len(blocks) == attachments.MAX_ATTACHMENTS and "eng ko'pi" in problems[0]


def test_plain_messages_have_no_attachment():
    assert not attachments.has_attachment(types.SimpleNamespace(text="salom"))
    assert attachments.has_attachment(_photo("p"))
