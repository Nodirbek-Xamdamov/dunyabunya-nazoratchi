"""Productionga tayyorlash bosqichida qo'shilgan imkoniyatlar: tushunarli xato matnlari,
prompt keshi, yaratilgan vazifani o'chirish, /yordam /id /status /brifing buyruqlari,
kunlik xulosa, monitoring, hisoblagichlar va o'tkazib yuborilgan eslatmani quvib yetish.

Barcha testlar mok'langan — tarmoqqa chiqilmaydi.
"""
from __future__ import annotations

import logging
import types
from datetime import datetime, time
from zoneinfo import ZoneInfo

import anthropic
import pytest

import app.alerts as alerts
import app.services.assistant as assistant_module
import app.services.briefing as briefing
import app.services.monitor as monitor
import app.services.status as status_service
import app.services.task_reminders as task_reminders
import app.telegram.handlers as handlers
import app.telegram.handlers_commands as commands
from app import metrics, runtime, scheduler_state
from app.services.errors import friendly_error
from app.services.llm import AnalyzeResult
from app.services.odoo import OdooError
from app.telegram import replies

TZ = ZoneInfo("Asia/Tashkent")


# ── Tushunarli xato matnlari ────────────────────────────────────────────────

def _api_error(cls, status_code=None):
    error = cls.__new__(cls)  # SDK konstruktori HTTP javobini talab qiladi — test uchun shart emas
    if status_code is not None:
        error.status_code = status_code
    return error


@pytest.mark.parametrize("error, expected", [
    (_api_error(anthropic.RateLimitError, 429), "limiti to'ldi"),
    (_api_error(anthropic.InternalServerError, 529), "band yoki vaqtincha ishlamayapti"),
    (_api_error(anthropic.AuthenticationError, 401), "kirish rad etildi"),
    (_api_error(anthropic.APITimeoutError), "juda kechikdi"),
    (_api_error(anthropic.APIConnectionError), "ulanib bo'lmadi"),
    (_api_error(anthropic.NotFoundError, 404), "modeli topilmadi"),
    (_api_error(anthropic.BadRequestError, 400), "so'rovni qabul qilmadi"),
])
def test_claude_errors_become_short_uzbek_explanations(error, expected):
    text = friendly_error(error)
    assert expected in text and "Error code" not in text and "{" not in text


def test_other_errors_keep_their_own_reason():
    assert friendly_error(OdooError("Odoo manzili topilmadi (DNS xatosi)", kind="dns")) == \
        "Odoo manzili topilmadi (DNS xatosi)"
    assert friendly_error(RuntimeError("Anthropic API 500")) == "Anthropic API 500"
    assert friendly_error(ValueError()) == "ValueError"


# ── Prompt keshi ────────────────────────────────────────────────────────────

class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


async def test_system_prompt_is_sent_as_a_cached_block_with_instruction_after_it(monkeypatch):
    async def fake_get_recent(session, uid):
        return []

    async def fake_add(*a, **kw):
        return None

    seen = {}

    async def fake_create(**kwargs):
        seen.update(kwargs)
        usage = types.SimpleNamespace(input_tokens=300, cache_read_input_tokens=10748,
                                      cache_creation_input_tokens=0, output_tokens=12)
        return types.SimpleNamespace(stop_reason="end_turn", usage=usage,
                                     content=[types.SimpleNamespace(type="text", text="Salom.")])

    monkeypatch.setattr(assistant_module.repo, "get_recent_conversation", fake_get_recent)
    monkeypatch.setattr(assistant_module.repo, "add_conversation_message", fake_add)
    monkeypatch.setattr(assistant_module, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(assistant_module._client.messages, "create", fake_create)

    await assistant_module.ask_with_files(1, "salom", instruction="Excel qilib bering")

    stable, extra = seen["system"]
    # Keshlanadigan blok — har so'rovda bir xil bo'lishi shart bo'lgan qism; o'zgaruvchan
    # ko'rsatma undan KEYIN turadi (aks holda kesh har so'rovda yangidan yozilardi)
    assert stable["cache_control"] == {"type": "ephemeral"} and "XAVFSIZLIK" in stable["text"]
    assert "cache_control" not in extra and "Excel qilib bering" in extra["text"]
    assert "Excel qilib bering" not in stable["text"]
    # Token sarfi hisoblagichlarga tushadi (kesh ulushi /status da ko'rinadi)
    today = metrics.totals(1)
    assert today["questions"] == 1 and today["tokens_cached"] == 10748 and today["tokens_in"] == 11048


def test_claude_client_does_not_wait_ten_minutes():
    assert assistant_module._client.timeout.read == 90.0 and assistant_module._client.max_retries == 2


# ── Yaratilgan vazifa: raqam, Odoo havolasi, o'chirish ──────────────────────

class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def send_chat_action(self, **kwargs):
        pass


class _ButtonMessage:
    def __init__(self, text="✅ Odooga yozildi — Sotuv\n📌 Hisobot"):
        self.text, self.caption = text, None
        self.chat = types.SimpleNamespace(id=-100123)
        self.message_thread_id = 5
        self.edits = []

    async def edit_text(self, text, **kw):
        self.edits.append(("text", text, kw))

    async def edit_reply_markup(self, **kw):
        self.edits.append(("markup", kw.get("reply_markup"), kw))


def _callback(data, uid=1):
    answers = []

    async def answer(*a, **kw):
        answers.append((a, kw))

    return types.SimpleNamespace(data=data, answer=answer, answers=answers, message=_ButtonMessage(),
                                 from_user=types.SimpleNamespace(id=uid, username="u", full_name="U"))


def _buttons(markup):
    return [(b.text, b.callback_data, b.url) for row in markup.inline_keyboard for b in row]


@pytest.fixture
def ceo_is_user_1(monkeypatch):
    monkeypatch.setattr(handlers.settings, "CEO_TELEGRAM_USER_ID", 1)
    monkeypatch.setattr(handlers.settings, "CEO_TELEGRAM_USERNAME", "")


async def test_created_task_reply_shows_id_and_action_buttons(monkeypatch, ceo_is_user_1):
    async def fake_resolve(name):
        return [], None, None

    async def fake_create_task(**kw):
        return 321

    async def fake_save(session, **kw):
        return None

    monkeypatch.setattr(handlers, "_resolve_assignee", fake_resolve)
    monkeypatch.setattr(handlers.odoo, "create_task", fake_create_task)
    monkeypatch.setattr(handlers.repo, "save_task_log", fake_save)
    monkeypatch.setattr(handlers, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(handlers.settings, "ODOO_URL", "https://odoo.example.com/")

    bot = _Bot()
    result = AnalyzeResult(is_task=True, title="Hisobot tayyorlash", description="Hisobot tayyorlash")
    await handlers._write_task(bot, result, 28, "Sotuv", reply_thread_id=5)

    sent = bot.sent[0]
    assert "🆔 #321" in sent["text"]
    assert _buttons(sent["reply_markup"]) == [
        ("🔗 Odoo'da ochish", None, "https://odoo.example.com/web#id=321&model=project.task&view_type=form"),
        ("🗑 O'chirish", "task_del:321", None),
    ]
    assert metrics.totals(1)["tasks_created"] == 1


async def test_task_delete_asks_first_then_deletes(monkeypatch, ceo_is_user_1):
    deleted = []

    async def fake_delete(task_id):
        deleted.append(task_id)

    monkeypatch.setattr(handlers.odoo, "delete_task", fake_delete)

    ask = _callback("task_del:321")
    await handlers.on_task_delete_ask(ask)
    assert deleted == []  # bir bosishda o'chmaydi — avval tasdiq so'raladi
    assert [b[1] for b in _buttons(ask.message.edits[0][1])] == ["task_delok:321", "task_delno:321"]

    keep = _callback("task_delno:321")
    await handlers.on_task_delete_cancel(keep)
    assert deleted == [] and [b[1] for b in _buttons(keep.message.edits[0][1])] == [None, "task_del:321"]

    confirm = _callback("task_delok:321")
    await handlers.on_task_delete_confirm(confirm, _Bot())
    assert deleted == [321]
    kind, text, kw = confirm.message.edits[0]
    assert kind == "text" and text.endswith("🗑 <b>Vazifa Odoo'dan o'chirildi</b>") and kw["reply_markup"] is None


async def test_task_delete_is_ceo_only_and_reports_odoo_failure(monkeypatch, ceo_is_user_1):
    async def failing_delete(task_id):
        raise OdooError("Odoo so'rovni rad etdi: Record does not exist")

    monkeypatch.setattr(handlers.odoo, "delete_task", failing_delete)

    stranger = _callback("task_delok:321", uid=2)
    await handlers.on_task_delete_confirm(stranger, _Bot())
    assert stranger.message.edits == [] and stranger.answers[0][1].get("show_alert") is True

    bot = _Bot()
    ceo = _callback("task_delok:321")
    await handlers.on_task_delete_confirm(ceo, bot)
    assert "o'chirib bo'lmadi: Odoo so'rovni rad etdi" in bot.sent[0]["text"]
    assert ceo.message.edits[0][0] == "markup"  # tugmalar qaytariladi — qayta urinish mumkin


# ── Buyruqlar ───────────────────────────────────────────────────────────────

class _Message:
    def __init__(self, *, uid=1, username="ceo", chat_type="private", chat_id=1, thread_id=None):
        self.from_user = types.SimpleNamespace(id=uid, username=username)
        self.chat = types.SimpleNamespace(id=chat_id, type=chat_type)
        self.message_thread_id = thread_id
        self.answers = []

    async def answer(self, text, **kw):
        self.answers.append(text)


@pytest.fixture
def ceo_user(monkeypatch):
    monkeypatch.setattr(commands.ceo_state.settings, "CEO_TELEGRAM_USER_ID", 1)
    monkeypatch.setattr(commands.ceo_state.settings, "CEO_TELEGRAM_USERNAME", "")


async def test_help_text_depends_on_who_asks(monkeypatch, ceo_user):
    async def no_manager(session, uid):
        return None

    monkeypatch.setattr(commands, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(commands.repo, "get_manager_by_telegram_id", no_manager)

    ceo = _Message()
    await commands.on_help(ceo)
    assert "/brifing" in ceo.answers[0] and "Vazifa bering" in ceo.answers[0]

    employee = _Message(uid=7, username="xodim")
    await commands.on_help(employee)
    assert "/start" in employee.answers[0] and "/brifing" not in employee.answers[0]

    stranger_in_group = _Message(uid=7, username="xodim", chat_type="supergroup", chat_id=-100)
    await commands.on_help(stranger_in_group)
    assert stranger_in_group.answers == []  # guruhda bot faqat CEO'ga javob beradi


async def test_id_command_shows_chat_ids(ceo_user):
    message = _Message(chat_type="supergroup", chat_id=-100555, thread_id=9)
    await commands.on_id(message)
    assert "<code>1</code>" in message.answers[0] and "<code>-100555</code>" in message.answers[0]
    assert "<code>9</code>" in message.answers[0]


async def test_status_and_briefing_are_ceo_only(monkeypatch, ceo_user):
    async def fail(*a, **kw):
        raise AssertionError("CEO bo'lmagan foydalanuvchi uchun Odoo'ga chiqilmasligi kerak")

    monkeypatch.setattr(commands.status_service, "collect_status", fail)
    monkeypatch.setattr(commands.briefing, "collect_briefing", fail)

    private = _Message(uid=7, username="xodim")
    await commands.on_status(private)
    assert private.answers == [replies.not_ceo_text()]

    group = _Message(uid=7, username="xodim", chat_type="supergroup", chat_id=-100)
    await commands.on_briefing(group, _Bot())
    assert group.answers == []


async def test_briefing_command_answers_with_the_summary(monkeypatch, ceo_user):
    async def fake_collect():
        return _BRIEFING

    monkeypatch.setattr(commands.briefing, "collect_briefing", fake_collect)
    message = _Message()
    await commands.on_briefing(message, _Bot())
    assert message.answers[0].startswith("<b>Kunlik xulosa</b> — 2026-10-05, dushanba")


# ── Kunlik xulosa ───────────────────────────────────────────────────────────

_BRIEFING = {
    "sana": "2026-10-05", "hafta_kuni": "dushanba",
    "sotuv": {
        "bugun": [],
        "kecha": [{"valyuta": "UZS", "summa": 478_960_000.0, "soni": 200}],
        "oy_boshidan": [{"valyuta": "UZS", "summa": 2_620_000_000.0, "soni": 1281}],
        "otgan_oy_shu_davrda": [{"valyuta": "UZS", "summa": 3_420_000_000.0, "soni": 1500}],
        "otgan_oy_davri": "2026-09-01 — 2026-09-05",
    },
    "tolovlar": {"bugun": [], "kecha": [{"valyuta": "USD", "kirim": 0.0, "chiqim": 810.0},
                                        {"valyuta": "UZS", "kirim": 95_790_000.0, "chiqim": 12_050_000.0}]},
    "qarzdorlik": {"jami": [{"valyuta": "UZS", "summa": 15_630_000_000.0, "soni": 5846}],
                   "muddati_otgan": [{"valyuta": "UZS", "summa": 11_470_000_000.0, "soni": 5382}]},
    "vazifalar": {"ochiq": 20, "muddati_otgan": 2},
}


def test_briefing_text_covers_sales_payments_debt_and_tasks():
    text = briefing.format_briefing(_BRIEFING)
    assert "Bugun: yo'q" in text
    assert "Kecha: 478.96 mln UZS (200 ta buyurtma)" in text
    # Taqqoslash teng davrlar o'rtasida: oy boshidan vs o'tgan oyning shu kunigacha
    assert "Oy boshidan: 2.62 mlrd UZS (o'tgan oyning shu davrida 3.42 mlrd UZS — −23%)" in text
    # Valyutalar qo'shilmaydi — har biri alohida qatorda
    assert "Kecha: kirim 0, chiqim 810 USD" in text
    assert "Kecha: kirim 95.79 mln, chiqim 12.05 mln UZS" in text
    assert "Muddati o'tgan: 11.47 mlrd UZS (5 382 ta faktura)" in text
    assert "Ochiq: 20 ta, shundan muddati o'tgani: 2 ta" in text


def test_briefing_change_is_shown_only_for_one_matching_currency():
    uzs = [{"valyuta": "UZS", "summa": 110.0}]
    assert briefing._change(uzs, [{"valyuta": "UZS", "summa": 100.0}]) == " — +10%"
    assert briefing._change(uzs, [{"valyuta": "USD", "summa": 100.0}]) == ""
    assert briefing._change(uzs, []) == ""


def test_briefing_schedule_is_off_by_default_and_rejects_bad_time(monkeypatch, caplog):
    monkeypatch.setattr(briefing.settings, "DAILY_BRIEFING_TIME", "")
    assert briefing.briefing_time() is None
    monkeypatch.setattr(briefing.settings, "DAILY_BRIEFING_TIME", "08:30")
    assert briefing.briefing_time() == time(8, 30)
    monkeypatch.setattr(briefing.settings, "DAILY_BRIEFING_TIME", "ertalab")
    with caplog.at_level("ERROR", logger="app.services.briefing"):
        assert briefing.briefing_time() is None
    assert "noto'g'ri" in caplog.text


# ── Monitoring ──────────────────────────────────────────────────────────────

def test_watch_reports_an_outage_once_and_announces_recovery(monkeypatch, caplog):
    notices = []
    monkeypatch.setattr(monitor.alerts, "notify", notices.append)
    watch = monitor._Watch("Odoo bilan aloqa")

    with caplog.at_level("ERROR", logger="app.services.monitor"):
        watch.update("Odoo manzili topilmadi (DNS xatosi)", ".env dagi ODOO_URL ni yangilang")
        watch.update("Odoo manzili topilmadi (DNS xatosi)")  # uzilish davom etyapti — takror xabar yo'q
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "DNS xatosi" in errors[0].getMessage() and "ODOO_URL" in errors[0].getMessage()

    watch.update(None)
    assert notices == ["✅ Odoo bilan aloqa tiklandi (1 daqiqa ishlamadi)"]
    watch.update(None)
    assert len(notices) == 1


def test_notify_queues_a_message_only_when_alerts_are_on(monkeypatch):
    monkeypatch.setattr(alerts, "_active", None)
    alerts.notify("tiklandi")  # ogohlantirishlar o'chiq — xato bermaydi

    handler = alerts.TelegramAlertHandler()
    monkeypatch.setattr(alerts, "_active", handler)
    alerts.notify("✅ Odoo bilan aloqa tiklandi")
    assert "Odoo bilan aloqa tiklandi" in handler.drain()


async def test_old_conversations_are_purged_only_when_retention_is_set(monkeypatch):
    calls = []

    async def fake_delete(session, days):
        calls.append(days)
        return 12

    monkeypatch.setattr(monitor, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(monitor.repo, "delete_old_conversation_messages", fake_delete)

    monkeypatch.setattr(monitor.settings, "CONVERSATION_RETENTION_DAYS", 0)
    assert await monitor.purge_old_conversations() == 0 and calls == []  # sukut: hech narsa o'chirilmaydi
    monkeypatch.setattr(monitor.settings, "CONVERSATION_RETENTION_DAYS", 90)
    assert await monitor.purge_old_conversations() == 12 and calls == [90]


# ── O'tkazib yuborilgan kunlik ishni quvib yetish ───────────────────────────

def test_daily_reminder_catches_up_after_downtime_but_runs_once_a_day():
    at = lambda h, m=0: datetime(2026, 10, 5, h, m, tzinfo=TZ)  # noqa: E731
    assert task_reminders.is_due(at(8, 59), None) is False          # hali erta
    assert task_reminders.is_due(at(9, 0), None) is True
    assert task_reminders.is_due(at(13, 30), "2026-10-04") is True  # bot 09:00 da o'chiq edi — kechikib yuboriladi
    assert task_reminders.is_due(at(13, 30), "2026-10-05") is False  # bugun yuborilgan
    assert task_reminders.is_due(at(21, 0), "2026-10-04") is False   # juda kech — ertaga


def test_scheduler_state_survives_a_restart():
    assert scheduler_state.last_run("task_reminders") is None
    scheduler_state.mark_run("task_reminders", "2026-10-05")
    scheduler_state.mark_run("briefing", "2026-10-04")
    assert scheduler_state.last_run("task_reminders") == "2026-10-05"
    assert scheduler_state.last_run("briefing") == "2026-10-04"


# ── Hisoblagichlar va holat ─────────────────────────────────────────────────

def test_metrics_are_persisted_and_reloaded(monkeypatch):
    metrics.inc("questions", 3)
    metrics.inc("errors")
    metrics.flush()
    assert metrics._PATH.exists()

    monkeypatch.setattr(metrics, "_days", {})
    monkeypatch.setattr(metrics, "_loaded", False)  # bot qayta ishga tushdi
    assert metrics.totals(7) == {"questions": 3, "errors": 1}


def test_error_log_records_are_counted():
    counter = metrics.ErrorCounter()
    counter.emit(logging.LogRecord("app.x", logging.ERROR, __file__, 1, "xato", None, None))
    assert metrics.totals(1)["errors"] == 1 and counter.level == logging.ERROR


def _status(**overrides):
    base = {
        "version": "master@abc1234", "uptime": 3 * 3600 + 12 * 60, "polling_ok": True,
        "polling": {"polling": "ok", "seconds_since_last_poll": 2},
        "odoo": {"aloqa": True, "server": "dunyabunya.odoo.com", "odoo_versiya": "18.0+e", "javob_vaqti_ms": 180},
        "odoo_test": False, "db_error": None, "backup": None, "free_gb": 241.0, "alerts": True,
        "today": {"questions": 12, "tasks_created": 3, "reports": 2, "errors": 0},
        "week": {"questions": 80, "tokens_in": 1000, "tokens_cached": 900}, "problems": [],
    }
    base.update(overrides)
    return base


def test_status_text_shows_health_and_usage():
    text = status_service.format_status(_status())
    assert "✅ Bot ishlayapti: 3 soat 12 daqiqa (versiya master@abc1234)" in text
    assert "✅ Odoo: dunyabunya.odoo.com (versiya 18.0+e, javob 180 ms)" in text
    assert "❌ Zaxira nusxa topilmadi" in text
    assert "Bugun: 12 savol, 3 vazifa, 2 fayl, 0 xato" in text and "Kesh ulushi (7 kun): 90%" in text
    assert "SINOV" not in text


def test_status_text_warns_about_test_odoo_and_outages():
    text = status_service.format_status(_status(
        odoo_test=True, alerts=False, db_error="connection refused",
        odoo={"aloqa": False, "server": "dunyabunya-test-1.dev.odoo.com",
              "sabab": "Odoo manzili topilmadi (DNS xatosi)", "tavsiya": "ODOO_URL ni yangilang"},
    ))
    assert "❌ Odoo: dunyabunya-test-1.dev.odoo.com — Odoo manzili topilmadi (DNS xatosi)" in text
    assert "ODOO_URL ni yangilang" in text and "SINOV bazasi" in text
    assert "❌ Ma'lumotlar bazasi: connection refused" in text
    assert "Xato ogohlantirishlari o'chiq" in text


def test_code_version_is_reported():
    assert isinstance(runtime.code_version(), str) and runtime.code_version()
