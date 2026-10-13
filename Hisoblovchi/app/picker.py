"""An inline month calendar for choosing the two ends of a date range ("Oraliq")."""
from __future__ import annotations

import calendar
from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

MONTHS = ("Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun", "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr")
WEEKDAYS = ("Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya")
# a press on a cell that is not a day does nothing
IDLE = "noop"


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _shift(month: date, by: int) -> date:
    index = month.year * 12 + month.month - 1 + by
    return date(index // 12, index % 12 + 1, 1)


def keyboard(month: date, start: date | None, today: date) -> InlineKeyboardMarkup:
    """The days of `month`. `start` is the first day of the range once chosen: the next press then picks its end."""
    first = start.isoformat() if start else "-"
    rows = [
        [
            _button("◀", f"cal:{first}:{_shift(month, -1):%Y-%m}"),
            _button(f"{MONTHS[month.month - 1]} {month.year}", IDLE),
            _button("▶", f"cal:{first}:{_shift(month, 1):%Y-%m}"),
        ],
        [_button(name, IDLE) for name in WEEKDAYS],
    ]
    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(month.year, month.month):
        row = []
        for number in week:
            if not number:
                row.append(_button(" ", IDLE))
                continue
            day = date(month.year, month.month, number)
            # today and the chosen first day are marked, to keep one's bearings
            text = f"[{number}]" if day == start else f"·{number}·" if day == today else str(number)
            row.append(_button(text, f"pick:{first}:{day.isoformat()}"))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)
