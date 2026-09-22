"""P0-1 regression: "boshqa guruhlar" (bo'lim mavzusi bo'lmagan, asosiy
TELEGRAM_GROUP_ID'dan farqli guruhlar) uchun CEO-only qoidasi (4-bo'lim).

Muhim farq boshqa handler-testlaridan: bu yerda handler funksiyasi TO'G'RIDAN-
TO'G'RI chaqirilmaydi — buning o'rniga HAQIQIY aiogram Dispatcher (app/main.py
bilan bir xil router tartibida: manager_router -> delivery_router ->
group_router) orqali haqiqiy Update/Message obyektlari yuboriladi. Bu, faqat
`on_other_group` funksiyasining ICHKI mantig'ini emas, balki HAQIQATDA shu
xabar uchun chaqiriladigan handler aynan shu ekanini ham tasdiqlaydi — ya'ni
boshqa (kengroq filtrli) handler uni "o'g'irlab" ketmasligini.

Real tarmoqqa/Telegram API'ga chiqilmaydi — Bot session to'liq mock qilingan.
"""
from __future__ import annotations

import time

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import GetMe
from aiogram.types import Chat, Message, Update, User

import app.main as main_module
import app.telegram.handlers as handlers
from app.services.llm import AnalyzeResult

OTHER_GROUP_CHAT_ID = -777888999  # TELEGRAM_GROUP_ID'dan (conftest: -100123456) FARQLI


class _RecordingSession(BaseSession):
    """Bot session'ni to'liq mock qiladi — haqiqiy HTTP so'rov yubormaydi,
    har bir chaqirilgan metodni (nomi + parametrlari) yozib boradi."""

    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, dict]] = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append((method.__class__.__name__, method.model_dump(exclude_none=True)))
        if isinstance(method, GetMe):
            return User(id=999999, is_bot=True, first_name="TestBot", username="test_bot")
        return True

    async def stream_content(self, *a, **kw):
        yield b""


# Routerlar modul darajasidagi singleton — bitta Dispatcher'ga faqat BIR
# MARTA biriktirilishi mumkin (aiogram cheklovi). app/main.py import qilinganda
# o'zining dp'sini allaqachon quradi (manager -> delivery -> group tartibida) —
# alohida Dispatcher yasash o'rniga xuddi SHU (production bilan bir xil)
# Dispatcher'dan foydalanamiz.
def _build_dispatcher():
    return main_module.dp


def _make_message(*, user_id: int, username: str, text: str) -> Update:
    user = User(id=user_id, is_bot=False, first_name="Test", username=username)
    chat = Chat(id=OTHER_GROUP_CHAT_ID, type="supergroup", title="Boshqa yordamchi guruh")
    msg = Message(message_id=1, date=int(time.time()), chat=chat, from_user=user, text=text)
    return Update(update_id=1, message=msg)


@pytest.fixture
def ceo_settings(monkeypatch):
    monkeypatch.setattr(handlers.settings, "CEO_TELEGRAM_USER_ID", 0)
    monkeypatch.setattr(handlers.settings, "CEO_TELEGRAM_USERNAME", "real_ceo")


def _analyze_result(**overrides) -> AnalyzeResult:
    base = dict(is_task=True, is_task_confidence=0.5, department_scores={})
    base.update(overrides)
    return AnalyzeResult(**base)


async def test_a_non_ceo_message_produces_zero_bot_calls(ceo_settings, monkeypatch):
    """(a) CEOdan boshqa a'zo yozsa — bot HECH QANDAY reaktsiya bermasin va
    analyze_message hatto chaqirilmasin (tezkor chiqish, keraksiz LLM chaqiruvi yo'q)."""
    called = {"analyze": False}

    async def _fail_analyze(text):
        called["analyze"] = True
        raise AssertionError("analyze_message chaqirilmasligi kerak edi — CEO filtri ishlamayapti")

    monkeypatch.setattr(handlers, "analyze_message", _fail_analyze)

    session = _RecordingSession()
    bot = Bot(token="123456:test-fake-token-not-real", session=session)
    dp = _build_dispatcher()

    update = _make_message(user_id=111222, username="oddiy_xodim", text="Omborni ertaga tekshiramiz, bu muhim")
    await dp.feed_update(bot, update)

    assert called["analyze"] is False
    assert session.calls == []


async def test_b_ceo_high_confidence_task_posts_note_not_odoo(ceo_settings, monkeypatch):
    """(b) CEO yuqori ishonchli (>=80%) vazifa matni yozsa — Odoo'ga YOZILMAYDI,
    General mavzuga "Manba: ..." eslatmasi tushadi, manba guruhga tasdiq qaytadi."""
    async def fake_analyze(text):
        return _analyze_result(is_task=True, is_task_confidence=0.9, department_scores={"Ombor": 0.85})

    odoo_create_called = {"v": False}

    async def fake_create_task(**kw):
        odoo_create_called["v"] = True
        return 999

    monkeypatch.setattr(handlers, "analyze_message", fake_analyze)
    monkeypatch.setattr(handlers.odoo, "create_task", fake_create_task)

    session = _RecordingSession()
    bot = Bot(token="123456:test-fake-token-not-real", session=session)
    dp = _build_dispatcher()

    update = _make_message(user_id=555, username="real_ceo", text="Murod omborni tekshirsin")
    await dp.feed_update(bot, update)

    assert odoo_create_called["v"] is False  # Odoo'ga hech qachon yozilmagan

    send_calls = [(name, params) for name, params in session.calls if name == "SendMessage"]
    # Bittasi General guruhga eslatma, ikkinchisi manba guruhga (reply) tasdiq
    assert len(send_calls) == 2
    general_note = next(p for _, p in send_calls if p.get("chat_id") == handlers.settings.TELEGRAM_GROUP_ID)
    assert "Manba" in general_note["text"]
    assert "Boshqa yordamchi guruh" in general_note["text"]

    confirm = next(p for _, p in send_calls if p.get("chat_id") == OTHER_GROUP_CHAT_ID)
    assert "Odoo" in confirm["text"] and "yozilmadi" in confirm["text"]


async def test_c_ceo_low_confidence_sends_nothing(ceo_settings, monkeypatch):
    """(c) CEO past ishonchli/oddiy xabar yozsa (masalan <80%) — hech qaerga
    hech narsa yuborilmasin (na Odoo, na General eslatma, na tasdiq)."""
    async def fake_analyze(text):
        return _analyze_result(is_task=True, is_task_confidence=0.5, department_scores={"Ombor": 0.4})

    monkeypatch.setattr(handlers, "analyze_message", fake_analyze)

    session = _RecordingSession()
    bot = Bot(token="123456:test-fake-token-not-real", session=session)
    dp = _build_dispatcher()

    update = _make_message(user_id=555, username="real_ceo", text="Balki omborni ko'rib chiqarmiz")
    await dp.feed_update(bot, update)

    assert session.calls == []
