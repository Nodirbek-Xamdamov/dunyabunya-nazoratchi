"""'/start' ro'yxatdan o'tish — hr.employee zaxira qidiruvi uchun testlar.

Jonli Odoo'da tasdiqlangan haqiqiy xato: 180 ta faol hodimdan atigi 54 tasi
Odoo LOGIN (res.users) hisobiga ega. Ro'yxatdan o'tish avval FAQAT res.users
bo'yicha qidirilar edi — shu sababli qolgan ~126 nafar xodim ismini QANCHALIK
TO'G'RI YOZISHIDAN QAT'IY NAZAR hech qachon topilmas, /start orqali ro'yxatdan
o'ta olmas va "✅ Ro'yxatdan o'tdingiz!" xabarini hech qachon ko'rmas edi.

Bu fayl: (1) OdooClient.find_employees_by_name, (2) res.users->hr.employee
fallback zanjiri, (3) ro'yxatdan o'tishda odoo_user_id source'ga qarab to'g'ri
(yoki None) saqlanishini tekshiradi. Barcha testlar mok'langan — tarmoqqa
chiqilmaydi.
"""
from __future__ import annotations

import pytest

import app.services.odoo as odoo_module
import app.telegram.handlers_manager as handlers_manager
from app.services.odoo import OdooClient


@pytest.fixture(autouse=True)
def _clear_caches(monkeypatch):
    monkeypatch.setattr(odoo_module, "_users_cache", None)
    monkeypatch.setattr(odoo_module, "_employees_cache", None)
    yield


@pytest.fixture
def client():
    return OdooClient(url="http://test:8069", db="d", login="l", api_key="k", timeout=10)


# ── OdooClient.find_employees_by_name ────────────────────────────────────────

async def test_find_employees_by_name_matches_hr_employee_without_login(client, monkeypatch):
    """Login (res.users) hisobi bo'lmagan, faqat hr.employee'da mavjud xodim ham topiladi."""
    async def fake_execute_kw(model, method, args, kwargs=None):
        assert model == "hr.employee"
        return [{"id": 501, "name": "Otabek Qodirov"}]

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    result = await client.find_employees_by_name("Otabek")
    assert len(result) == 1
    assert result[0]["id"] == 501


async def test_find_employees_by_name_uses_same_fuzzy_threshold(client, monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        return [{"id": 1, "name": "Nodir Xamdamov"}]

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    assert await client.find_employees_by_name("Nodirga") != []
    assert await client.find_employees_by_name("Mutlaqo boshqa ism") == []


async def test_find_employees_by_name_dedupes(client, monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        return [{"id": 7, "name": "Botir Aliyev"}, {"id": 7, "name": "Botir Aliyev"}]

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    result = await client.find_employees_by_name("Botir")
    assert len(result) == 1


async def test_find_users_and_find_employees_use_separate_caches(client, monkeypatch):
    """res.users va hr.employee keshlari mustaqil — biri ikkinchisini bosib
    o'tmasligi (yoki noto'g'ri modelga so'rov yubormasligi) kerak."""
    calls = []

    async def fake_execute_kw(model, method, args, kwargs=None):
        calls.append(model)
        if model == "res.users":
            return [{"id": 1, "name": "Aziz Karimov"}]
        return [{"id": 2, "name": "Aziz Tursunov"}]

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    r1 = await client.find_users_by_name("Aziz")
    r2 = await client.find_employees_by_name("Aziz")
    assert calls == ["res.users", "hr.employee"]
    assert r1[0]["id"] == 1
    assert r2[0]["id"] == 2


# ── _find_registration_candidates: res.users -> hr.employee zanjiri ─────────

async def test_registration_prefers_res_users_when_found(monkeypatch):
    async def fake_find_users(name):
        return [{"id": 10, "name": "Sardor Aliyev", "score": 100.0}]

    async def fake_find_employees(name):
        raise AssertionError("res.users topilganda hr.employee qidirilmasligi kerak")

    monkeypatch.setattr(handlers_manager._odoo, "find_users_by_name", fake_find_users)
    monkeypatch.setattr(handlers_manager._odoo, "find_employees_by_name", fake_find_employees)

    candidates, used_fallback = await handlers_manager._find_registration_candidates("Sardor")
    assert used_fallback is False
    assert candidates[0]["source"] == "res.users"
    assert candidates[0]["id"] == 10


async def test_registration_falls_back_to_hr_employee_when_res_users_empty(monkeypatch):
    """Bu aynan xabar qilingan xatoning regressiya testi: res.users'da yo'q,
    lekin hr.employee'da bor xodim endi topilishi va fallback belgilanishi shart."""
    async def fake_find_users(name):
        return []

    async def fake_find_employees(name):
        return [{"id": 999, "name": "Karim Yusupov", "score": 90.0}]

    monkeypatch.setattr(handlers_manager._odoo, "find_users_by_name", fake_find_users)
    monkeypatch.setattr(handlers_manager._odoo, "find_employees_by_name", fake_find_employees)

    candidates, used_fallback = await handlers_manager._find_registration_candidates("Karim")
    assert used_fallback is True
    assert len(candidates) == 1
    assert candidates[0]["source"] == "hr.employee"
    assert candidates[0]["id"] == 999


async def test_registration_empty_when_neither_finds_anything(monkeypatch):
    async def fake_find_users(name):
        return []

    async def fake_find_employees(name):
        return []

    monkeypatch.setattr(handlers_manager._odoo, "find_users_by_name", fake_find_users)
    monkeypatch.setattr(handlers_manager._odoo, "find_employees_by_name", fake_find_employees)

    candidates, used_fallback = await handlers_manager._find_registration_candidates("Notanish")
    assert candidates == []
    assert used_fallback is False


# ── on_confirm: manba (source) ga qarab odoo_user_id to'g'ri saqlanishi ─────

class _FakeUser:
    def __init__(self, uid, username="tester"):
        self.id = uid
        self.username = username


class _FakeMessage:
    def __init__(self):
        self.edited = []
        self.answered = []

    async def edit_text(self, text, **kw):
        self.edited.append(text)

    async def answer(self, text, **kw):
        self.answered.append(text)

    async def edit_reply_markup(self, **kw):
        pass


class _FakeCallback:
    def __init__(self, data, uid):
        self.data = data
        self.from_user = _FakeUser(uid)
        self.message = _FakeMessage()

    async def answer(self, *a, **kw):
        pass


async def test_on_confirm_res_users_source_sets_odoo_user_id(monkeypatch):
    uid = 4001
    handlers_manager._pending_confirm[uid] = {
        "full_name": "Sardor Aliyev",
        "odoo_user": {"id": 10, "name": "Sardor Aliyev", "source": "res.users"},
    }

    captured = {}

    async def fake_get_employee_by_telegram_id(tid):
        return None

    async def fake_upsert_employee(**kw):
        captured.update(kw)

    async def fake_notify_general(bot, text):
        pass

    monkeypatch.setattr(handlers_manager.employee_store, "get_employee_by_telegram_id", fake_get_employee_by_telegram_id)
    monkeypatch.setattr(handlers_manager.employee_store, "upsert_employee", fake_upsert_employee)
    monkeypatch.setattr(handlers_manager, "_notify_general", fake_notify_general)

    callback = _FakeCallback(f"confirm:yes:{uid}", uid)
    await handlers_manager.on_confirm(callback, bot=object())

    assert captured["odoo_user_id"] == 10
    assert not any("login hisobingiz topilmadi" in t for t in callback.message.answered)


async def test_on_confirm_hr_employee_source_leaves_odoo_user_id_none_and_warns(monkeypatch):
    """Bu xato xabarining bosh sababini to'g'ridan-to'g'ri tekshiradi: hr.employee
    orqali topilgan xodim ham MUVAFFAQIYATLI ro'yxatdan o'tishi ('Ro'yxatdan
    o'tdingiz!' xabarini olishi) shart — faqat vazifa-DM cheklovi haqida
    ogohlantiriladi, ro'yxatdan o'tishning o'zi rad etilmaydi."""
    uid = 4002
    handlers_manager._pending_confirm[uid] = {
        "full_name": "Karim Yusupov",
        "odoo_user": {"id": 999, "name": "Karim Yusupov", "source": "hr.employee"},
    }

    captured = {}

    async def fake_get_employee_by_telegram_id(tid):
        return None

    async def fake_upsert_employee(**kw):
        captured.update(kw)

    async def fake_notify_general(bot, text):
        pass

    monkeypatch.setattr(handlers_manager.employee_store, "get_employee_by_telegram_id", fake_get_employee_by_telegram_id)
    monkeypatch.setattr(handlers_manager.employee_store, "upsert_employee", fake_upsert_employee)
    monkeypatch.setattr(handlers_manager, "_notify_general", fake_notify_general)

    callback = _FakeCallback(f"confirm:yes:{uid}", uid)
    await handlers_manager.on_confirm(callback, bot=object())

    assert captured["odoo_user_id"] is None
    assert captured["odoo_user_name"] == "Karim Yusupov"
    assert any("Ro'yxatdan o'tdingiz!" in t for t in callback.message.answered)
    assert any("login hisobingiz topilmadi" in t for t in callback.message.answered)
