"""Statistika funksiyasini sinaydi: PDF+grafik generatsiyasi (app.bot.statistics_pdf)
va "Statistika" menyu tugmasi handleri (app.bot.handlers.menu). Haqiqiy Odoo/Telegram
so'rovi yubormaydi -- FastAPI javobi monkeypatch orqali soxtalashtiriladi."""

from unittest.mock import AsyncMock, MagicMock

from app.bot.handlers import menu as menu_module
from app.bot.statistics_pdf import _build_chart_image, _fmt, build_statistics_pdf

SAMPLE_MONTHS = [
    {"month": "2026-07", "spent": 1_500_000.0, "returned": 0.0, "paid": 1_000_000.0},
    {"month": "2026-08", "spent": 0.0, "returned": -250_000.0, "paid": 0.0},
]


def test_fmt_returns_blank_for_zero():
    assert _fmt(0.0) == ""
    assert _fmt(0) == ""


def test_fmt_uses_space_as_thousands_separator():
    """2026-08-04, mijoz aniq so'ragan: vergul o'rniga bo'shliq."""
    assert _fmt(22_410_500.0) == "22 410 500"
    assert _fmt(-160_500.0) == "-160 500"


def test_fmt_omits_zero_cents():
    """5 000 000.00 kabi butun summalar uchun ortiqcha ".00" yozilmasligi kerak."""
    assert _fmt(5_000_000.00) == "5 000 000"


def test_fmt_keeps_nonzero_cents():
    """Odoo'dan 5000000.13 kabi butun bo'lmagan summa kelsa, kasr qismi saqlanishi
    kerak -- 2026-08-04, mijoz aniq so'ragan misol bilan so'zma-so'z tekshiriladi."""
    assert _fmt(5_000_000.13) == "5 000 000.13"


def test_build_statistics_pdf_returns_valid_pdf_bytes_uz():
    pdf_bytes = build_statistics_pdf(SAMPLE_MONTHS, "Test Partner", "uz")
    assert pdf_bytes.startswith(b"%PDF")


def test_build_statistics_pdf_returns_valid_pdf_bytes_ru():
    """Rus tilidagi (kirill) matn bilan ham xatosiz PDF yaratilishi kerak -- fpdf2'ning
    o'rnatilgan asosiy shriftlari kirillni qo'llamaydi, shu sabab DejaVu shrifti
    ishlatiladi (statistics_pdf.py'dagi izohga qarang)."""
    pdf_bytes = build_statistics_pdf(SAMPLE_MONTHS, "Тест Партнёр", "ru")
    assert pdf_bytes.startswith(b"%PDF")


def test_build_chart_image_returns_valid_png_bytes():
    png_bytes = _build_chart_image(SAMPLE_MONTHS, "uz", "2026")
    assert png_bytes.startswith(b"\x89PNG")


def test_build_chart_image_handles_negative_returned_values_without_error():
    """`returned` odoo_client.py'da manfiy saqlanadi (jadval uchun) -- grafik
    chizishda esa mutlaq qiymatga o'tkazilishi (`abs()`) kerak, aks holda manfiy
    chiziq Y o'qi ostiga tushib ketishi yoki `set_ylim` hisob-kitobi buzilishi
    mumkin. Bu yerda faqat chizish xatosiz ishlashini tekshiramiz -- vizual
    joylashuv PDF orqali ko'rib chiqilgan (ushbu turdagi o'zgarishlarda haqiqiy
    PDF yaratib, Read vositasi orqali ko'rib chiqish tavsiya etiladi)."""
    months_with_return = [{"month": "2026-08", "spent": 100.0, "returned": -9_999_999.0, "paid": 0.0}]
    png_bytes = _build_chart_image(months_with_return, "uz", "2026")
    assert png_bytes.startswith(b"\x89PNG")


def test_build_statistics_pdf_skips_all_zero_months_from_table():
    """Buyurtma VA qaytarilgan ikkalasi ham 0 bo'lgan oy (masalan hali kelmagan
    kelajakdagi oy) jadval qatoridan olib tashlanishi kerak -- shu bilan birga
    jami qatoriga ta'sir qilmasligi kerak (0 qo'shish natijani o'zgartirmaydi).
    Bu yerda faqat xatosiz ishlashini tekshiramiz, vizual joylashuv PDF orqali
    ko'rib chiqilgan."""
    months = SAMPLE_MONTHS + [
        {"month": "2026-12", "spent": 0.0, "returned": 0.0, "paid": 0.0}
    ]
    pdf_bytes = build_statistics_pdf(months, "Test Partner", "uz")
    assert pdf_bytes.startswith(b"%PDF")


def _make_message(text: str = "📊 Statistika", chat_id: int = 1, username: str | None = "testuser"):
    message = MagicMock()
    message.text = text
    message.chat.id = chat_id
    message.from_user.username = username
    message.answer = AsyncMock()
    message.answer_document = AsyncMock()
    return message


def _make_state():
    state = MagicMock()
    state.clear = AsyncMock()
    return state


async def test_statistics_button_not_found_shows_no_partner_message(monkeypatch):
    monkeypatch.setattr(
        menu_module, "fetch_statistics_data", AsyncMock(return_value={"found": False, "months": None})
    )

    message = _make_message()
    await menu_module.handle_menu_section_button(message, _make_state())

    message.answer.assert_called_once()
    message.answer_document.assert_not_called()


async def test_statistics_button_no_months_shows_generic_error(monkeypatch):
    monkeypatch.setattr(
        menu_module, "fetch_statistics_data", AsyncMock(return_value={"found": True, "months": None})
    )

    message = _make_message()
    await menu_module.handle_menu_section_button(message, _make_state())

    message.answer.assert_called_once()
    message.answer_document.assert_not_called()


async def test_statistics_button_sends_pdf_document(monkeypatch):
    monkeypatch.setattr(
        menu_module,
        "fetch_statistics_data",
        AsyncMock(
            return_value={
                "found": True,
                "partner_name": "Test Partner",
                "months": SAMPLE_MONTHS,
                "by_object": [{"object_id": 5, "object_name": "Olmazor", "spent": 1_000_000.0}],
                "current_balance": -20_000_000.0,
            }
        ),
    )
    captured = {}

    def fake_build_pdf(months, partner_name, lang):
        captured["months"] = months
        captured["partner_name"] = partner_name
        captured["lang"] = lang
        return b"%PDF-fake"

    monkeypatch.setattr(menu_module, "build_statistics_pdf", fake_build_pdf)

    message = _make_message()
    await menu_module.handle_menu_section_button(message, _make_state())

    message.answer_document.assert_called_once()
    args, kwargs = message.answer_document.call_args
    assert kwargs == {}
    assert args[0].filename == "Buyurtmalar statistikasi.pdf"
    message.answer.assert_not_called()
    assert captured["months"] == SAMPLE_MONTHS
    assert captured["partner_name"] == "Test Partner"


async def test_statistics_button_shows_generic_error_on_pdf_build_failure(monkeypatch):
    monkeypatch.setattr(
        menu_module,
        "fetch_statistics_data",
        AsyncMock(return_value={"found": True, "partner_name": "Test Partner", "months": SAMPLE_MONTHS}),
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(menu_module, "build_statistics_pdf", _boom)

    message = _make_message()
    await menu_module.handle_menu_section_button(message, _make_state())

    message.answer.assert_called_once()
    message.answer_document.assert_not_called()
