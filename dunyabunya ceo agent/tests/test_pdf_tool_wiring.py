"""get_invoice_pdf/get_payment_receipt_pdf/get_delivery_slip_pdf/get_sale_order_pdf
regression testlari.

Ilgari bu 4 ta tool haqiqatda HECH QANDAY fayl yaratmasdan, faqat mavjud bo'lmagan
faylga ishora qiluvchi URL qaytarardi ("PDF generation requires report service —
return URL instead" izohi bilan) — CEO/menejer bu linkni bossa doim "fayl topilmadi"
bo'lardi. Endi haqiqiy PDF (`app/services/pdf_documents.py`dagi reportlab renderlari
orqali) generatsiya qilinib, REPORTS_DIR'ga saqlanadi. Barcha testlar Odoo'ga mok'langan
— tarmoqqa chiqilmaydi, reportlab esa haqiqiy fayl yozadi (vaqtinchalik papkaga).

Fayl nomlari record ID'dan TASHQARI tasodifiy 8-hex tokenga ega (P0-2: taxmin
qilib boshqa hisobotni topib olishning oldini olish uchun) — shuning uchun
testlar aniq nom o'rniga naqsh (prefix + tasodifiy suffiks + kengaytma)ni
tekshiradi.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import app.services.odoo_tools as odoo_tools


@pytest.fixture(autouse=True)
def _isolated_reports_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(odoo_tools, "REPORTS_DIR", tmp_path / "reports")
    yield


async def test_get_invoice_pdf_writes_real_file_and_returns_url(monkeypatch):
    calls = []

    async def fake_execute_kw(model, method, args, kwargs=None):
        calls.append(model)
        if model == "account.move":
            return [{"id": 1, "name": "INV/001", "invoice_date": "2026-09-01",
                      "partner_id": [5, "Mijoz"], "payment_state": "not_paid",
                      "amount_total": 1000.0, "amount_tax": 100.0, "amount_residual": 1000.0}]
        if model == "account.move.line":
            return [{"name": "Mahsulot A", "quantity": 2, "price_unit": 500.0, "price_subtotal": 1000.0}]
        raise AssertionError(f"unexpected model {model}")

    monkeypatch.setattr(odoo_tools._odoo, "execute_kw", fake_execute_kw)

    url = await odoo_tools.get_invoice_pdf(1)

    assert calls == ["account.move", "account.move.line"]
    prefix = f"{odoo_tools.settings.PUBLIC_BASE_URL}/reports/invoice_1_"
    assert url.startswith(prefix) and url.endswith(".pdf")
    assert re.fullmatch(r"[0-9a-f]{16}", url[len(prefix):-len(".pdf")])
    out_path = odoo_tools.REPORTS_DIR / url.rsplit("/", 1)[-1]
    assert out_path.exists()
    assert out_path.stat().st_size > 0


async def test_get_invoice_pdf_filename_unguessable_across_calls(monkeypatch):
    """P0-2 regressiya: bir xil invoice_id uchun ikki marta chaqirilsa ham fayl
    nomi HAR SAFAR boshqacha bo'lishi kerak (avval faqat invoice_{id}.pdf edi —
    ichki tarmoqdagi istalgan kishi ID'larni ketma-ket sanab boshqa hisob-fakturalarni
    ochib ko'ra olardi)."""
    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "account.move":
            return [{"id": 1, "name": "INV/001", "invoice_date": "2026-09-01",
                      "partner_id": [5, "Mijoz"], "payment_state": "not_paid",
                      "amount_total": 1000.0, "amount_tax": 100.0, "amount_residual": 1000.0}]
        return []

    monkeypatch.setattr(odoo_tools._odoo, "execute_kw", fake_execute_kw)
    url_1 = await odoo_tools.get_invoice_pdf(1)
    url_2 = await odoo_tools.get_invoice_pdf(1)
    assert url_1 != url_2


async def test_get_invoice_pdf_returns_none_when_not_found(monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        return []

    monkeypatch.setattr(odoo_tools._odoo, "execute_kw", fake_execute_kw)
    assert await odoo_tools.get_invoice_pdf(999) is None


async def test_get_payment_receipt_pdf_writes_real_file(monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        assert model == "account.payment"
        return [{"id": 7, "name": "PAY/007", "date": "2026-09-01",
                  "partner_id": [5, "Mijoz"], "payment_type": "inbound",
                  "amount": 500.0, "state": "posted"}]

    monkeypatch.setattr(odoo_tools._odoo, "execute_kw", fake_execute_kw)
    url = await odoo_tools.get_payment_receipt_pdf(7)

    prefix = f"{odoo_tools.settings.PUBLIC_BASE_URL}/reports/payment_7_"
    assert url.startswith(prefix) and url.endswith(".pdf")
    assert (odoo_tools.REPORTS_DIR / url.rsplit("/", 1)[-1]).exists()


async def test_get_delivery_slip_pdf_maps_quantity_field_and_writes_file(monkeypatch):
    """Bu Odoo versiyasida stock.move'da 'quantity_done' emas 'quantity' bor —
    render funksiyasi 'quantity_done' kutadi, shuning uchun wiring kodi buni
    to'g'ri moslashi kerak (regressiya: avval noto'g'ri fielddan foydalanish
    XML-RPC xatosiga olib kelishi mumkin edi)."""
    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "stock.picking":
            return [{"id": 3, "name": "WH/OUT/003", "location_id": [1, "Ombor"],
                      "location_dest_id": [2, "Mijoz"], "scheduled_date": "2026-09-01", "note": ""}]
        if model == "stock.move":
            assert "quantity_done" not in (kwargs or {}).get("fields", [])
            return [{"product_id": [10, "Mahsulot B"], "product_uom_qty": 5, "quantity": 5}]
        raise AssertionError(f"unexpected model {model}")

    monkeypatch.setattr(odoo_tools._odoo, "execute_kw", fake_execute_kw)
    url = await odoo_tools.get_delivery_slip_pdf(3)

    prefix = f"{odoo_tools.settings.PUBLIC_BASE_URL}/reports/delivery_3_"
    assert url.startswith(prefix) and url.endswith(".pdf")
    assert (odoo_tools.REPORTS_DIR / url.rsplit("/", 1)[-1]).exists()


async def test_get_sale_order_pdf_writes_real_file(monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"id": 9, "name": "S00009", "date_order": "2026-09-01",
                      "partner_id": [5, "Mijoz"], "user_id": [2, "Menejer"],
                      "amount_total": 2000.0, "note": ""}]
        if model == "sale.order.line":
            return [{"product_id": [10, "Mahsulot"], "product_uom_qty": 4,
                      "price_unit": 500.0, "price_subtotal": 2000.0}]
        raise AssertionError(f"unexpected model {model}")

    monkeypatch.setattr(odoo_tools._odoo, "execute_kw", fake_execute_kw)
    url = await odoo_tools.get_sale_order_pdf(9)

    prefix = f"{odoo_tools.settings.PUBLIC_BASE_URL}/reports/sale_order_9_"
    assert url.startswith(prefix) and url.endswith(".pdf")
    assert (odoo_tools.REPORTS_DIR / url.rsplit("/", 1)[-1]).exists()
