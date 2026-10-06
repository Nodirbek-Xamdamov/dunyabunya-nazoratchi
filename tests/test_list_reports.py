"""Ro'yxat hisobotlari: guruhlash (bo'limlar bo'yicha), saralash, kompaniya filtri va har bir
formatdagi ko'rinish (HTML, Excel, Word, PDF, matn).

Barcha testlar mok'langan Odoo bilan ishlaydi — tarmoqqa chiqilmaydi.
"""
from __future__ import annotations

import re

import pytest

import app.services.odoo_tools as odoo_tools
from app.services import list_layout as ll
from app.services import report_html as rh
from app.services.tool_errors import ToolInputError

META = {
    "name": {"string": "Xodim nomi", "type": "char"},
    "job_title": {"string": "Lavozim", "type": "char"},
    "work_phone": {"string": "Ish telefoni", "type": "char"},
    "work_email": {"string": "Email", "type": "char"},
    "department_id": {"string": "Bo'lim", "type": "many2one", "relation": "hr.department"},
    "company_id": {"string": "Kompaniya", "type": "many2one", "relation": "res.company"},
    "amount_total": {"string": "Jami", "type": "monetary"},
    "date_order": {"string": "Sana", "type": "datetime"},
}
RETAIL = "Rahbariyat (Retail) / Operatsion"
EMPLOYEES = [
    {"id": 1, "name": "ASATULLAYEV JOVOXIR", "job_title": "Online Sales Manager", "work_phone": "+998917850020",
     "work_email": "sales_1@dunyabunya.com", "department_id": [52, f"{RETAIL} / Savdo bo‘limi / Отдел B2B"]},
    {"id": 2, "name": "ABDIYEV MUROD", "job_title": "Head of the branch", "work_phone": "998977140844",
     "work_email": False, "department_id": [20, f"{RETAIL} / Filiallar tarmog‘i / Hasanboy filiali"]},
    {"id": 3, "name": "RIXSIYEV ABDULLOX", "job_title": "Cashier", "work_phone": "9335",
     "work_email": False, "department_id": [20, f"{RETAIL} / Filiallar tarmog‘i / Hasanboy filiali"]},
    {"id": 4, "name": "YUSUPOV <CEO>", "job_title": False, "work_phone": False, "work_email": False,
     "department_id": False},
]
FIELDS = ["name", "job_title", "work_phone", "work_email"]


@pytest.fixture
def odoo(monkeypatch, tmp_path):
    """Odoo o'rniga xodimlar ro'yxati; fayllar vaqtinchalik papkaga yoziladi."""
    state = type("State", (), {"calls": [], "rows": EMPLOYEES, "total": None})()

    async def execute_kw(model, method, args, kwargs=None):
        kwargs = kwargs or {}
        state.calls.append((model, method, args, kwargs))
        if method == "fields_get":
            return META
        if method == "search_count":
            return len(state.rows) if state.total is None else state.total
        if method == "read_group":
            return []
        return [dict(r) for r in state.rows][: kwargs.get("limit")]

    monkeypatch.setattr(odoo_tools._odoo, "execute_kw", execute_kw)
    monkeypatch.setattr(odoo_tools, "REPORTS_DIR", tmp_path)
    from app.services import claude_report
    monkeypatch.setattr(claude_report, "REPORTS_DIR", tmp_path)
    state.dir = tmp_path
    return state


def _file(result, folder):
    url = result["url"] if isinstance(result, dict) else result
    return folder / url.rsplit("/", 1)[-1]


# ── Tuzilma (list_layout) ───────────────────────────────────────────────────

def test_groups_get_short_titles_without_the_prefix_shared_by_all():
    rows = [{"name": r["name"], "department_id": (r["department_id"] or [0, False])[1]} for r in EMPLOYEES]
    layout = ll.build(rows, [("name", "Xodim nomi"), ("department_id", "Bo'lim")],
                      {"department_id": "many2one"}, "department_id")

    assert layout.grouped and layout.group_label == "Bo'lim"
    assert layout.columns == [("name", "Xodim nomi")]             # guruh ustuni jadvaldan olinadi
    assert [(g.title, g.context, len(g.rows)) for g in layout.groups] == [
        ("Hasanboy filiali", "Filiallar tarmog‘i", 2), ("Отдел B2B", "Savdo bo‘limi", 1), ("Ko'rsatilmagan", "", 1)]
    assert layout.groups[0].full.startswith("Rahbariyat (Retail)")  # to'liq nom saqlanadi


def test_segment_repeated_in_most_groups_is_dropped_from_the_context():
    names = ["Retail / Boshqaruv", "Retail / Operatsion / Kadrlar", "Retail / Operatsion / Marketing",
             "Retail / Operatsion / Moliya / Kassa", "Retail / Operatsion / Ombor / Sklad"]
    layout = ll.build([{"n": i, "d": name} for i, name in enumerate(names)], [("n", "N"), ("d", "Bo'lim")], {}, "d")
    assert [(g.title, g.context) for g in layout.groups] == [
        ("Boshqaruv", ""), ("Kadrlar", ""), ("Marketing", ""), ("Kassa", "Moliya"), ("Sklad", "Ombor")]


def test_ungrouped_layout_and_unknown_group_field():
    columns = [("name", "Ism"), ("work_phone", "Telefon")]
    rows = [{"name": "A", "work_phone": "+998901234567"}]
    for group_by in ("", "department_id"):
        layout = ll.build(rows, columns, {}, group_by)
        assert not layout.grouped and layout.columns == columns and layout.rows == rows
    assert ll.build(rows, columns, {}, "").kinds == {"name": "text", "work_phone": "phone"}


def test_phone_date_and_path_presentation():
    assert ll.phone_parts("+998917850020, 998 90 035 99 99") == [
        ("+998 91 785 00 20", "+998917850020"), ("+998 90 035 99 99", "+998900359999")]
    assert ll.phone_parts("917850020") == [("+998 91 785 00 20", "+998917850020")]
    assert ll.phone_parts("9335") == [] and ll.phone_text("9335") == "9335"   # ichki qisqa raqam — o'zgarishsiz
    assert ll.date_text("2026-10-04 16:26") == "04.10.2026 16:26" and ll.date_text("2026-10-04") == "04.10.2026"
    assert ll.date_sort_key("2026-10-04 16:26") < ll.date_sort_key("2026-10-05")
    assert ll.split_path("A / B / C") == ("C", "A › B") and ll.split_path("Yagona") == ("Yagona", "")


# ── HTML ────────────────────────────────────────────────────────────────────

async def test_html_report_grouped_by_department(odoo):
    result = await odoo_tools.generate_html_report("hr.employee", [["company_id", "=", 1]], FIELDS,
                                                   "Sotuv xodimlari", group_by="department_id")
    page = _file(result, odoo.dir).read_text(encoding="utf-8")
    body = re.sub(r"<style>.*?</style>|<script>.*?</script>", "", page, flags=re.S)

    # Guruh maydoni o'qiladi, lekin jadval ustuni bo'lmaydi; har guruh — o'z bloki
    read = next(a for _m, method, a, _k in odoo.calls if method == "search_read")
    assert read[1] == FIELDS + ["department_id"]
    assert body.count('<details class="grp"') == 3 and "<th>Bo&#x27;lim</th>" not in body
    assert '<span class="gt">Отдел B2B</span><span class="gc">Savdo bo‘limi</span>' in body
    assert '<span class="gn" data-n="2">2 ta</span>' in body
    # Tepada: jami, guruhlar soni va taqsimot (nom bosilsa guruhga o'tadi)
    assert ">Jami yozuvlar</div>" in body and ">Bo&#x27;lim soni</div>" in body
    assert "Bo&#x27;lim bo&#x27;yicha taqsimot" in body and '<a href="#g1">' in body
    # Telefon va email bosiladi; ichki qisqa raqam va Odoo'dagi nomlar oddiy matn bo'lib qoladi
    assert '<a class="lnk" href="tel:+998917850020">+998 91 785 00 20</a>' in body
    assert '<a class="lnk" href="mailto:sales_1@dunyabunya.com">' in body
    assert ">9335</td>" in body and "YUSUPOV &lt;CEO&gt;" in body and "<CEO>" not in body
    # Tor ekranda kartochka ko'rinishi uchun har katakda ustun nomi; ustunlar hamma guruhda bir xil kenglikda
    assert 'data-l="Lavozim"' in body and body.count("<colgroup>") == 3
    assert len(set(re.findall(r"<colgroup>.*?</colgroup>", body))) == 1


async def test_html_report_without_grouping_is_one_searchable_list(odoo):
    odoo.rows = EMPLOYEES * 3   # 12 qator — qidiruv va saralash yoqiladi
    result = await odoo_tools.generate_html_report("hr.employee", [], FIELDS, "Xodimlar", order="name desc")
    page = _file(result, odoo.dir).read_text(encoding="utf-8")
    body = re.sub(r"<style>.*?</style>|<script>.*?</script>", "", page, flags=re.S)

    assert 'class="grp"' not in body and body.count("<table") == 1
    assert 'class="tools all" data-all' in body and "data-s" in body
    read = next(k for _m, method, _a, k in odoo.calls if method == "search_read")
    assert read["order"] == "name desc"


async def test_grouped_report_charts_the_amount_when_there_is_one(odoo):
    odoo.rows = [
        {"id": 1, "name": "S1", "amount_total": 100.0, "date_order": "2026-10-04 11:26:39", "department_id": [1, "Kichik"]},
        {"id": 2, "name": "S2", "amount_total": 900.0, "date_order": "2026-10-04 08:00:00", "department_id": [2, "Katta"]},
        {"id": 3, "name": "S3", "amount_total": 50.0, "date_order": "2026-10-03 08:00:00", "department_id": [2, "Katta"]},
    ]
    result = await odoo_tools.generate_html_report("sale.order", [], ["name", "date_order", "amount_total"],
                                                   "Buyurtmalar", group_by="department_id")
    body = re.sub(r"<style>.*?</style>|<script>.*?</script>", "", _file(result, odoo.dir).read_text(encoding="utf-8"),
                  flags=re.S)

    chart = body[body.index('class="hbars"'):body.index("</section>", body.index('class="hbars"'))]
    assert chart.index("Katta") < chart.index("Kichik") and "Jami yig&#x27;indisi" in body   # summa bo'yicha, kattasi oldinda
    assert '<span class="gs">Jami: 950</span>' in body
    # Sana — Toshkent vaqtida, kun.oy.yil; saralash kaliti bilan
    assert 'data-v="202610041626"' in body and ">04.10.2026 16:26</td>" in body


# ── Excel ───────────────────────────────────────────────────────────────────

async def test_excel_grouped_has_band_rows_outline_and_a_summary_sheet(odoo):
    from openpyxl import load_workbook

    result = await odoo_tools.export_excel("hr.employee", [], FIELDS, "Sotuv xodimlari", group_by="department_id")
    book = load_workbook(_file(result, odoo.dir), rich_text=True)
    sheet, summary = book["Ro'yxat"], book["Xulosa"]

    assert [c.value for c in sheet[4]] == ["Xodim nomi", "Lavozim", "Ish telefoni", "Email"]
    band = str(sheet["A5"].value)
    assert band.startswith("Hasanboy filiali") and "Filiallar tarmog‘i" in band and "2 ta" in band
    assert "A5:D5" in {str(r) for r in sheet.merged_cells.ranges}
    assert sheet["A6"].value == "ABDIYEV MUROD" and sheet["C6"].value == "+998 97 714 08 44"
    assert sheet.row_dimensions[6].outlineLevel == 1 and sheet.row_dimensions[5].outlineLevel == 0
    assert sheet.sheet_properties.outlinePr.summaryBelow is False and not sheet.auto_filter.ref
    assert str(sheet.cell(row=sheet.max_row, column=1).value) == "Jami — 4 ta"

    assert [c.value for c in summary[4]] == ["Bo'lim", "Tarkibida", "Soni"]
    assert [[c.value for c in row] for row in summary.iter_rows(min_row=5, max_row=8)] == [
        ["Hasanboy filiali", "Filiallar tarmog‘i", 2], ["Отдел B2B", "Savdo bo‘limi", 1],
        ["Ko'rsatilmagan", None, 1], ["Jami", None, 4]]


async def test_excel_grouped_puts_subtotals_in_the_band_row(odoo):
    from openpyxl import load_workbook

    odoo.rows = [
        {"id": 1, "name": "S1", "amount_total": 100.5, "department_id": [1, "Ali"]},
        {"id": 2, "name": "S2", "amount_total": 900.0, "department_id": [1, "Ali"]},
        {"id": 3, "name": "S3", "amount_total": 50.0, "department_id": [2, "Vali"]},
    ]
    result = await odoo_tools.export_excel("sale.order", [], ["name", "amount_total"], "Buyurtmalar",
                                           group_by="department_id", order="amount_total desc")
    book = load_workbook(_file(result, odoo.dir), rich_text=True)
    sheet = book["Ro'yxat"]

    assert sheet["B5"].value == 1000.5 and sheet["B8"].value == 50.0          # guruh qatorida oraliq yig'indi
    assert sheet.cell(row=sheet.max_row, column=2).value == 1050.5            # umumiy jami
    assert [c.value for c in book["Xulosa"][5]] == ["Ali", 2, 1000.5]


async def test_excel_without_grouping_keeps_the_flat_filterable_table(odoo):
    from openpyxl import load_workbook

    result = await odoo_tools.export_excel("hr.employee", [], FIELDS + ["department_id"], "Xodimlar")
    book = load_workbook(_file(result, odoo.dir))
    assert book.sheetnames == ["Xodimlar"] and book.active.auto_filter.ref == "A4:E8"
    assert book.active["C5"].value == "+998 91 785 00 20"


# ── Word, PDF, matn ─────────────────────────────────────────────────────────

async def test_word_document_has_group_headings_and_styled_tables(odoo):
    import docx
    from docx.oxml.ns import qn

    result = await odoo_tools.export_word("hr.employee", [], FIELDS, "Sotuv xodimlari", group_by="department_id")
    document = docx.Document(_file(result, odoo.dir))

    texts = [p.text for p in document.paragraphs]
    assert texts[1] == "Sotuv xodimlari" and "4 ta yozuv" in texts[2] and "Bo'lim: 3 ta" in texts[2]
    assert any(t.startswith("Hasanboy filiali") and t.endswith("2 ta") for t in texts)
    assert len(document.tables) == 3
    header = document.tables[0].rows[0]
    assert [c.text for c in header.cells] == ["Xodim nomi", "Lavozim", "Ish telefoni", "Email"]
    assert header.cells[0]._tc.tcPr.find(qn("w:shd")).get(qn("w:fill")) == "184F95"
    assert header._tr.trPr.find(qn("w:tblHeader")) is not None              # sarlavha har sahifada takrorlanadi
    # Word sxemasi element tartibini talab qiladi: tblBorders — tblLayout va tblLook'dan oldin
    tags = [child.tag.split("}")[1] for child in document.tables[0]._tbl.tblPr]
    assert tags.index("tblBorders") < tags.index("tblLook")
    assert "tblLayout" not in tags or tags.index("tblBorders") < tags.index("tblLayout")
    assert document.tables[0].rows[1].cells[2].text == "+998 97 714 08 44"


async def test_pdf_is_generated_for_grouped_and_flat_lists(odoo):
    grouped = await odoo_tools.export_pdf("hr.employee", [], FIELDS, "Sotuv xodimlari", group_by="department_id")
    flat = await odoo_tools.export_pdf("hr.employee", [], FIELDS, "Xodimlar")
    for result in (grouped, flat):
        data = _file(result, odoo.dir).read_bytes()
        assert data.startswith(b"%PDF") and len(data) > 3000


async def test_text_export_shows_group_context_and_plain_values(odoo):
    result = await odoo_tools.export_text("hr.employee", [], FIELDS, "Sotuv xodimlari", group_by="department_id")
    text = odoo_tools.pop_text_export(result["text_export"])

    assert "<b>Hasanboy filiali</b> <i>(Filiallar tarmog‘i)</i> — 2 ta" in text
    assert "1. <b>ABDIYEV MUROD</b> — Head of the branch · +998 97 714 08 44" in text
    assert "<b>Ko'rsatilmagan</b> — 1 ta\n1. <b>YUSUPOV &lt;CEO&gt;</b>" in text


# ── Saralash, sahifalash, kompaniya ─────────────────────────────────────────

async def test_bad_order_or_group_field_is_rejected_before_odoo_is_asked(odoo):
    for kwargs in ({"order": "name; drop table"}, {"group_by": "department_id.name"}, {"group_by": "a b"}):
        with pytest.raises(ToolInputError):
            await odoo_tools.export_excel("hr.employee", [], FIELDS, "X", **kwargs)
    assert not [1 for _m, method, _a, _k in odoo.calls if method == "search_read"]


async def test_small_export_keeps_the_natural_order_and_big_one_pages_by_id(odoo, monkeypatch):
    await odoo_tools.export_excel("hr.employee", [], FIELDS, "Kichik")
    first = [k for _m, method, _a, k in odoo.calls if method == "search_read"]
    assert len(first) == 1 and "order" not in first[0] and "offset" not in first[0]   # modelning o'z tartibi (ism bo'yicha)

    monkeypatch.setattr(odoo_tools, "_READ_PAGE_SIZE", 2)
    odoo.calls.clear()
    await odoo_tools.export_excel("hr.employee", [], FIELDS, "Katta", order="name")
    pages = [k for _m, method, _a, k in odoo.calls if method == "search_read"]
    assert pages[0]["order"] == "name" and "offset" not in pages[0]
    assert all(p["order"] == "name, id" for p in pages[1:]) and pages[1]["offset"] == 0    # barqaror tartibda boshidan


async def test_employee_tools_can_be_limited_to_one_company(odoo):
    await odoo_tools.search_employees_readonly("Sardor", company_id=1)
    await odoo_tools.get_employees_by_department(company_id=1)
    await odoo_tools.search_employees_readonly()
    domains = [a[0] for _m, method, a, _k in odoo.calls if method in ("search_read", "read_group")]
    assert domains[0] == [["active", "=", True], ["name", "ilike", "Sardor"], ["company_id", "=", 1]]
    assert domains[1] == [["active", "=", True], ["company_id", "=", 1]]
    assert domains[2] == [["active", "=", True]]


def test_table_stack_mode_marks_the_title_cell_and_labels_the_rest():
    html = rh.table(["Ism", "Telefon", "Summa"], [["Ali", "+998901234567", 1500000.0]],
                    kinds=["text", "phone", "number"], stack=True, widths=[20, 20, 10])
    assert '<table class="stack fx">' in html and '<col style="width:40.00%">' in html
    assert '<td class="s t" data-l="Ism">Ali</td>' in html
    assert '<td class="nw" data-l="Telefon"><a class="lnk" href="tel:+998901234567">+998 90 123 45 67</a></td>' in html
    assert 'data-l="Summa">1.5 mln</td>' in html


# ── Eksport natijasi: fayldagi yozuvlar soni ────────────────────────────────

async def test_every_export_reports_how_many_records_the_file_holds(odoo):
    """Ilgari to'liq sig'gan eksport faqat havola qaytarardi: model fayl haqida yozganda sonni
    oldingi javobdan olar va 5 qatorli faylni "38 ta" deb yozgan."""
    kw = dict(model="hr.employee", domain=[], fields=FIELDS, title="Xodimlar")
    for export in (odoo_tools.export_excel, odoo_tools.export_word, odoo_tools.export_pdf,
                   odoo_tools.export_csv, odoo_tools.generate_html_report):
        result = await export(**kw)
        assert result["rows_in_file"] == 4 and result["rows_total"] == 4 and "note" not in result
        assert _file(result, odoo.dir).exists()

    odoo.total = 900
    partial = await odoo_tools.export_excel(**kw)
    assert partial["rows_in_file"] == 4 and partial["rows_total"] == 900 and "900" in partial["note"]
