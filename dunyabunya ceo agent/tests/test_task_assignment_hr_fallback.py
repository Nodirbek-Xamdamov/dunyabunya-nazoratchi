"""Vazifa ISM BO'YICHA berilganda hr.employee zaxira qidiruvi uchun testlar.

Xuddi /start ro'yxatdan o'tishdagi kabi: 180 ta faol xodimdan 134 tasida Odoo
LOGIN (res.users) hisobi yo'q. Ilgari CEO "Karimga topshir" desa va Karim faqat
hr.employee'da bo'lsa (aksariyat xodimlar kabi), bot "Karim topilmadi" deb
NOTO'G'RI xabar berardi — aslida Karim MAVJUD, faqat unga Odoo'da RASMAN
biriktirib bo'lmaydi (project.task.user_ids faqat res.users qabul qiladi).

Bu fayl: _find_odoo_users/_resolve_assignee to'g'ri manba (source) bilan
belgilashini, va _write_task/on_task_assign shunga qarab to'g'ri (aniq, "topilmadi"
emas) xabar berishini tekshiradi. Barcha testlar mok'langan — tarmoqqa chiqilmaydi.
"""
from __future__ import annotations

import types

import pytest

import app.telegram.handlers as handlers
from app.services.llm import AnalyzeResult


def _analyze_result(**overrides) -> AnalyzeResult:
    base = dict(
        is_task=True, is_task_confidence=0.9, title="Test vazifa",
        description="Test tavsif", assignee_name=None, deadline=None,
        department_scores={},
    )
    base.update(overrides)
    return AnalyzeResult(**base)


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return types.SimpleNamespace(message_id=1)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _QueueSession:
    def __init__(self, queue):
        self._queue = queue

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **kw):
        return _FakeResult(self._queue.pop(0) if self._queue else None)

    def add(self, obj):
        pass

    async def commit(self):
        pass


def _session_factory(*queue):
    shared = list(queue)
    return lambda: _QueueSession(shared)


@pytest.fixture(autouse=True)
def _clear_emp_cache():
    handlers._emp_cache.clear()
    handlers._emp_cache_ts.clear()
    yield
    handlers._emp_cache.clear()
    handlers._emp_cache_ts.clear()


# ── _find_odoo_users: manba (source) to'g'ri belgilanishi ───────────────────

async def test_find_odoo_users_tags_res_users_source(monkeypatch):
    async def fake_get_employees_by_name(name):
        return []

    async def fake_find_users_by_name(name):
        return [{"id": 10, "name": "Sardor Aliyev", "score": 100.0}]

    monkeypatch.setattr(handlers.employee_store, "get_employees_by_name", fake_get_employees_by_name)
    monkeypatch.setattr(handlers.odoo, "find_users_by_name", fake_find_users_by_name)

    result = await handlers._find_odoo_users("Sardor")
    assert result[0]["source"] == "res.users"
    assert result[0]["id"] == 10


async def test_find_odoo_users_falls_back_to_hr_employee_when_res_users_empty(monkeypatch):
    """Asosiy regressiya testi: res.users'da yo'q, lekin hr.employee'da bor
    xodim endi TOPILISHI shart (ilgari butunlay ko'rinmas edi)."""
    async def fake_get_employees_by_name(name):
        return []

    async def fake_find_users_by_name(name):
        return []

    async def fake_find_employees_by_name(name):
        return [{"id": 749, "name": "Atabekov Rovshan", "score": 100.0}]

    monkeypatch.setattr(handlers.employee_store, "get_employees_by_name", fake_get_employees_by_name)
    monkeypatch.setattr(handlers.odoo, "find_users_by_name", fake_find_users_by_name)
    monkeypatch.setattr(handlers.odoo, "find_employees_by_name", fake_find_employees_by_name)

    result = await handlers._find_odoo_users("Atabekov")
    assert len(result) == 1
    assert result[0]["source"] == "hr.employee"
    assert result[0]["id"] == 749


async def test_find_odoo_users_empty_when_neither_source_matches(monkeypatch):
    async def fake_get_employees_by_name(name):
        return []

    async def fake_find_users_by_name(name):
        return []

    async def fake_find_employees_by_name(name):
        return []

    monkeypatch.setattr(handlers.employee_store, "get_employees_by_name", fake_get_employees_by_name)
    monkeypatch.setattr(handlers.odoo, "find_users_by_name", fake_find_users_by_name)
    monkeypatch.setattr(handlers.odoo, "find_employees_by_name", fake_find_employees_by_name)

    assert await handlers._find_odoo_users("Notanish Ism") == []


# ── _resolve_assignee: 3 qiymatli natija ─────────────────────────────────────

async def test_resolve_assignee_confident_res_users_match(monkeypatch):
    async def fake_get_employee_by_name(name):
        return None

    async def fake_find_users_by_name(name):
        return [{"id": 42, "name": "Botir Aliyev", "score": 95.0}]

    monkeypatch.setattr(handlers.employee_store, "get_employee_by_name", fake_get_employee_by_name)
    monkeypatch.setattr(handlers.odoo, "find_users_by_name", fake_find_users_by_name)

    user_ids, unresolved, no_login = await handlers._resolve_assignee("Botir")
    assert user_ids == [42]
    assert unresolved is None
    assert no_login is None


async def test_resolve_assignee_hr_employee_only_returns_no_login_name(monkeypatch):
    """Regressiya: hr.employee'da aniq topilsa, 'topilmadi' emas — no_login_name
    orqali ANIQ sababi qaytishi shart."""
    async def fake_get_employee_by_name(name):
        return None

    async def fake_find_users_by_name(name):
        return []

    async def fake_find_employees_by_name(name):
        return [{"id": 749, "name": "Atabekov Rovshan", "score": 100.0}]

    monkeypatch.setattr(handlers.employee_store, "get_employee_by_name", fake_get_employee_by_name)
    monkeypatch.setattr(handlers.odoo, "find_users_by_name", fake_find_users_by_name)
    monkeypatch.setattr(handlers.odoo, "find_employees_by_name", fake_find_employees_by_name)

    user_ids, unresolved, no_login = await handlers._resolve_assignee("Atabekov")
    assert user_ids == []
    assert unresolved is None
    assert no_login == "Atabekov Rovshan"


async def test_resolve_assignee_nothing_found_anywhere(monkeypatch):
    async def fake_get_employee_by_name(name):
        return None

    async def fake_find_users_by_name(name):
        return []

    async def fake_find_employees_by_name(name):
        return []

    monkeypatch.setattr(handlers.employee_store, "get_employee_by_name", fake_get_employee_by_name)
    monkeypatch.setattr(handlers.odoo, "find_users_by_name", fake_find_users_by_name)
    monkeypatch.setattr(handlers.odoo, "find_employees_by_name", fake_find_employees_by_name)

    user_ids, unresolved, no_login = await handlers._resolve_assignee("Hech kim")
    assert user_ids == []
    assert unresolved == "Hech kim"
    assert no_login is None


# ── _write_task: no_login_name uchun aniq xabar (avtomatik yo'l) ────────────

async def test_write_task_no_login_name_sends_accurate_message_not_not_found(monkeypatch):
    async def fake_resolve_assignee(name):
        return [], None, "Atabekov Rovshan"

    async def fake_create_task(**kw):
        assert kw.get("user_ids") in (None, [])
        return 500

    async def fake_log(*a, **kw):
        return None

    async def fake_find_active_employee_by_name(name):
        return None  # botda ham ro'yxatdan o'tmagan — sof "login yo'q" holati

    monkeypatch.setattr(handlers, "_resolve_assignee", fake_resolve_assignee)
    monkeypatch.setattr(handlers.odoo, "create_task", fake_create_task)
    monkeypatch.setattr(handlers.repo, "save_task_log", fake_log)
    monkeypatch.setattr(handlers, "SessionLocal", _session_factory())
    monkeypatch.setattr(handlers.employee_store, "find_active_employee_by_name", fake_find_active_employee_by_name)

    bot = _FakeBot()
    result = _analyze_result(assignee_name="Atabekov")
    await handlers._write_task(bot, result, odoo_project_id=39, department_label="HR", reply_thread_id=7)

    texts = " ".join(s["text"] for s in bot.sent)
    assert "Atabekov Rovshan" in texts
    assert "login" in texts.lower()
    assert "topilmadi" not in texts.lower()  # noto'g'ri/chalg'ituvchi xabar EMAS


async def test_write_task_no_login_name_still_notifies_if_registered_on_telegram(monkeypatch):
    """Yangi xatti-harakat: Odoo login yo'q, lekin CEO aytgan ism bilan xodim
    botda /start bosgan bo'lsa (employee_store'da ro'yxatdan o'tgan) — baribir
    ovozli xabar+summary yuborilishi kerak, faqat guruhga "login yo'q" deb
    qo'ya qolinmasligi kerak."""
    async def fake_resolve_assignee(name):
        return [], None, "Atabekov Rovshan"

    async def fake_create_task(**kw):
        return 501

    async def fake_log(*a, **kw):
        return None

    class _Emp:
        telegram_user_id = 4242

    async def fake_find_active_employee_by_name(name):
        assert name == "Atabekov Rovshan"
        return _Emp()

    sent_to = {}

    async def fake_send(bot, **kw):
        sent_to.update(kw)
        return True

    monkeypatch.setattr(handlers, "_resolve_assignee", fake_resolve_assignee)
    monkeypatch.setattr(handlers.odoo, "create_task", fake_create_task)
    monkeypatch.setattr(handlers.repo, "save_task_log", fake_log)
    monkeypatch.setattr(handlers, "SessionLocal", _session_factory())
    monkeypatch.setattr(handlers.employee_store, "find_active_employee_by_name", fake_find_active_employee_by_name)
    monkeypatch.setattr(handlers, "_send_task_to_employee", fake_send)

    bot = _FakeBot()
    result = _analyze_result(assignee_name="Atabekov")
    await handlers._write_task(bot, result, odoo_project_id=39, department_label="HR", reply_thread_id=7)

    assert sent_to["telegram_user_id"] == 4242
    texts = " ".join(s["text"] for s in bot.sent)
    assert "login hisobi yo'qligi" not in texts


# ── on_task_assign: hr.employee-manbali tanlov ───────────────────────────────

class _FakeUser:
    def __init__(self, uid=1):
        self.id = uid
        self.username = "u"
        self.full_name = "U"


class _FakeMsg:
    def __init__(self, chat_id=-100, thread_id=5):
        self.chat = types.SimpleNamespace(id=chat_id)
        self.message_thread_id = thread_id
        self.text = "Vazifa"
        self.edited = []

    async def edit_text(self, text, **kw):
        self.edited.append(text)


class _FakeCallback:
    def __init__(self, data, uid=1):
        self.data = data
        self.from_user = _FakeUser(uid)
        self.message = _FakeMsg()
        self.answers = []

    async def answer(self, *a, **kw):
        self.answers.append((a, kw))


@pytest.fixture(autouse=True)
def _ceo_is_user_1(monkeypatch):
    # task_assign/task_skip tugmalari faqat CEO uchun — bu testlarda bosuvchi (uid=1) CEO.
    monkeypatch.setattr(handlers.settings, "CEO_TELEGRAM_USER_ID", 1)


async def test_on_task_assign_rejects_non_ceo_click(monkeypatch):
    """CEO bo'lmagan guruh a'zosi "Ha, topshirish"ni bossa — Odoo'ga hech narsa
    yozilmaydi, kutilayotgan tanlov saqlanib qoladi, ogohlantirish chiqadi."""
    monkeypatch.setattr(handlers.settings, "CEO_TELEGRAM_USERNAME", "")
    thread_id = 91
    handlers._pending_task_selections[thread_id] = {"sentinel": True}

    async def fail_create_task(**kw):
        raise AssertionError("CEO bo'lmagan bosuvchi uchun vazifa yaratilmasligi kerak")

    monkeypatch.setattr(handlers.odoo, "create_task", fail_create_task)

    callback = _FakeCallback(f"task_assign:{thread_id}:10", uid=2)
    try:
        await handlers.on_task_assign(callback, _FakeBot())
        assert handlers._pending_task_selections[thread_id] == {"sentinel": True}
        assert callback.message.edited == []
        assert callback.answers and callback.answers[0][1].get("show_alert") is True
    finally:
        handlers._pending_task_selections.pop(thread_id, None)


async def test_on_task_assign_hr_employee_source_no_user_ids_and_warns(monkeypatch):
    """Asosiy topilma: hr.employee orqali tanlangan nomzod uchun create_task
    user_ids=None bilan chaqirilishi (Odoo'ga noto'g'ri ID yuborilmasligi) VA
    guruhga "login yo'q" degan aniq xabar borishi shart — DM urinishi EMAS."""
    thread_id = 77
    handlers._pending_task_selections[thread_id] = {
        "result": _analyze_result(title="Omborni tekshirish", assignee_name="Atabekov"),
        "topic": types.SimpleNamespace(odoo_project_id=39, name="HR"),
        "users": [{"id": 749, "name": "Atabekov Rovshan", "score": 100.0, "source": "hr.employee"}],
        "reply_thread_id": thread_id,
        "telegram_topic_id": thread_id,
        "telegram_message_id": 1,
    }

    create_task_kwargs = {}

    async def fake_create_task(**kw):
        create_task_kwargs.update(kw)
        return 600

    async def fake_log(*a, **kw):
        return None

    async def fake_notify_general(bot, text):
        pass

    async def fake_find_active_employee_by_name(name):
        return None  # botda ham ro'yxatdan o'tmagan — sof "login yo'q" holati

    monkeypatch.setattr(handlers.odoo, "create_task", fake_create_task)
    monkeypatch.setattr(handlers.repo, "save_task_log", fake_log)
    monkeypatch.setattr(handlers, "SessionLocal", _session_factory())
    monkeypatch.setattr(handlers, "_notify_general", fake_notify_general)
    monkeypatch.setattr(handlers.employee_store, "find_active_employee_by_name", fake_find_active_employee_by_name)

    bot = _FakeBot()
    callback = _FakeCallback(f"task_assign:{thread_id}:749")
    await handlers.on_task_assign(callback, bot)

    assert create_task_kwargs["user_ids"] is None
    assert any("login hisobi yo'q" in s["text"] for s in bot.sent)
    assert any("Atabekov Rovshan" in t for t in callback.message.edited)


async def test_on_task_assign_res_users_source_still_sets_user_ids(monkeypatch):
    """Regressiya: res.users manbali (odatdagi) yo'l o'zgarmasligi kerak."""
    thread_id = 88
    handlers._pending_task_selections[thread_id] = {
        "result": _analyze_result(title="Sotuv", assignee_name="Sardor"),
        "topic": types.SimpleNamespace(odoo_project_id=34, name="Sotuv"),
        "users": [{"id": 10, "name": "Sardor Aliyev", "score": 100.0, "source": "res.users"}],
        "reply_thread_id": thread_id,
        "telegram_topic_id": thread_id,
        "telegram_message_id": 1,
    }

    create_task_kwargs = {}

    async def fake_execute_kw(model, method, args, kwargs=None):
        return [{"id": 10, "name": "Sardor Aliyev"}]

    async def fake_create_task(**kw):
        create_task_kwargs.update(kw)
        return 601

    async def fake_log(*a, **kw):
        return None

    async def fake_notify_assignee_or_warn(bot, **kw):
        pass

    async def fake_notify_general(bot, text):
        pass

    monkeypatch.setattr(handlers.odoo, "execute_kw", fake_execute_kw)
    monkeypatch.setattr(handlers.odoo, "create_task", fake_create_task)
    monkeypatch.setattr(handlers.repo, "save_task_log", fake_log)
    monkeypatch.setattr(handlers, "SessionLocal", _session_factory())
    monkeypatch.setattr(handlers, "_notify_assignee_or_warn", fake_notify_assignee_or_warn)
    monkeypatch.setattr(handlers, "_notify_general", fake_notify_general)

    bot = _FakeBot()
    callback = _FakeCallback(f"task_assign:{thread_id}:10")
    await handlers.on_task_assign(callback, bot)

    assert create_task_kwargs["user_ids"] == [10]
