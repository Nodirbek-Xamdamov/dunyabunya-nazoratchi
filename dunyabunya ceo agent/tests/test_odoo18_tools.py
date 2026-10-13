"""Odoo 18 ga moslik va hisobot raqamlari to'g'riligi uchun regressiya testlari.

2026-10-05 da jonli Odoo (18.0) bilan solishtirganda topilgan xatolar:
- read_group guruh soni "id:count" kalitida EMAS, `<maydon>_count`/`__count`da keladi —
  kompaniya hisobotida "Buyurtmalar: 0", "CRM lidlar: 0" chiqardi;
- account.payment'da 'posted' holati yo'q (in_process/paid) — to'lovlar doim bo'sh qaytardi;
- product.product'da type='product' yo'q (is_storable) — mahsulot qidiruvi doim bo'sh edi;
- bo'sh maydon (False) son deb olinib, HTML hisobotda "mobile_phone: 0" kartochkasi chiqardi;
- 500/1000 qator bilan cheklangan qo'lda jamlash noto'g'ri yig'indi berardi.

Barcha testlar mok'langan Odoo bilan ishlaydi — tarmoqqa chiqilmaydi.
"""
from __future__ import annotations

import re

import pytest

import app.services.claude_report as claude_report
import app.services.odoo_tools as odoo_tools
from app.services import report_html as rh


@pytest.fixture(autouse=True)
def _isolated_reports_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(odoo_tools, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(claude_report, "REPORTS_DIR", tmp_path / "reports")
    yield


def _fake_odoo(monkeypatch, handler):
    calls = []

    async def fake_execute_kw(model, method, args, kwargs=None):
        calls.append((model, method, args, kwargs or {}))
        return handler(model, method, args, kwargs or {})

    monkeypatch.setattr(odoo_tools._odoo, "execute_kw", fake_execute_kw)
    return calls


def _report_text(result) -> str:
    url = result["url"] if isinstance(result, dict) else result
    return (odoo_tools.REPORTS_DIR / url.rsplit("/", 1)[-1]).read_text(encoding="utf-8")


# ── read_group natijasini o'qish ────────────────────────────────────────────

def test_group_count_reads_odoo_count_keys():
    assert odoo_tools._group_count({"stage_id_count": 7, "id": 7}, "stage_id") == 7
    assert odoo_tools._group_count({"__count": 12}, "partner_id") == 12
    assert odoo_tools._group_count({"date_order_count": 8269}, "date_order:month") == 8269
    assert odoo_tools._group_count({}, "state") == 0


def test_month_label_is_uzbek_and_tolerates_timezone_shifted_range():
    row = {"date_order:month": "March 2026",
           "__range": {"date_order:month": {"from": "2026-02-28 19:00:00", "to": "2026-03-31 19:00:00"}}}
    assert odoo_tools._month_label(row, "date_order:month") == "Mart 2026"
    assert odoo_tools._month_label({"invoice_date:month": False}, "invoice_date:month") == "Sanasi yo'q"


def test_datetime_bounds_cover_the_whole_last_day_in_tashkent_time():
    """`date_order <= '2026-10-05'` 5-oktabr 00:00 (UTC) gacha degani edi — oxirgi kun tushib qolardi."""
    bounds = odoo_tools._dt_bounds("date_order", "2026-10-01", "2026-10-05")
    assert bounds == [["date_order", ">=", "2026-09-30 19:00:00"],
                      ["date_order", "<", "2026-10-05 19:00:00"]]


# ── Odoo 18 holat/tur qiymatlari ────────────────────────────────────────────

async def test_search_payments_default_uses_odoo18_states(monkeypatch):
    calls = _fake_odoo(monkeypatch, lambda *a: [])
    await odoo_tools.search_payments()
    domain = calls[0][2][0]
    assert domain == [["state", "in", ["in_process", "paid", "posted"]]]


async def test_low_stock_uses_is_storable_not_removed_product_type(monkeypatch):
    def handler(model, method, args, kwargs):
        if method == "fields_get":
            return {"is_storable": {"type": "boolean"}, "type": {"type": "selection"}}
        if method == "search_count":
            return 5
        return [{"id": 1, "name": "Sement", "qty_available": 3.0}]

    calls = _fake_odoo(monkeypatch, handler)
    result = await odoo_tools.check_low_stock(10)

    domains = [c[2][0] for c in calls if c[1] in ("search_read", "search_count")]
    assert all(["is_storable", "=", True] in d for d in domains)
    assert not any(["type", "=", "product"] in d for d in domains)
    assert result["kam_qolgan_soni"] == 5 and result["tugagan_soni"] == 5
    # Sukut bo'yicha tugagan (0) mahsulotlar "kam qolgan" ro'yxatiga qo'shilmaydi
    assert ["qty_available", ">", 0] in domains[0]


async def test_find_product_has_no_type_filter_and_searches_code(monkeypatch):
    calls = _fake_odoo(monkeypatch, lambda *a: [])
    await odoo_tools.find_product("0012-46245")
    domain = calls[0][2][0]
    assert ["default_code", "ilike", "0012-46245"] in domain
    assert not any(isinstance(leaf, list) and leaf[0] == "type" for leaf in domain)


async def test_invoice_pdf_reads_product_lines(monkeypatch):
    def handler(model, method, args, kwargs):
        if model == "account.move":
            return [{"id": 1, "name": "INV/1", "currency_id": [1, "USD"], "amount_total": 10}]
        return []

    calls = _fake_odoo(monkeypatch, handler)
    await odoo_tools.get_invoice_pdf(1)
    line_domain = calls[1][2][0]
    assert ["display_type", "in", ["product", False]] in line_domain


async def test_pdf_tools_no_longer_hide_odoo_errors(monkeypatch):
    """Ilgari har qanday xato `None`ga aylanib, model "faktura topilmadi" deb javob berardi —
    aslida Odoo bilan aloqa yo'q bo'lsa ham."""
    from app.services.odoo import OdooError

    def handler(*a):
        raise OdooError("Odoo manzili topilmadi", kind="dns")

    _fake_odoo(monkeypatch, handler)
    with pytest.raises(OdooError):
        await odoo_tools.get_invoice_pdf(1)


async def test_create_crm_lead_writes_expected_revenue(monkeypatch):
    calls = _fake_odoo(monkeypatch, lambda *a: 42)
    assert await odoo_tools.create_crm_lead("Yangi mijoz", planned_revenue=5_000_000) == 42
    vals = calls[0][2][0]
    assert vals == {"name": "Yangi mijoz", "expected_revenue": 5_000_000}


async def test_upcoming_deadlines_excludes_done_and_cancelled_tasks(monkeypatch):
    calls = _fake_odoo(monkeypatch, lambda *a: [])
    await odoo_tools.get_upcoming_deadlines(7)
    assert ["state", "not in", ["1_done", "1_canceled"]] in calls[0][2][0]


# ── Noto'g'ri hujjat yaratuvchi tool'lar o'chirilgan ────────────────────────

async def test_kassa_chiqim_and_exchange_creation_write_nothing(monkeypatch):
    calls = _fake_odoo(monkeypatch, lambda *a: 1)
    kassa = await odoo_tools.create_kassa_chiqim(partner_id=5, amount=1000, description="test")
    exchange = await odoo_tools.create_currency_exchange(1, 2, 100, 12800)
    assert "error" in kassa and "error" in exchange
    assert calls == []  # ilgari mijozga hisob-faktura qoralamasi yaratilardi


async def test_search_kassa_chiqim_reads_the_real_document(monkeypatch):
    def handler(model, method, args, kwargs):
        assert model == "x_kassa_chiqim"
        if method == "search_read":
            return [{"id": 9, "x_name": False, "x_studio_amount": 19_000_000.0,
                     "x_studio_currency_id": [127, "UZS"], "x_studio_chiqim": [103, "Naqd"],
                     "x_studio_kirim": [46, "Tranzit"], odoo_tools._KASSA_STATUS_FIELD: "status2",
                     "x_studio_note_selection": "Inkassatsiya", "create_date": "2026-10-04 08:30:47",
                     "create_uid": [213, "Kassir"]}]
        if method == "search_count":
            return 43
        return [{"x_studio_currency_id": [127, "UZS"], "x_studio_amount": 417_342_000.0,
                 "x_studio_currency_id_count": 43}]

    calls = _fake_odoo(monkeypatch, handler)
    result = await odoo_tools.search_kassa_chiqim(status="done", date_from="2026-10-01")

    assert result["jami_soni"] == 43
    assert result["jami_summa"] == [{"valyuta": "UZS", "summa": 417_342_000.0, "soni": 43}]
    row = result["yozuvlar"][0]
    assert row["holat"] == "Bajarilgan" and row["sana"] == "2026-10-04 13:30"  # Toshkent vaqti
    assert [odoo_tools._KASSA_STATUS_FIELD, "=", "status2"] in calls[0][2][0]


# ── Jamlash: valyuta bo'yicha, Odoo'ning o'zida ─────────────────────────────

async def test_profit_summary_aggregates_in_odoo_and_splits_currencies(monkeypatch):
    def handler(model, method, args, kwargs):
        assert method == "read_group"  # xom qatorlar (limit 500/1000) tortilmaydi
        if model == "account.move":
            return [
                {"currency_id": [127, "UZS"], "move_type": "out_invoice", "__count": 74915,
                 "amount_total": 161_000_000_000.0, "amount_residual": 15_000_000_000.0},
                {"currency_id": [127, "UZS"], "move_type": "out_refund", "__count": 300,
                 "amount_total": 4_000_000_000.0, "amount_residual": 0.0},
            ]
        return [
            {"currency_id": [127, "UZS"], "payment_type": "inbound", "__count": 60574, "amount": 139_000_000_000.0},
            {"currency_id": [1, "USD"], "payment_type": "inbound", "__count": 201, "amount": 317_034.36},
            {"currency_id": [1, "USD"], "payment_type": "outbound", "__count": 339, "amount": 1_580_695.02},
        ]

    calls = _fake_odoo(monkeypatch, handler)
    result = await odoo_tools.get_profit_summary("2026-01-01", "2026-10-05")
    by_currency = {v["valyuta"]: v for v in result["valyutalar"]}

    assert by_currency["UZS"]["jami_faktura"] == 161_000_000_000.0
    assert by_currency["UZS"]["qaytarilgan"] == 4_000_000_000.0
    assert by_currency["UZS"]["fakturalar_soni"] == 74915
    assert by_currency["UZS"]["mijozlardan_tushgan"] == 139_000_000_000.0
    assert by_currency["USD"]["mijozlardan_tushgan"] == 317_034.36  # so'mga qo'shilmagan
    assert by_currency["USD"]["chiqim_tolovlar"] == 1_580_695.02
    payment_domain = next(c[2][0] for c in calls if c[0] == "account.payment")
    assert ["state", "in", ["in_process", "paid", "posted"]] in payment_domain


async def test_top_debtors_groups_by_customer_and_sorts_by_debt(monkeypatch):
    def handler(model, method, args, kwargs):
        assert method == "read_group" and args[2] == ["partner_id", "currency_id"]
        assert kwargs["orderby"] == "amount_residual desc" and kwargs["lazy"] is False
        return [{"partner_id": [63013, "DAVRON"], "currency_id": [127, "UZS"],
                 "amount_residual": 2_602_529_811.74, "__count": 168}]

    _fake_odoo(monkeypatch, handler)
    assert await odoo_tools.get_top_debtors(5) == [
        {"mijoz": "DAVRON", "partner_id": 63013, "qarz": 2_602_529_811.74, "valyuta": "UZS",
         "tolanmagan_fakturalar": 168},
    ]


async def test_cashflow_splits_income_and_expense_per_month_and_currency(monkeypatch):
    rng = {"date:month": {"from": "2026-09-01", "to": "2026-10-01"}}

    def handler(model, method, args, kwargs):
        return [
            {"date:month": "sentabr 2026", "__range": rng, "payment_type": "inbound",
             "currency_id": [127, "UZS"], "amount": 20.0, "__count": 2},
            {"date:month": "sentabr 2026", "__range": rng, "payment_type": "outbound",
             "currency_id": [127, "UZS"], "amount": 12.0, "__count": 1},
            {"date:month": "sentabr 2026", "__range": rng, "payment_type": "outbound",
             "currency_id": [1, "USD"], "amount": 5.0, "__count": 1},
        ]

    _fake_odoo(monkeypatch, handler)
    assert await odoo_tools.get_cashflow_summary() == [
        {"davr": "Sentabr 2026", "valyuta": "USD", "kirim": 0.0, "chiqim": 5.0, "farq": -5.0},
        {"davr": "Sentabr 2026", "valyuta": "UZS", "kirim": 20.0, "chiqim": 12.0, "farq": 8.0},
    ]


async def test_sales_by_product_counts_only_confirmed_orders(monkeypatch):
    calls = _fake_odoo(monkeypatch, lambda *a: [])
    await odoo_tools.get_sales_by_product("2026-09-01", "2026-09-30")
    domain, kwargs = calls[0][2][0], calls[0][3]
    assert ["state", "in", ["sale", "done"]] in domain
    assert kwargs["orderby"] == "price_subtotal desc"  # "top" — eng yiriklari, tartibsiz birinchilari emas


# ── Kompaniya hisoboti ──────────────────────────────────────────────────────

async def test_company_report_shows_real_order_and_lead_counts(monkeypatch):
    month = {"from": "2026-09-01 00:00:00", "to": "2026-10-01 00:00:00"}

    def handler(model, method, args, kwargs):
        if method == "search_count":
            return 187
        if method == "fields_get":
            return {"state": {"type": "selection", "selection": [["01_in_progress", "Ish jarayonida"],
                                                                 ["1_done", "Bajarildi"]]}}
        groupby = args[2][0]
        if model == "sale.order" and groupby == "date_order:month":
            return [{"date_order:month": "sentabr 2026", "__range": {groupby: month},
                     "date_order_count": 11348, "amount_total": 26_674_071_239.71}]
        if model == "sale.order":
            return [{"currency_id": [127, "UZS"], "currency_id_count": 11348, "amount_total": 26_674_071_239.71}]
        if model == "account.move":
            return [{"invoice_date:month": "sentabr 2026", "__range": {groupby: {"from": "2026-09-01"}},
                     "invoice_date_count": 13058, "amount_total": 25_626_328_305.0,
                     "amount_residual": 9_554_378_103.0}]
        if model == "project.task":
            return [{"state": "01_in_progress", "state_count": 17}, {"state": "1_done", "state_count": 1}]
        if model == "crm.lead":
            return [{"stage_id": [17, "Yangi <b>"], "stage_id_count": 2}]
        raise AssertionError(f"unexpected {model}.{method}")

    _fake_odoo(monkeypatch, handler)
    url = await odoo_tools.generate_company_report("2026-09-01", "2026-09-30")
    page = _report_text(url)

    assert "11 348 ta buyurtma" in page  # bosh kartochka izohida (ilgari "Buyurtmalar: 0" edi)
    assert "jami 2 ta lid" in page  # ilgari "CRM lidlar: 0" edi
    assert "Sentabr 2026" in page and "Ish jarayonida" in page
    assert "Summalar valyutasi: UZS" in page
    # Odoo'dagi nomlar HTML'ni buzmaydi
    assert "Yangi &lt;b&gt;" in page and "Yangi <b>" not in re.sub(r"<script.*?</script>", "", page, flags=re.S)


# ── Umumiy jadval-hisobot (generate_html_report) ────────────────────────────

_EMPLOYEE_META = {
    "name": {"string": "Xodim nomi", "type": "char"},
    "mobile_phone": {"string": "Ish Mobil", "type": "char"},
    "active": {"string": "Faol", "type": "boolean"},
    "department_id": {"string": "Bo'lim", "type": "many2one", "relation": "hr.department"},
}


async def test_html_report_treats_empty_fields_as_blank_not_numbers(monkeypatch):
    """2026-10-05: birinchi xodimning telefoni bo'sh (False) bo'lgani uchun telefon ustuni
    "son" deb topilib, hisobotda "mobile_phone 0" kartochkasi va telefonli grafik chiqqan edi."""
    def handler(model, method, args, kwargs):
        if method == "fields_get":
            return _EMPLOYEE_META
        if method == "search_count":
            return 2
        return [
            {"id": 1, "name": "ABASOV <P>", "mobile_phone": False, "active": True, "department_id": [28, "Xavfsizlik"]},
            {"id": 2, "name": "KADIROV S", "mobile_phone": "+998931097303", "active": True, "department_id": False},
        ]

    _fake_odoo(monkeypatch, handler)
    url = await odoo_tools.generate_html_report(
        "hr.employee", [], ["name", "mobile_phone", "active", "department_id"], "Xodimlar")
    page = _report_text(url)

    # Sarlavhalar — Odoo'dagi maydon nomi, texnik nom emas
    assert "<th>Xodim nomi</th><th>Ish Mobil</th><th>Faol</th><th>Bo&#x27;lim</th>" in page
    assert "mobile_phone" not in page
    cells = re.sub(r' (?:class|data-l)="[^"]*"', "", page)  # katak klasslari va telefon uchun yorliqlar ahamiyatsiz
    assert "<td>ABASOV &lt;P&gt;</td><td></td><td>Ha</td><td>Xavfsizlik</td>" in cells
    # Telefon — bosiladigan havola, o'qiladigan ko'rinishda
    assert '<a class="lnk" href="tel:+998931097303">+998 93 109 73 03</a>' in page
    # Faqat jadval: telefon bo'yicha "yig'indi" kartochkasi ham, grafik ham yo'q
    assert page.count('class="card"') == 1 and 'class="tile' not in page
    assert 'class="hbars"' not in page and 'class="cols"' not in page


async def test_html_report_says_when_rows_do_not_fit_and_uses_real_totals(monkeypatch):
    def handler(model, method, args, kwargs):
        if method == "fields_get":
            return {"name": {"string": "Raqam", "type": "char"},
                    "amount_total": {"string": "Jami", "type": "monetary"}}
        if method == "search_count":
            return 76544
        if method == "read_group":
            return [{"amount_total": 159_900_000_000.0, "__count": 76544}]
        return [{"id": i, "name": f"S{i}", "amount_total": 1000.0} for i in range(kwargs["limit"])]

    _fake_odoo(monkeypatch, handler)
    result = await odoo_tools.generate_html_report("sale.order", [], ["name", "amount_total"], "Buyurtmalar")

    assert result["rows_in_file"] == 1000 and result["rows_total"] == 76544
    page = _report_text(result["url"])
    assert "jami 76 544 ta yozuvdan dastlabki 1 000 tasi" in page
    # "Jami" kartochkasi butun tanlov bo'yicha (159.9 mlrd), ko'ringan 1000 qator yig'indisi (1 mln) emas
    assert '<div class="lbl">Jami</div><div class="val">159.9 mlrd</div>' in page


async def test_many2many_ids_are_shown_as_names(monkeypatch):
    def handler(model, method, args, kwargs):
        if method == "fields_get":
            return {"name": {"string": "Sarlavha", "type": "char"},
                    "user_ids": {"string": "Mas'ullar", "type": "many2many", "relation": "res.users"}}
        if model == "res.users" and method == "read":
            assert args[0] == [2, 87]
            return [{"id": 2, "display_name": "Sardor"}, {"id": 87, "display_name": "Abdurahmon"}]
        return [{"id": 1, "name": "Hisobot", "user_ids": [87, 2]}, {"id": 2, "name": "Reja", "user_ids": []}]

    _fake_odoo(monkeypatch, handler)
    rows, columns, _meta = await odoo_tools._read_rows("project.task", [], ["name", "user_ids"])
    assert rows == [{"name": "Hisobot", "user_ids": "Abdurahmon, Sardor"}, {"name": "Reja", "user_ids": ""}]
    assert columns == [("name", "Sarlavha"), ("user_ids", "Mas'ullar")]


async def test_export_reports_rows_that_did_not_fit(monkeypatch):
    def handler(model, method, args, kwargs):
        if method == "fields_get":
            return {"name": {"string": "Nomi", "type": "char"}}
        if method == "search_count":
            return 11517
        return [{"id": i, "name": f"Mijoz {i}"} for i in range(kwargs["limit"])]

    _fake_odoo(monkeypatch, handler)
    result = await odoo_tools.export_csv("res.partner", [], ["name"], "Mijozlar", limit=500)

    assert result["rows_in_file"] == 500 and result["rows_total"] == 11517
    text = (odoo_tools.REPORTS_DIR / result["url"].rsplit("/", 1)[-1]).read_text(encoding="utf-8-sig")
    assert text.splitlines()[0] == "Nomi" and len(text.splitlines()) == 501


async def test_large_export_is_read_in_stable_pages(monkeypatch):
    seen = []

    def handler(model, method, args, kwargs):
        if method == "fields_get":
            return {"name": {"string": "Nomi", "type": "char"}}
        seen.append((kwargs.get("offset"), kwargs.get("limit"), kwargs.get("order")))
        start = kwargs.get("offset") or 0
        return [{"id": i, "name": str(i)} for i in range(start, min(start + kwargs["limit"], 4500))]

    _fake_odoo(monkeypatch, handler)
    rows, _columns, _meta = await odoo_tools._read_rows("sale.order", [], ["name"], limit=10000)

    assert len(rows) == 4500 and [r["name"] for r in rows[:2]] == ["0", "1"]
    # Birinchi so'rov — modelning o'z tartibida (ro'yxat bitta sahifaga sig'sa shu bilan tugaydi);
    # sig'magach, ID bo'yicha barqaror tartibda boshidan sahifalab o'qiladi
    assert seen == [(None, 2000, None), (0, 2000, "id"), (2000, 2000, "id"), (4000, 2000, "id")]


# ── Raqam formati ───────────────────────────────────────────────────────────

def test_number_format_keeps_counts_exact_and_compacts_large_sums():
    assert rh.fmt_num(8269) == "8 269"
    assert rh.fmt_num(850_000) == "850 000"
    assert rh.fmt_num(159_918_027_388.05) == "159.9 mlrd"
    assert rh.fmt_num(20_000_000) == "20 mln"
    assert rh.fmt_num(1_532_000, 2) == "1.53 mln"
    assert rh.fmt_num(-252_040) == "-252 040"
    assert rh.fmt_num(False) == "0"
    assert not rh.is_number(False) and rh.is_number(0)
