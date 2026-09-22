"""app/telegram/handlers.py va handlers_manager.py uchun testlar:

1. GENERAL topikda HECH QACHON Odoo vazifasi ochilmasligi (strukturaviy kafolat).
2. Boshqa guruhlarda faqat CEO xabari va ishonch >= 0.80 bo'lsagina eslatma ketishi.
3. Xatolik boshqaruvi: Odoo/Claude vaqtinchalik ishlamay qolsa, foydalanuvchi HECH
   QACHON jimgina "osilib qolmasligi" — aniq xabar olishi shart (ilgari bir nechta
   joyda try/except umuman yo'q edi, xato butun handler'ni jim-jim to'xtatardi).
4. Vazifa mas'uliga DM yuborish / ro'yxatdan o'tmagan holatda aniq ogohlantirish.

Barcha testlar to'liq mok'langan (Bot, Odoo, DB) — tarmoqqa yoki haqiqiy Telegram/
Odoo'ga chiqilmaydi.
"""
from __future__ import annotations

import types

import pytest

import app.telegram.handlers as handlers
import app.telegram.handlers_manager as handlers_manager
from app.services.llm import AnalyzeResult
from app.services.odoo import OdooError


# ── Umumiy fake'lar ──────────────────────────────────────────────────────────

class _FakeBot:
    def __init__(self):
        self.sent = []          # [(kwargs), ...] — send_message chaqiruvlari
        self.sent_voice = []    # [(kwargs), ...] — send_voice chaqiruvlari
        self.chat_actions = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return types.SimpleNamespace(message_id=1, chat=types.SimpleNamespace(id=kwargs.get("chat_id")))

    async def send_voice(self, **kwargs):
        self.sent_voice.append(kwargs)
        return types.SimpleNamespace(message_id=1, chat=types.SimpleNamespace(id=kwargs.get("chat_id")))

    async def send_chat_action(self, **kwargs):
        self.chat_actions.append(kwargs)

    async def download(self, *a, **kw):
        return None


class _FakeUser:
    def __init__(self, uid=111, username="testuser", full_name="Test User"):
        self.id = uid
        self.username = username
        self.full_name = full_name


class _FakeChat:
    def __init__(self, cid, ctype="supergroup", title="Test Group"):
        self.id = cid
        self.type = ctype
        self.title = title


class _FakeMessage:
    def __init__(self, text=None, thread_id=None, chat_id=-100999, user=None, voice=None):
        self.text = text
        self.caption = None
        self.voice = voice
        self.message_thread_id = thread_id
        self.message_id = 42
        self.chat = _FakeChat(chat_id)
        self.from_user = user or _FakeUser()
        self.replies = []

    async def reply(self, text, **kw):
        self.replies.append(text)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _QueueSession:
    """SessionLocal() o'rniga — session.execute(...) navbatdagi natijani qaytaradi,
    session.add()/commit() hech narsa qilmaydi (faqat yozib boradi)."""

    def __init__(self, queue):
        self._queue = queue
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *_a, **_kw):
        return _FakeResult(self._queue.pop(0) if self._queue else None)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


def _session_factory(*queue):
    """Har `SessionLocal()` chaqiruvi UMUMIY navbatdan keyingi qiymatni ishlatadi —
    testda nechta ketma-ket `session.execute(...)` bo'lishini bilgan holda yoziladi."""
    shared = list(queue)
    return lambda: _QueueSession(shared)


def _analyze_result(**overrides) -> AnalyzeResult:
    base = dict(
        is_task=True, is_task_confidence=0.9, title="Test vazifa",
        description="Test tavsif", assignee_name=None, deadline=None,
        department_scores={},
    )
    base.update(overrides)
    return AnalyzeResult(**base)


# ── 0) Bo'lim topiklari (on_topic_message) — HAMMA a'zoga ochiq ─────────────
# Spec (4-bo'lim): "Bo'lim mavzusi (asosiy guruh): Vazifalar o'sha bo'lim
# loyihasiga yoziladi. Savollar ham berilsa bo'ladi — AI javob beradi."
# CEO-only cheklov FAQAT "boshqa guruhlar" uchun (pastda) — bu yerga
# TEGMASLIK kerak (spec 4-bo'lim, "DIQQAT" eslatmasi).

async def test_topic_message_processes_non_ceo_sender(monkeypatch):
    """Bo'lim topikida istalgan xodim/menejer yozganda ham xabar odatdagidek
    qayta ishlanishi kerak (get_message_text chaqiriladi, CEO-filtri YO'Q)."""
    called = {"get_message_text": False}

    async def fake_get_message_text(message, bot):
        called["get_message_text"] = True
        return "Omborni tekshiring"

    async def fake_get_topic_by_telegram_id(session, thread_id):
        return None  # ro'yxatdan o'tmagan mavzu — assistant javobiga tushadi

    async def fake_assistant_reply(bot, text, thread_id=None):
        pass

    monkeypatch.setattr(handlers, "get_message_text", fake_get_message_text)
    monkeypatch.setattr(handlers.repo, "get_topic_by_telegram_id", fake_get_topic_by_telegram_id)
    monkeypatch.setattr(handlers, "_assistant_reply", fake_assistant_reply)
    monkeypatch.setattr(handlers, "SessionLocal", _session_factory())
    monkeypatch.setattr(handlers.settings, "CEO_TELEGRAM_USER_ID", 555)
    monkeypatch.setattr(handlers.settings, "CEO_TELEGRAM_USERNAME", "")

    bot = _FakeBot()
    message = _FakeMessage(text="Omborni tekshiring", thread_id=12, chat_id=handlers.settings.TELEGRAM_GROUP_ID,
                           user=_FakeUser(uid=111, username="boshqa_xodim"))
    await handlers.on_topic_message(message, bot)

    assert called["get_message_text"] is True


# ── 1) GENERAL topik hech qachon task yozmaydi ──────────────────────────────

async def test_general_topic_never_calls_analyze_message_or_write_task(monkeypatch):
    """Strukturaviy kafolat: on_general_message ichida analyze_message/_write_task
    chaqirilishi UMUMAN MUMKIN EMAS — funksiya faqat _assistant_reply'ga boradi
    (CEO xabari uchun — General topik faqat CEO uchun xizmat qiladi)."""
    called = {"analyze": False, "write_task": False, "assistant_reply": False}

    async def fake_analyze(text):
        called["analyze"] = True
        return _analyze_result()

    async def fake_write_task(*a, **kw):
        called["write_task"] = True

    async def fake_assistant_reply(bot, text, thread_id=None):
        called["assistant_reply"] = True

    async def fake_save_group_message(*a, **kw):
        return None

    monkeypatch.setattr(handlers, "analyze_message", fake_analyze)
    monkeypatch.setattr(handlers, "_write_task", fake_write_task)
    monkeypatch.setattr(handlers, "_assistant_reply", fake_assistant_reply)
    monkeypatch.setattr(handlers, "save_group_message", fake_save_group_message)
    monkeypatch.setattr(handlers, "SessionLocal", _session_factory())
    monkeypatch.setattr(handlers.settings, "CEO_TELEGRAM_USER_ID", 111)  # _FakeUser() default uid

    bot = _FakeBot()
    message = _FakeMessage(text="Xodimlar haqida ma'lumot", thread_id=None, chat_id=handlers.settings.TELEGRAM_GROUP_ID)
    await handlers.on_general_message(message, bot)

    assert called["analyze"] is False
    assert called["write_task"] is False
    assert called["assistant_reply"] is True  # CEO xabari haqiqatan ham javob olishi kerak


async def test_general_topic_ignores_non_ceo_sender(monkeypatch):
    """General topik faqat CEO uchun — boshqa a'zolar yozsa hech narsa (na Odoo
    tooli, na assistant javobi) ishga tushmasligi kerak."""
    called = {"assistant_reply": False}

    async def fake_assistant_reply(bot, text, thread_id=None):
        called["assistant_reply"] = True

    monkeypatch.setattr(handlers, "_assistant_reply", fake_assistant_reply)
    monkeypatch.setattr(handlers.settings, "CEO_TELEGRAM_USER_ID", 555)
    monkeypatch.setattr(handlers.settings, "CEO_TELEGRAM_USERNAME", "")

    bot = _FakeBot()
    message = _FakeMessage(text="Salom", thread_id=None, chat_id=handlers.settings.TELEGRAM_GROUP_ID,
                           user=_FakeUser(uid=111, username="boshqa_xodim"))
    await handlers.on_general_message(message, bot)

    assert called["assistant_reply"] is False
    assert bot.sent == []
    assert bot.chat_actions == []


# ── 2) Boshqa guruh — faqat CEO + ishonch >= 0.80 ──────────────────────────

async def test_other_group_ignores_non_ceo_sender(monkeypatch):
    called = {"analyze": False}

    async def fake_analyze(text):
        called["analyze"] = True
        return _analyze_result(is_task=True, is_task_confidence=0.99)

    monkeypatch.setattr(handlers, "analyze_message", fake_analyze)
    monkeypatch.setattr(handlers.settings, "CEO_TELEGRAM_USER_ID", 555)
    bot = _FakeBot()
    message = _FakeMessage(text="Buyurtma tuzing", chat_id=-200111, user=_FakeUser(uid=111))
    await handlers.on_other_group(message, bot)
    assert called["analyze"] is False  # CEO emas — hatto analyze ham chaqirilmasin


async def test_other_group_ceo_high_confidence_posts_note(monkeypatch):
    async def fake_analyze(text):
        return _analyze_result(is_task=True, is_task_confidence=0.95, department_scores={"Sotuv": 0.9})

    monkeypatch.setattr(handlers, "analyze_message", fake_analyze)
    monkeypatch.setattr(handlers.settings, "CEO_TELEGRAM_USER_ID", 555)
    bot = _FakeBot()
    message = _FakeMessage(text="Mijozga qo'ng'iroq qiling", chat_id=-200111, user=_FakeUser(uid=555))
    await handlers.on_other_group(message, bot)
    assert len(bot.sent) == 1
    assert bot.sent[0]["chat_id"] == handlers.settings.TELEGRAM_GROUP_ID
    assert "Manba" in bot.sent[0]["text"]


async def test_other_group_ceo_low_confidence_posts_nothing(monkeypatch):
    """Ishonch < 0.80 bo'lsa — hech narsa yuborilmasligi shart (spam qilinmasin)."""
    async def fake_analyze(text):
        return _analyze_result(is_task=True, is_task_confidence=0.5, department_scores={"Sotuv": 0.3})

    monkeypatch.setattr(handlers, "analyze_message", fake_analyze)
    monkeypatch.setattr(handlers.settings, "CEO_TELEGRAM_USER_ID", 555)
    bot = _FakeBot()
    message = _FakeMessage(text="Balki keyin", chat_id=-200111, user=_FakeUser(uid=555))
    await handlers.on_other_group(message, bot)
    assert bot.sent == []


async def test_other_group_never_writes_to_odoo(monkeypatch):
    """Boshqa guruhdan HECH QACHON Odoo'ga task yozilmasligi (faqat eslatma)."""
    called = {"create_task": False}

    async def fake_analyze(text):
        return _analyze_result(is_task=True, is_task_confidence=0.99, department_scores={"HR": 0.9})

    async def fake_create_task(*a, **kw):
        called["create_task"] = True
        return 1

    monkeypatch.setattr(handlers, "analyze_message", fake_analyze)
    monkeypatch.setattr(handlers.odoo, "create_task", fake_create_task)
    monkeypatch.setattr(handlers.settings, "CEO_TELEGRAM_USER_ID", 555)
    bot = _FakeBot()
    message = _FakeMessage(text="Xodim ishga oling", chat_id=-200111, user=_FakeUser(uid=555))
    await handlers.on_other_group(message, bot)
    assert called["create_task"] is False


# ── 3) Xatolik boshqaruvi — foydalanuvchi hech qachon jim qolmasligi ────────

async def test_write_task_odoo_failure_notifies_user_not_silent(monkeypatch):
    """Ilgari: create_task xato bersa (masalan Odoo timeout), handler jim-jim
    to'xtar edi — foydalanuvchi hech narsa ko'rmasdi. Endi aniq xabar borishi shart."""
    async def fake_resolve_assignee(name):
        return [], None, None

    async def fake_create_task(**kw):
        raise OdooError("Odoo 30s ichida javob bermadi (timeout)")

    monkeypatch.setattr(handlers, "_resolve_assignee", fake_resolve_assignee)
    monkeypatch.setattr(handlers.odoo, "create_task", fake_create_task)

    bot = _FakeBot()
    result = _analyze_result(title="Vazifa", assignee_name=None)
    await handlers._write_task(bot, result, odoo_project_id=39, department_label="HR", reply_thread_id=7)

    assert len(bot.sent) == 1
    assert "xatolik" in bot.sent[0]["text"].lower()
    assert "timeout" in bot.sent[0]["text"].lower()


async def test_write_task_assignee_resolution_failure_notifies_user(monkeypatch):
    async def fake_resolve_assignee(name):
        raise RuntimeError("Odoo ulanish xatosi")

    monkeypatch.setattr(handlers, "_resolve_assignee", fake_resolve_assignee)
    bot = _FakeBot()
    result = _analyze_result(assignee_name="Botir")
    await handlers._write_task(bot, result, odoo_project_id=39, department_label="HR", reply_thread_id=7)
    assert len(bot.sent) == 1
    assert "xatolik" in bot.sent[0]["text"].lower()


async def test_write_task_success_path_still_works(monkeypatch):
    """Regressiya: xato-handling qo'shilgani muvaffaqiyatli yo'lni buzmasligi kerak."""
    async def fake_resolve_assignee(name):
        return [], None, None

    async def fake_create_task(**kw):
        return 123

    async def fake_log(*a, **kw):
        return None

    monkeypatch.setattr(handlers, "_resolve_assignee", fake_resolve_assignee)
    monkeypatch.setattr(handlers.odoo, "create_task", fake_create_task)
    monkeypatch.setattr(handlers.repo, "save_task_log", fake_log)
    monkeypatch.setattr(handlers, "SessionLocal", _session_factory())

    bot = _FakeBot()
    result = _analyze_result(title="Vazifa", assignee_name=None)
    await handlers._write_task(bot, result, odoo_project_id=39, department_label="HR", reply_thread_id=7)
    assert len(bot.sent) == 1
    assert "Odooga yozildi" in bot.sent[0]["text"]


async def test_assistant_reply_ask_with_files_failure_notifies_user(monkeypatch):
    async def fake_ask_with_files(*a, **kw):
        raise RuntimeError("Anthropic API 500")

    monkeypatch.setattr(handlers, "ask_with_files", fake_ask_with_files)
    bot = _FakeBot()
    await handlers._assistant_reply(bot, "Nechta xodim bor?", thread_id=3)

    # Ilgari xato bo'lsa handler shu yerda jimgina to'xtardi (hech qanday
    # qo'shimcha xabarsiz) — endi "kuting" xabaridan KEYIN aniq xatolik xabari
    # ham borishi, va delivery.send_result kabi keyingi bosqichlarga
    # o'tilmasligi (jami roppa-rosa 2 ta xabar) shart.
    texts = [s["text"] for s in bot.sent]
    assert len(bot.sent) == 2
    assert any("kuting" in t.lower() for t in texts)
    assert any("xatolik" in t.lower() and "anthropic api 500" in t.lower() for t in texts)


async def test_on_delete_task_odoo_failure_notifies_manager(monkeypatch):
    class _FakeManager:
        odoo_project_id = 39

    async def fake_resolve_manager(message):
        return _FakeManager()

    class _FailingOdooClient:
        async def get_task(self, task_id):
            raise RuntimeError("Odoo bilan bog'lanib bo'lmadi")

    monkeypatch.setattr(handlers_manager, "_resolve_manager", fake_resolve_manager)
    monkeypatch.setattr(handlers_manager, "_manager_odoo", lambda m: _FailingOdooClient())

    sent = []

    class _FakeMsg:
        from_user = _FakeUser()

        async def answer(self, text, **kw):
            sent.append(text)

    class _Cmd:
        args = "42"

    await handlers_manager.on_delete_task(_FakeMsg(), _Cmd())
    assert len(sent) == 1
    assert "xatolik" in sent[0].lower()


# ── /otask: menejer FAQAT o'zi yaratgan vazifani o'chira olishi ────────────

async def test_on_delete_task_denies_when_not_creator(monkeypatch):
    """Bo'lim mos bo'lsa ham, vazifani BOSHQA foydalanuvchi yaratgan bo'lsa o'chirilmaydi."""
    class _FakeManager:
        odoo_project_id = 39

    async def fake_resolve_manager(message):
        return _FakeManager()

    class _FakeOdooClient:
        async def get_task(self, task_id):
            return {"id": task_id, "project_id": [39, "HR"]}

        async def delete_task(self, task_id):
            raise AssertionError("delete_task chaqirilmasligi kerak edi")

    class _FakeTaskLog:
        created_by_telegram_user_id = 999999  # boshqa foydalanuvchi

    async def fake_get_task_log(session, task_id):
        return _FakeTaskLog()

    monkeypatch.setattr(handlers_manager, "_resolve_manager", fake_resolve_manager)
    monkeypatch.setattr(handlers_manager, "_manager_odoo", lambda m: _FakeOdooClient())
    monkeypatch.setattr(handlers_manager.repo, "get_task_log_by_odoo_task_id", fake_get_task_log)
    monkeypatch.setattr(handlers_manager, "SessionLocal", _session_factory())

    sent = []

    class _FakeMsg:
        from_user = _FakeUser(uid=111)

        async def answer(self, text, **kw):
            sent.append(text)

    class _Cmd:
        args = "42"

    await handlers_manager.on_delete_task(_FakeMsg(), _Cmd())
    assert len(sent) == 1
    assert "yaratmagansiz" in sent[0].lower()


async def test_on_delete_task_denies_when_no_task_log_entry(monkeypatch):
    """Kuzatuvsiz (eski/task_log yozuvi yo'q) vazifalar uchun egalik tasdiqlanmagani
    sababli ATAYLAB rad etiladi — noaniq holatda ruxsat berilmaydi."""
    class _FakeManager:
        odoo_project_id = 39

    async def fake_resolve_manager(message):
        return _FakeManager()

    class _FakeOdooClient:
        async def get_task(self, task_id):
            return {"id": task_id, "project_id": [39, "HR"]}

        async def delete_task(self, task_id):
            raise AssertionError("delete_task chaqirilmasligi kerak edi")

    async def fake_get_task_log(session, task_id):
        return None

    monkeypatch.setattr(handlers_manager, "_resolve_manager", fake_resolve_manager)
    monkeypatch.setattr(handlers_manager, "_manager_odoo", lambda m: _FakeOdooClient())
    monkeypatch.setattr(handlers_manager.repo, "get_task_log_by_odoo_task_id", fake_get_task_log)
    monkeypatch.setattr(handlers_manager, "SessionLocal", _session_factory())

    sent = []

    class _FakeMsg:
        from_user = _FakeUser(uid=111)

        async def answer(self, text, **kw):
            sent.append(text)

    class _Cmd:
        args = "42"

    await handlers_manager.on_delete_task(_FakeMsg(), _Cmd())
    assert len(sent) == 1
    assert "yaratmagansiz" in sent[0].lower()


async def test_on_delete_task_allows_when_creator_matches(monkeypatch):
    """Regressiya: menejer O'ZI yaratgan, o'z bo'limidagi vazifani muvaffaqiyatli o'chira oladi."""
    class _FakeManager:
        odoo_project_id = 39

    async def fake_resolve_manager(message):
        return _FakeManager()

    deleted = {}

    class _FakeOdooClient:
        async def get_task(self, task_id):
            return {"id": task_id, "project_id": [39, "HR"]}

        async def delete_task(self, task_id):
            deleted["task_id"] = task_id

    class _FakeTaskLog:
        created_by_telegram_user_id = 111

    async def fake_get_task_log(session, task_id):
        return _FakeTaskLog()

    monkeypatch.setattr(handlers_manager, "_resolve_manager", fake_resolve_manager)
    monkeypatch.setattr(handlers_manager, "_manager_odoo", lambda m: _FakeOdooClient())
    monkeypatch.setattr(handlers_manager.repo, "get_task_log_by_odoo_task_id", fake_get_task_log)
    monkeypatch.setattr(handlers_manager, "SessionLocal", _session_factory())

    sent = []

    class _FakeMsg:
        from_user = _FakeUser(uid=111)

        async def answer(self, text, **kw):
            sent.append(text)

    class _Cmd:
        args = "42"

    await handlers_manager.on_delete_task(_FakeMsg(), _Cmd())
    assert deleted["task_id"] == 42
    assert len(sent) == 1
    assert "o'chirildi" in sent[0].lower()


# ── 4) DM bildirishnoma: ro'yxatdan o'tgan / o'tmagan / yuborib bo'lmadi ────

async def test_notify_assignee_sends_dm_when_employee_linked(monkeypatch):
    class _Emp:
        telegram_user_id = 777

    async def fake_get_employee_by_odoo_user_id(odoo_user_id):
        return _Emp()

    monkeypatch.setattr(handlers.employee_store, "get_employee_by_odoo_user_id", fake_get_employee_by_odoo_user_id)

    sent_dm = {}

    async def fake_send(bot, **kw):
        sent_dm.update(kw)
        return True

    monkeypatch.setattr(handlers, "_send_task_to_employee", fake_send)
    bot = _FakeBot()
    await handlers._notify_assignee_or_warn(
        bot, task_id=1, title="T", description="D", topic_name="HR",
        odoo_user_id=99, assignee_name="Botir", display_name="Botir", chat_id=-100, thread_id=5,
    )
    assert sent_dm["telegram_user_id"] == 777
    assert bot.sent == []  # guruhga qo'shimcha ogohlantirish yuborilmagan — hammasi yaxshi


async def test_notify_assignee_warns_group_when_not_registered(monkeypatch):
    async def fake_get_employee_by_odoo_user_id(odoo_user_id):
        return None

    async def fake_find_active_employee_by_name(name):
        return None  # ism bo'yicha ham botda ro'yxatdan o'tgan hodim topilmadi

    monkeypatch.setattr(handlers.employee_store, "get_employee_by_odoo_user_id", fake_get_employee_by_odoo_user_id)
    monkeypatch.setattr(handlers.employee_store, "find_active_employee_by_name", fake_find_active_employee_by_name)
    bot = _FakeBot()
    await handlers._notify_assignee_or_warn(
        bot, task_id=2, title="T", description="D", topic_name="HR",
        odoo_user_id=100, assignee_name="Kamila", display_name="Kamila", chat_id=-100, thread_id=5,
    )
    assert len(bot.sent) == 1
    assert "Kamila" in bot.sent[0]["text"]
    assert "ro'yxatdan o'tmaganligi" in bot.sent[0]["text"]


async def test_notify_assignee_finds_employee_by_name_when_no_odoo_link(monkeypatch):
    """Yangi xatti-harakat: odoo_user_id orqali topilmasa (yoki berilmagan bo'lsa),
    CEO aytgan ism bo'yicha to'g'ridan-to'g'ri employee_store'dan qidiriladi —
    Odoo login holatidan qat'iy nazar, botda /start bosgan bo'lsa xabar boradi."""
    class _Emp:
        telegram_user_id = 555

    async def fake_get_employee_by_odoo_user_id(odoo_user_id):
        return None

    async def fake_find_active_employee_by_name(name):
        assert name == "Nodira"
        return _Emp()

    monkeypatch.setattr(handlers.employee_store, "get_employee_by_odoo_user_id", fake_get_employee_by_odoo_user_id)
    monkeypatch.setattr(handlers.employee_store, "find_active_employee_by_name", fake_find_active_employee_by_name)

    sent_dm = {}

    async def fake_send(bot, **kw):
        sent_dm.update(kw)
        return True

    monkeypatch.setattr(handlers, "_send_task_to_employee", fake_send)
    bot = _FakeBot()
    await handlers._notify_assignee_or_warn(
        bot, task_id=9, title="T", description="D", topic_name="HR",
        odoo_user_id=None, assignee_name="Nodira", display_name="Nodira",
        chat_id=-100, thread_id=5, no_login=True,
    )
    assert sent_dm["telegram_user_id"] == 555
    assert bot.sent == []


async def test_notify_assignee_warns_group_when_dm_send_fails(monkeypatch):
    """Xodim ulangan, lekin DM yuborishning o'zi muvaffaqiyatsiz (masalan botni
    bloklagan) — guruhga shu haqda aniq ogohlantirish borishi kerak."""
    class _Emp:
        telegram_user_id = 777

    async def fake_get_employee_by_odoo_user_id(odoo_user_id):
        return _Emp()

    monkeypatch.setattr(handlers.employee_store, "get_employee_by_odoo_user_id", fake_get_employee_by_odoo_user_id)

    async def fake_send(bot, **kw):
        return False

    monkeypatch.setattr(handlers, "_send_task_to_employee", fake_send)
    bot = _FakeBot()
    await handlers._notify_assignee_or_warn(
        bot, task_id=3, title="T", description="D", topic_name="HR",
        odoo_user_id=101, assignee_name="Aziz", display_name="Aziz", chat_id=-100, thread_id=5,
    )
    assert len(bot.sent) == 1
    assert "Aziz" in bot.sent[0]["text"]
    assert "yuborib bo'lmadi" in bot.sent[0]["text"]


async def test_send_task_to_employee_includes_done_cancel_buttons(monkeypatch):
    """TTS ishlamasa (yoki sinovda o'chirilgan bo'lsa) ham, matnli xabarga
    Bajarildi/Bekor qilindi tugmalari qo'shilishi shart."""
    async def fake_tts(text):
        return b""  # TTS o'chirilgan/muvaffaqiyatsiz — matnli xabarga qaytish kerak

    monkeypatch.setattr(handlers, "synthesize_speech", fake_tts)
    bot = _FakeBot()
    ok = await handlers._send_task_to_employee(
        bot, task_id=55, title="Sinov", description="", topic_name="HR",
        telegram_user_id=999,
    )
    assert ok is True
    assert bot.sent_voice == []
    assert len(bot.sent) == 1
    markup = bot.sent[0]["reply_markup"]
    callback_datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "task_done:55" in callback_datas
    assert "task_cancel:55" in callback_datas


async def test_send_task_to_employee_sends_voice_with_caption_when_tts_ok(monkeypatch):
    async def fake_tts(text):
        assert "Sinov" in text
        return b"fake-ogg-opus-bytes"

    monkeypatch.setattr(handlers, "synthesize_speech", fake_tts)
    bot = _FakeBot()
    ok = await handlers._send_task_to_employee(
        bot, task_id=56, title="Sinov", description="Batafsil tavsif", topic_name="HR",
        telegram_user_id=999,
    )
    assert ok is True
    assert bot.sent == []
    assert len(bot.sent_voice) == 1
    call = bot.sent_voice[0]
    assert "Sinov" in call["caption"]
    markup = call["reply_markup"]
    callback_datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "task_done:56" in callback_datas
    assert "task_cancel:56" in callback_datas


async def test_send_task_to_employee_no_telegram_id_returns_false_no_send(monkeypatch):
    bot = _FakeBot()
    ok = await handlers._send_task_to_employee(
        bot, task_id=1, title="T", description="", topic_name="HR",
        telegram_user_id=None,
    )
    assert ok is False
    assert bot.sent == []
    assert bot.sent_voice == []


# ── /otask: CEO — bo'lim/egalik tekshiruvisiz istalgan vazifani o'chira oladi ──

async def test_on_delete_task_ceo_can_delete_any_task(monkeypatch):
    """CEO hali birorta ham menejer ro'yxatga olinmagan holatda ham /otask'ni
    o'zi sinab ko'ra olishi kerak — bo'lim/egalik tekshiruvi CEO'ga qo'llanmaydi."""
    monkeypatch.setattr(handlers_manager.settings, "CEO_TELEGRAM_USER_ID", 111)
    monkeypatch.setattr(handlers_manager.settings, "CEO_TELEGRAM_USERNAME", "")

    deleted = {}

    class _FakeAdminOdoo:
        async def get_task(self, task_id):
            return {"id": task_id, "project_id": [33, "Moliya"]}

        async def delete_task(self, task_id):
            deleted["task_id"] = task_id

    async def fail_resolve_manager(message):
        raise AssertionError("CEO uchun _resolve_manager chaqirilmasligi kerak")

    monkeypatch.setattr(handlers_manager, "_odoo", _FakeAdminOdoo())
    monkeypatch.setattr(handlers_manager, "_resolve_manager", fail_resolve_manager)

    sent = []

    class _FakeMsg:
        from_user = _FakeUser(uid=111)

        async def answer(self, text, **kw):
            sent.append(text)

    class _Cmd:
        args = "77"

    await handlers_manager.on_delete_task(_FakeMsg(), _Cmd())
    assert deleted["task_id"] == 77
    assert len(sent) == 1
    assert "o'chirildi" in sent[0].lower()


async def test_on_delete_task_ceo_not_found_still_reported(monkeypatch):
    monkeypatch.setattr(handlers_manager.settings, "CEO_TELEGRAM_USER_ID", 111)
    monkeypatch.setattr(handlers_manager.settings, "CEO_TELEGRAM_USERNAME", "")

    class _FakeAdminOdoo:
        async def get_task(self, task_id):
            return None

    monkeypatch.setattr(handlers_manager, "_odoo", _FakeAdminOdoo())

    sent = []

    class _FakeMsg:
        from_user = _FakeUser(uid=111)

        async def answer(self, text, **kw):
            sent.append(text)

    class _Cmd:
        args = "999"

    await handlers_manager.on_delete_task(_FakeMsg(), _Cmd())
    assert len(sent) == 1
    assert "topilmadi" in sent[0].lower()
