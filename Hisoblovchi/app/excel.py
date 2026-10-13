"""A list of reminders as an .xlsx file laid out like the accountants' own sheet "Hujjat".

The sum is left empty for them to type in, with its total underneath; nobody is marked as paid.
"""
from __future__ import annotations

import io
from collections.abc import Iterable
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

SHEET = "Hujjat"
HEADERS = ("Hujjat", "Sana", "Hafta kuni", "Mijoz", "Summa", "To‘langan", "Hodim")
WIDTHS = (14, 14, 16, 38, 20, 13, 26)
WEEKDAYS = ("Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba")
NOT_PAID = "✕"
MONEY = "#,##0.00"
ROW_HEIGHT = 30
_CENTER = Alignment(horizontal="center", vertical="center")
_BOLD = Font(bold=True)


def build(items: Iterable[tuple[str | None, str | None, str | None, date]]) -> bytes:
    """`items` are (order number, customer, employee, payment date), in the order of the chat's list."""
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET
    sheet.append(HEADERS)
    for order_number, customer, employee, due in items:
        sheet.append([order_number or "", due, WEEKDAYS[due.weekday()], customer or "", None, NOT_PAID, employee or ""])
        row = sheet[sheet.max_row]
        for cell in (row[0], row[3], row[6]):
            # text read from a photo or typed in a chat stays text: a leading "=" must never run as a formula
            cell.data_type = "s"
        row[1].number_format = "DD.MM.YYYY"
        row[4].number_format = MONEY
    last = sheet.max_row
    sheet.append([None, None, None, "Jami:", f"=SUM(E2:E{last})"])
    sheet.cell(last + 1, 5).number_format = MONEY
    for row in sheet.iter_rows(min_row=1, max_row=last + 1, max_col=len(HEADERS)):
        sheet.row_dimensions[row[0].row].height = ROW_HEIGHT
        for cell in row:
            cell.alignment = _CENTER
            if cell.row in (1, last + 1):
                cell.font = _BOLD
    for letter, width in zip("ABCDEFG", WIDTHS):
        sheet.column_dimensions[letter].width = width
    sheet.freeze_panes = "A2"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
