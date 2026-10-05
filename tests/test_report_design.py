"""Hisobotlarning ko'rinishi: HTML sahifa bloklari (app/services/report_html.py) va
Excel fayl formati (odoo_tools._write_xlsx).

Maqsad — "chiroyli" degan talabni tekshiriladigan qoidalarga aylantirish: sahifa tashqi
resurslarsiz ochiladi, grafik qiymatlari jadvalda ham bor, Odoo'dagi matn sahifani buzmaydi,
Excel'da sonlar son, sanalar sana bo'lib yoziladi.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import pytest
from openpyxl import load_workbook

import app.services.claude_report as claude_report
import app.services.odoo_tools as odoo_tools
from app.services import report_html as rh


# ── HTML bloklari ───────────────────────────────────────────────────────────

def test_page_is_self_contained_and_supports_both_themes():
    page = rh.page(title="Sotuv <hisoboti>", subtitle="2026-09", body=rh.card("Bo'lim", rh.note("izoh")))

    # Tashqi so'rov yo'q: fayl Telegram'dan yuklab olingach internetsiz ham to'liq ochiladi
    assert not re.search(r'(src|href)="https?://', page) and "@import" not in page
    assert "<h1>Sotuv &lt;hisoboti&gt;</h1>" in page
    assert "prefers-color-scheme:dark" in page and "data-theme=dark" in page
    assert "@media print" in page  # bosmada yorug' mavzu
    assert ">dunyabunya<" in page  # kompaniya nomi kichik harflar bilan


def test_column_chart_uses_round_axis_and_labels_only_peak_and_last():
    values = [16.2e9, 23.4e9, 22.3e9, 22.4e9, 22.8e9, 23.5e9, 26.7e9, 2.6e9]
    chart = rh.columns(["Mar", "Apr", "May", "Iyn", "Iyl", "Avg", "Sen", "Okt"], [("Sotuv", values)],
                       titles=[f"Oy {i}" for i in range(8)], partial_last=True)

    ticks = re.findall(r'class="cols-tick[^"]*" style="bottom:[\d.]+%"><span>([^<]+)</span>', chart)
    assert ticks == ["0", "10 mlrd", "20 mlrd", "30 mlrd"]
    # Har ustunga son yozilmaydi — faqat eng balandiga (qo'shni oxirgi ustun yozuvi bilan urilmasin)
    assert re.findall(r'class="col-val">([^<]+)<', chart) == ["26.7 mlrd"]
    assert chart.count('class="col"') == 8
    # Tugamagan oy alohida (ochroq) rangda va izohi bilan
    assert chart.count('class="seg pt"') == 1 and "hali tugamagan" in chart
    # Har ustunning aniq qiymati maslahatda (skriptsiz ham — title orqali)
    assert 'title="Oy 0 — 16.2 mlrd"' in chart


def test_stacked_columns_have_a_legend_and_both_values_in_the_tooltip():
    chart = rh.columns(["Sen", "Okt"], [("To'langan", [16.1e9, 1.3e9]), ("Qarz", [9.5e9, 1.5e9])])

    assert chart.count('<i class="sw ') == 2 and "Qarz</span>" in chart  # ikki qator — legend shart
    assert chart.count('class="seg "') == 2 and chart.count('class="seg s2"') == 2
    assert "To&#x27;langan: 16.1 mlrd; Qarz: 9.5 mlrd; Jami: 25.6 mlrd" in chart


def test_chart_with_no_data_says_so_instead_of_drawing_an_empty_grid():
    assert "Ma&#x27;lumot yo&#x27;q" in rh.columns(["Sen"], [("Sotuv", [0])])
    assert "Ma&#x27;lumot yo&#x27;q" in rh.hbars(["A"], [0])
    assert "Ma&#x27;lumot yo&#x27;q" in rh.meter(0, 0, "To'langan", "Qarz")


def test_meter_shows_share_and_both_parts():
    block = rh.meter(146.0e9, 161.6e9, "To'langan", "Qarz")
    assert '<div class="meter-pct">90.3%</div>' in block
    assert 'style="width:90.35%"' in block
    assert "<b>146 mlrd</b>" in block and "<b>15.6 mlrd</b>" in block and "(9.7%)" in block


def test_table_formats_numbers_and_keeps_the_exact_value_reachable():
    block = rh.table(["Mijoz", "Buyurtmalar", "Summa"],
                     [["A & B <savdo>", 266, 3_660_565_819.74], ["Bo'sh", 0, 0]],
                     numbered=True, bar_col=2)

    assert "A &amp; B &lt;savdo&gt;" in block
    assert '<td class="n" data-v="266">266</td>' in block
    # Ixcham yozilgan summa: to'liq qiymat ustiga borganda ko'rinadi, saralash uchun xom son
    assert '<td class="n" data-v="3660565820" title="3 660 565 819.74">3.66 mlrd</td>' in block
    assert block.count('<td class="n"></td>') == 2  # 0 qiymatlar bo'sh
    assert 'class="rb" style="width:100.0%"' in block  # reyting chizig'i nom ostida


def test_interactive_table_is_searchable_sortable_and_autonumbered():
    block = rh.table(["Xodim", "Bo'lim"], [[f"Xodim {i}", "Sotuv"] for i in range(20)],
                     numbered=True, interactive=True)

    assert 'type="search"' in block and "data-x" in block and block.count("data-s") == 2
    assert "<tbody class=auto>" in block  # raqamlash CSS orqali — qidiruv/saralashdan keyin ham 1,2,3...
    assert 'class="tbl tall"' in block  # uzun jadval o'z ichida aylanadi, sarlavhasi qotib turadi
    # Qidiruv maydoni skriptsiz ko'rinmaydi (ishlamaydigan tugma bo'lmasin)
    assert '<div class="tools" hidden>' in block


def test_generic_report_skips_the_lone_count_tile_and_names_the_file_readably(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_report, "REPORTS_DIR", tmp_path)
    rows = [{"name": f"Xodim {i}", "phone": "+99890"} for i in range(12)]
    url = claude_report.generate_report_link(title="Xodimlar ro'yxati", description="12 ta yozuv", rows=rows,
                                             columns=[("name", "Xodim nomi"), ("phone", "Telefon")])

    filename = url.rsplit("/", 1)[-1]
    assert re.fullmatch(r"xodimlar_royxati_[0-9a-f]{12}\.html", filename)
    page = (tmp_path / filename).read_text(encoding="utf-8")
    assert 'class="tile' not in page  # yozuvlar soni sarlavha ostida — alohida kartochka kerak emas
    assert 'type="search"' in page


# ── Excel ───────────────────────────────────────────────────────────────────

@pytest.fixture
def workbook(tmp_path):
    path = tmp_path / "buyurtmalar.xlsx"
    rows = [
        {"name": "S001", "partner_id": "DAVRON 9896", "date_order": "2026-10-03 08:02",
         "invoice_date": "2026-10-03", "amount_total": 1_280_000.0, "qty": 0, "note": "x" * 80},
        {"name": "S002", "partner_id": "", "date_order": "2026-10-04 16:26",
         "invoice_date": "", "amount_total": 252_500.5, "qty": 3, "note": ""},
    ]
    columns = [("name", "Raqam"), ("partner_id", "Mijoz"), ("date_order", "Buyurtma sanasi"),
               ("invoice_date", "Faktura sanasi"), ("amount_total", "Jami"), ("qty", "Soni"), ("note", "Izoh")]
    types = {"name": "char", "partner_id": "many2one", "date_order": "datetime", "invoice_date": "date",
             "amount_total": "monetary", "qty": "integer", "note": "text"}
    odoo_tools._write_xlsx(path, "Buyurtmalar: oktabr", rows, columns, types, total=76544)
    return load_workbook(path).active


def test_excel_has_title_block_styled_header_and_frozen_filterable_table(workbook):
    ws = workbook
    assert ws["A1"].value == "Buyurtmalar: oktabr" and ws["A1"].font.bold
    assert "jami 76 544 ta yozuvdan dastlabki 2 tasi" in ws["A2"].value  # sig'magan qatorlar aytiladi
    assert ws.title == "Buyurtmalar  oktabr"  # varaq nomida taqiqlangan belgilar yo'q

    header = ws[4]
    assert [c.value for c in header] == ["Raqam", "Mijoz", "Buyurtma sanasi", "Faktura sanasi", "Jami", "Soni", "Izoh"]
    assert all(c.font.bold and c.fill.fgColor.rgb.endswith("184F95") for c in header)
    # Son/sana ustunlari sarlavhasi qiymatlari kabi o'ngga tekislangan
    assert [c.alignment.horizontal for c in header] == ["left", "left", "right", "right", "right", "right", "left"]

    assert ws.freeze_panes == "B5" and ws.auto_filter.ref == "A4:G6"
    assert ws.sheet_view.showGridLines is False
    assert ws.print_title_rows == "$4:$4" and ws.page_setup.orientation == "landscape"


def test_excel_writes_real_dates_and_numbers_with_one_format_per_column(workbook):
    ws = workbook
    assert ws["C5"].value == datetime(2026, 10, 3, 8, 2) and ws["C5"].number_format == "DD.MM.YYYY HH:MM"
    assert ws["D5"].value in (date(2026, 10, 3), datetime(2026, 10, 3)) and ws["D5"].number_format == "DD.MM.YYYY"
    assert ws["D6"].value is None
    # Ustunda kasrli qiymat bor — butun ustun ikki kasr xonali (aralash format bo'lmaydi)
    assert ws["E5"].value == 1_280_000.0 and ws["E5"].number_format == "#,##0.00"
    assert ws["E6"].number_format == "#,##0.00"
    assert ws["F5"].value is None and ws["F6"].value == 3  # 0 qiymatlar bo'sh qoldiriladi
    assert ws["G5"].alignment.wrap_text is True  # uzun matn katak ichida qatorga bo'linadi


def test_excel_has_totals_row_and_striped_rows(workbook):
    ws = workbook
    assert ws["A7"].value == "Jami (fayldagi qatorlar)" and ws["A7"].font.bold
    assert ws["E7"].value == pytest.approx(1_532_500.5) and ws["E7"].number_format == "#,##0.00"
    assert ws["F7"].value is None  # butun sonli ustun (dona) summalanmaydi — faqat summa/miqdor

    rules = [(str(rng.sqref), rule.formula) for rng, rule_list in ws.conditional_formatting._cf_rules.items()
             for rule in rule_list]
    assert rules == [("A5:G6", ["MOD(ROW(),2)=0"])]  # totallar qatori yo'l-yo'l bo'yalmaydi


def test_excel_without_measures_has_no_totals_row(tmp_path):
    path = tmp_path / "xodimlar.xlsx"
    odoo_tools._write_xlsx(path, "Xodimlar", [{"name": "A"}, {"name": "B"}], [("name", "Xodim nomi")],
                           {"name": "char"}, total=2)
    ws = load_workbook(path).active
    assert ws.max_row == 6 and ws["A2"].value.startswith("2 ta yozuv")
    assert ws.freeze_panes == "A5"  # kam ustunli jadvalda birinchi ustun qotirilmaydi
