"""Parsing what a user writes in reply to a document photo: "#Nodir, 12,10,2026 +".

The date may be "15.10", "15,10,2026", "15 oktabr", "ertaga"; "#Nodir" names who talked to the customer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

_RELATIVE = {
    "bugun": 0, "ertaga": 1, "indinga": 2,
    "бугун": 0, "эртага": 1, "индинга": 2,
    "сегодня": 0, "завтра": 1, "послезавтра": 2,
}

_MONTHS = {
    1: "yanvar yan январь января январ янв",
    2: "fevral fev февраль февраля феврал фев",
    3: "mart март марта",
    4: "aprel apr апрель апреля апрел апр",
    5: "may май мая",
    6: "iyun июнь июня июн",
    7: "iyul июль июля июл",
    8: "avgust avg август августа авг",
    9: "sentabr sentyabr sen сентябрь сентября сентябр сен",
    10: "oktabr oktyabr okt октябрь октября октябр окт",
    11: "noyabr noy ноябрь ноября ноябр ноя",
    12: "dekabr dek декабрь декабря декабр дек",
}
_MONTH_BY_NAME = {name: number for number, names in _MONTHS.items() for name in names.split()}
# Uzbek case endings: "oktabrda", "oktabrgacha"
_SUFFIXES = ("gacha", "ning", "da", "ga", "гача", "нинг", "да", "га")

# punctuation before the date, left over once the "#Nodir," in front of it is taken out
_LEADER = re.compile(r"^[\s.,;:!+]+")
# words and punctuation people add after the date: "15.10.2026 y.", "15 oktabr gacha", "15.10 kuni", "12,10,2026 +"
_TRAILER = re.compile(r"(?:[\s.,;:!+]+|(?<![^\W\d_])(?:yil|y|йил|год|г|gacha|kuni|гача|куни)\b)+$")
_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_NUMERIC = re.compile(r"(\d{1,2})\s*[.,/\-]\s*(\d{1,2})(?:\s*[.,/\-]\s*(\d{4}|\d{2}))?")
_NAMED = re.compile(r"(\d{1,2})[\s\-]*([^\W\d_]+)(?:[\s,\-]+(\d{4}))?")
# the same two shapes found among other words; the digits must not be a piece of a longer number
_NUMERIC_INSIDE = re.compile(rf"(?<!\d){_NUMERIC.pattern}(?!\d)")
_NAMED_INSIDE = re.compile(rf"(?<!\d){_NAMED.pattern}")
_TAG = re.compile(r"#(\w+)")
# a tagged reply longer than this is a conversation, not a payment date
_TAGGED_LIMIT = 200
# one word of a person's name: letters, with an apostrophe or a hyphen inside ("G'ayrat", "Abdu-Rahmon")
_WORD = re.compile(r"[^\W\d_]+(?:['’‘`ʻʼ\-][^\W\d_]+)*")
_GAP = re.compile(r"[\s,;:]+")


@dataclass(frozen=True)
class Reply:
    """A reply to a document photo. `due` is None when it is tagged but no date could be read in it.

    `tag` is the name of who talked to the customer, without the "#" it may have been written with.
    """

    due: date | None
    tag: str | None


def _month(word: str) -> int | None:
    if word in _MONTH_BY_NAME:
        return _MONTH_BY_NAME[word]
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and word[: -len(suffix)] in _MONTH_BY_NAME:
            return _MONTH_BY_NAME[word[: -len(suffix)]]
    return None


def _build(day: int, month: int, year: str | None, today: date) -> date:
    if year is None:
        candidate = date(today.year, month, day)
        return candidate if candidate >= today else date(today.year + 1, month, day)
    return date(int(year) + (2000 if len(year) == 2 else 0), month, day)


def parse_date(text: str, today: date) -> date | None:
    """Return the date written in `text`, or None when the whole text is not a date.

    Raises ValueError for a date that does not exist (31.11). A date without a year means its next occurrence.
    """
    if len(text) > 40:
        return None
    s = _TRAILER.sub("", _LEADER.sub("", text.strip().lower()))
    if s in _RELATIVE:
        return today + timedelta(days=_RELATIVE[s])
    if m := _ISO.fullmatch(s):
        day, month, year = int(m[3]), int(m[2]), m[1]
    elif m := _NUMERIC.fullmatch(s):
        day, month, year = int(m[1]), int(m[2]), m[3]
    elif (m := _NAMED.fullmatch(s)) and (month := _month(m[2])):
        day, year = int(m[1]), m[3]
    else:
        return None
    # "45.50" or "12-70" is some other number, not a mistyped date
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    return _build(day, month, year, today)


def _date_inside(text: str, today: date) -> date | None:
    """The date standing among other words: "12,10,2026 + to'laydi dedi"."""
    s = text.lower()
    numeric = [m for m in _NUMERIC_INSIDE.finditer(s) if 1 <= int(m[1]) <= 31 and 1 <= int(m[2]) <= 12]
    # in "5,5 mln 15,10,2026" the one written with a year is the date
    numeric.sort(key=lambda m: m[3] is None)
    if numeric:
        return _build(int(numeric[0][1]), int(numeric[0][2]), numeric[0][3], today)
    for m in _NAMED_INSIDE.finditer(s):
        if (month := _month(m[2])) and 1 <= int(m[1]) <= 31:
            return _build(int(m[1]), month, m[3], today)
    return None


def _name(text: str) -> str | None:
    """`text` as a person's name of one to three words; None when it is not one."""
    words = text.split()
    if 1 <= len(words) <= 3 and all(_WORD.fullmatch(word) for word in words):
        return " ".join(words)
    return None


def parse_reply(text: str, today: date) -> Reply | None:
    """Read "#Nodir, 12,10,2026 +" or "Nodir 12.10.2026": the payment date and who talked to the customer.

    Returns None when the text is not a payment date. Raises ValueError for a date that does not exist.
    Without "#" the text has to be a date alone or a name followed by a date, so ordinary chat under a photo
    is not taken for one; with "#" the date may stand among other words.
    """
    tags = _TAG.findall(text)
    rest = _TAG.sub(" ", text)
    due = parse_date(rest, today)
    if tags:
        if due is None and len(text) <= _TAGGED_LIMIT:
            due = _date_inside(rest, today)
        return Reply(due, ", ".join(tags))
    if due is not None:
        return Reply(due, None)
    text = text.strip()
    for gap in _GAP.finditer(text):
        name = _name(text[:gap.start()])
        if name is None:
            # what stands before is no longer a name, and it only grows from here
            break
        due = parse_date(text[gap.end():], today)
        if due is not None:
            return Reply(due, name)
    return None
