"""
Kunlik Excel hisobot (2026-10-13; 2026-10-15 dan har guruh alohida varaqda).

Har kuni `REPORT_TIME` da (`TIMEZONE` bo'yicha; ishlab chiqarishda 00:00) undan oldingi 24 soat
uchun `hisobot_YYYY-MM-DD.xlsx` tuziladi, `REPORTS_DIR`ga saqlanadi va hisobot guruhining
(`REPORT_CHAT_ID`) "General" topigiga yuboriladi. `REPORT_TIME=00:00` bo'lsa bu o'tgan kalendar
kuni; boshqa vaqt bo'lsa (masalan sinovdagi 11:00) -- kechagi 11:00 dan bugungi 11:00 gacha,
sana sifatida davr boshlangan kun yoziladi. Bot hisobot vaqtida o'chiq bo'lgan bo'lsa, ishga
tushishi bilan yuborilmagan kunlarning hisobotini yuboradi.

Fayl tuzilishi (foydalanuvchi talabi, 2026-10-15):
  1-varaq "Umumiy" -- hamma guruhlar birga: toifalar jadvali, har toifa bo'yicha xulosa, umumiy
     xulosa, ostida barcha javobsiz so'rovlar (qaysi guruhdan ekani bilan).
  Keyin har bir guruhga bitta varaq (varaq nomi -- guruh nomi, alifbo tartibida): o'sha guruhning
     xuddi shunday kunlik hisoboti va ostida o'sha guruhning javobsiz so'rovlari.
  Har bir javobsiz so'rov yonida: muddatdan keyin bo'lsa ham kimdir (menejer bo'lishi shart emas)
     mijozga javob berdimi -- kim va necha daqiqadan keyin.

Tartib (`build_report`):
  1. o'sha kunning tasniflanmagan xabarlari tasniflanadi, kutayotgan tekshiruvlar yopiladi;
  2. BARCHA RAQAMLARNI SQL/kod hisoblaydi (`monitor_db.compute_day_stats`) va umumiy sonlarni
     `daily_stats`ga yozadi;
  3. model faqat matnli xulosa yozadi: unga tayyor raqamlar va xabarlar mazmuni beriladi, u
     hech narsa sanamaydi. Xabari yo'q toifa/guruh uchun model chaqirilmaydi; har bir guruhning
     xulosalari bitta so'rovda olinadi. Kechagi kun bilan solishtirish va javobsiz so'rovlar soni
     haqidagi jumlalarni kod yozadi;
  4. umumiy xulosalar `daily_summaries`da saqlanadi (keyingi oylik taqqoslash uchun).

Yuborishda xato bo'lsa qayta uriniladi; baribir bo'lmasa admin chatga xabar beriladi va hisobot
"yuborilmagan" bo'lib qoladi (keyingi hisobot vaqtida yoki bot qayta ishga tushganda yana uriniladi).
Hisobotdan keyin bazaning kunlik zaxira nusxasi olinadi.
"""

import asyncio
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import FSInputFile
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from app import llm_client, monitor_classifier, monitor_db
from app.config import settings
from app.llm_client import LLMUnavailable
from app.monitor_db import CATEGORIES, SUMMARY_OVERALL, CategoryStat, DayStats, MissedRequest

logger = logging.getLogger(__name__)

EMPTY_CATEGORY_TEXT = "Bugun bu toifada xabar bo'lmadi"
OVERALL_SHEET = "Umumiy"
_MONTHS = (
    "yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr",
)
_WEEKDAYS = ("dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba")
_SUMMARY_MESSAGES_LIMIT = 40
_GROUP_MESSAGES_PER_CATEGORY = 15
# Bir vaqtda nechta guruhning hisoboti tayyorlanadi (model so'rovlari baribir `llm_client` navbatida)
_GROUP_CONCURRENCY = 4
_SEND_RETRY_DELAYS = (0, 15, 60)
_CLASSIFY_PASSES = 30

SUMMARY_SYSTEM_PROMPT = """\
Sen qurilish mahsulotlari sotadigan "dunyabunya" kompaniyasi rahbariyati uchun kunlik hisobotga \
qisqa xulosa yozasan. Senga mijozlar guruhlaridagi xabarlarning mazmuni va TAYYOR raqamlar beriladi.

Qoidalar:
- Oddiy, tushunarli o'zbek tilida, lotin harflarida yoz.
- Hech narsani o'zing sanama va yangi raqam o'ylab topma. Son kerak bo'lsa faqat senga berilgan \
tayyor raqamlardan foydalan.
- Ro'yxatda yo'q narsani qo'shma, taxmin qilma.
- Ro'yxat, chiziqcha yoki sarlavha ishlatma: oddiy, ketma-ket jumlalar bilan yoz."""

_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}


@dataclass
class GroupReport:
    """Bitta guruhning shu kungi hisoboti (Excel'da alohida varaq)."""

    chat_id: int
    title: str
    stats: DayStats
    category_summaries: dict[str, str] = field(default_factory=dict)
    overall_summary: str = ""
    missed: list[MissedRequest] = field(default_factory=list)
    previous_complaints: int | None = None


@dataclass
class Report:
    """Hamma guruhlar bo'yicha hisobot ("Umumiy" varaq) va har bir guruhniki."""

    day: date
    stats: DayStats
    category_summaries: dict[str, str] = field(default_factory=dict)
    overall_summary: str = ""
    missed: list[MissedRequest] = field(default_factory=list)
    previous_complaints: int | None = None  # undan oldingi kunning shikoyatlari (ma'lumot bo'lmasa None)
    groups: list[GroupReport] = field(default_factory=list)


def format_day(day: date) -> str:
    return f"{day.year}-yil {day.day}-{_MONTHS[day.month - 1]}, {_WEEKDAYS[day.weekday()]}"


def format_period(day: date) -> str:
    """Hisobot davri: 00:00 dagi hisobot uchun -- kunning o'zi, boshqa vaqt uchun -- qaysi
    soatdan qaysi soatgacha ekani."""
    at = monitor_db.report_time()
    if at == time.min:
        return format_day(day)
    next_day = day + timedelta(days=1)
    return (
        f"{day.year}-yil {day.day}-{_MONTHS[day.month - 1]} {at:%H:%M} dan "
        f"{next_day.day}-{_MONTHS[next_day.month - 1]} {at:%H:%M} gacha"
    )


# ---------------------------------------------------------------------------
# Matnli xulosalar
# ---------------------------------------------------------------------------


async def _ask(user_text: str, schema: dict, name: str) -> dict | None:
    try:
        return await llm_client.request_json(SUMMARY_SYSTEM_PROMPT, user_text, schema, name)
    except LLMUnavailable as exc:
        logger.warning("Xulosa uchun modelga yetib bo'lmadi: %s", exc)
        return None


def _clean(value) -> str | None:
    return " ".join(str(value or "").split()) or None


async def _ask_summary(user_text: str) -> str | None:
    return _clean((await _ask(user_text, _SUMMARY_SCHEMA, "summary") or {}).get("summary"))


def _numbers(stat: CategoryStat | DayStats) -> str:
    return f"jami {stat.total} ta xabar: shikoyat {stat.complaints}, ijobiy {stat.positive}, neytral {stat.neutral}"


def _fallback(stat: CategoryStat | DayStats) -> str:
    return f"Bu toifada {_numbers(stat)}. (Matnli xulosa tuzilmadi: model javob bermadi.)"


_CATEGORY_TASK = (
    "Shu toifa bo'yicha 2-4 jumlali xulosa yoz. 3-5 tagacha aniq misol keltir: qaysi guruhda nima "
    "bo'lgani, guruh nomi bilan. \"Mijozlar turli savollar berdi\" kabi umumiy gaplar yozma."
)
# "Boshqa" toifasi uchun (foydalanuvchi talabi, 2026-10-13): xabarlar birma-bir yozilmaydi, gap
# umuman nimalar haqida ketgani aytiladi
_OTHER_TASK = (
    "Bu toifa uchun alohida xabarlarni va guruhlarni sanab o'tma. Gap umuman nimalar haqida "
    "ketganini 1-2 jumlada umumlashtirib yoz (masalan: \"Mijozlar tovar bor-yo'qligi, buyurtma "
    "berish va ish vaqti haqida so'rashdi\")."
)


async def summarize_category(day: date, stat: CategoryStat) -> str:
    """Hamma guruhlar bo'yicha bitta toifaning xulosasi: 2-4 jumla, aniq misollar bilan ("Boshqa"
    uchun -- faqat umumiy mazmun). Xabari yo'q toifa uchun model chaqirilmaydi."""
    if not stat.total:
        return EMPTY_CATEGORY_TEXT
    messages = await monitor_db.category_messages(day, stat.category, _SUMMARY_MESSAGES_LIMIT)
    lines = [f"- {m.group} | {m.sentiment} | {m.summary or m.text[:200]}" for m in messages]
    shown = f"(ko'rsatilgani {len(lines)} ta, jami {stat.total} ta)" if len(lines) < stat.total else ""
    text = await _ask_summary(
        f"Toifa: {stat.category}\n"
        f"Tayyor raqamlar: {_numbers(stat)}.\n\n"
        f"Xabarlar (guruh | tur | mazmun) {shown}:\n" + "\n".join(lines) + "\n\n"
        + (_OTHER_TASK if stat.category == "Boshqa" else _CATEGORY_TASK)
    )
    return text or _fallback(stat)


def comparison_sentences(stats: DayStats, previous_complaints: int | None) -> str:
    """Kechagi kun bilan solishtirish va javobsiz so'rovlar -- raqamli jumlalarni kod yozadi."""
    today = stats.complaints
    if previous_complaints is None:
        compare = f"Shikoyatlar soni: {today} ta (undan oldingi kun bilan solishtirish uchun ma'lumot yo'q)."
    elif today == previous_complaints:
        compare = f"Shikoyatlar soni oldingi kunga nisbatan o'zgarmadi: {today} ta."
    else:
        change = "ko'paydi" if today > previous_complaints else "kamaydi"
        compare = (
            f"Shikoyatlar soni oldingi kunga nisbatan {abs(today - previous_complaints)} taga {change}: "
            f"oldingi kuni {previous_complaints} ta, shu kuni {today} ta."
        )
    if stats.missed:
        missed = f"Javobsiz qolgan so'rovlar: {stats.missed} ta (pastdagi \"Javobsiz so'rovlar\" ro'yxatida)."
    else:
        missed = f"Barcha so'rovlarga {settings.SLA_MINUTES} daqiqa ichida javob berilgan."
    return f"{compare} {missed}"


async def summarize_overall(report: Report) -> str:
    """Umumiy xulosa: asosiy muammolar va ijobiy jihatlarni model yozadi (3-4 jumla), kechagi
    kun bilan solishtirish va javobsiz so'rovlar sonini kod qo'shadi."""
    stats = report.stats
    tail = comparison_sentences(stats, report.previous_complaints)
    if not stats.total:
        return f"Shu kuni kompaniyaga oid mijoz xabari bo'lmadi. {tail}"
    table = "\n".join(
        f"- {row.category}: jami {row.total}, shikoyat {row.complaints}, ijobiy {row.positive}, neytral {row.neutral}"
        for row in stats.categories
    )
    summaries = "\n".join(
        f"{index}) {category}: {text}"
        for index, (category, text) in enumerate(report.category_summaries.items(), start=1)
        if text != EMPTY_CATEGORY_TEXT
    )
    text = await _ask_summary(
        f"Sana: {format_period(report.day)}\n"
        f"Tayyor raqamlar: kompaniyaga oid xabarlar {_numbers(stats)}. "
        f"Kompaniyaga aloqasiz (filtrlangan) xabarlar: {stats.filtered} ta.\n\n"
        f"Toifalar bo'yicha raqamlar:\n{table}\n\nToifalar bo'yicha xulosalar:\n{summaries}\n\n"
        "Kun bo'yicha 3-4 jumlali umumiy xulosa yoz: asosiy muammolar va ijobiy jihatlar. Shikoyatlar "
        "sonining oldingi kun bilan solishtirmasi va javobsiz so'rovlar haqida YOZMA -- ular alohida qo'shiladi."
    )
    if text is None:
        text = f"Shu kuni kompaniyaga oid {_numbers(stats)}. (Matnli xulosa tuzilmadi: model javob bermadi.)"
    return f"{text} {tail}"


def _group_schema(categories: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "toifalar": {
                "type": "object",
                "properties": {category: {"type": "string"} for category in categories},
                "required": categories,
                "additionalProperties": False,
            },
            "umumiy": {"type": "string"},
        },
        "required": ["toifalar", "umumiy"],
        "additionalProperties": False,
    }


async def summarize_group(day: date, group: GroupReport) -> None:
    """Bitta guruhning toifalar bo'yicha va umumiy xulosasini BITTA model so'rovida oladi
    (200 ta guruhda so'rovlar soni oshib ketmasligi uchun). Xabari yo'q toifa/guruh uchun model
    chaqirilmaydi; model javob bermasa -- raqamli matn."""
    stats = group.stats
    tail = comparison_sentences(stats, group.previous_complaints)
    active = [stat for stat in stats.categories if stat.total]
    group.category_summaries = {stat.category: EMPTY_CATEGORY_TEXT for stat in stats.categories}
    if not active:
        group.overall_summary = f"Shu kuni kompaniyaga oid mijoz xabari bo'lmadi. {tail}"
        return

    blocks = []
    for stat in active:
        messages = await monitor_db.category_messages(day, stat.category, _GROUP_MESSAGES_PER_CATEGORY, group.chat_id)
        lines = "\n".join(f"- {m.sentiment} | {m.summary or m.text[:200]}" for m in messages)
        blocks.append(f"[{stat.category}] ({_numbers(stat)})\n{lines}")
    data = await _ask(
        f"Guruh: {group.title}\n"
        f"Sana: {format_period(day)}\n"
        f"Tayyor raqamlar: kompaniyaga oid xabarlar {_numbers(stats)}.\n\n"
        "Toifalar bo'yicha xabarlar (tur | mazmun):\n" + "\n\n".join(blocks) + "\n\n"
        "Vazifa: \"toifalar\" ichida har bir toifa uchun (kalit -- toifa nomi) 1-3 jumlali xulosa yoz: "
        "aniq misollar bilan, nima bo'lgani. \"Boshqa\" toifasi uchun xabarlarni sanab o'tma -- gap umuman "
        "nimalar haqida ketganini umumlashtir. \"umumiy\" -- shu guruh bo'yicha 2-3 jumlali xulosa: asosiy "
        "muammolar va ijobiy jihatlar. Shikoyatlar sonining solishtirmasi va javobsiz so'rovlar haqida YOZMA "
        "-- ular alohida qo'shiladi.",
        _group_schema([stat.category for stat in active]),
        "group_summary",
    ) or {}
    texts = data.get("toifalar") if isinstance(data.get("toifalar"), dict) else {}
    for stat in active:
        group.category_summaries[stat.category] = _clean(texts.get(stat.category)) or _fallback(stat)
    overall = _clean(data.get("umumiy")) or f"Shu kuni kompaniyaga oid {_numbers(stats)}."
    group.overall_summary = f"{overall} {tail}"


async def _classify_remaining(day: date) -> None:
    """Hisobotdan oldin o'sha kunning tasniflanmagan xabarlarini tasniflaydi."""
    try:
        for _pass in range(_CLASSIFY_PASSES):
            if not await monitor_db.pending_count(day):
                return
            await monitor_classifier.classify_pending(day)
    except LLMUnavailable as exc:
        logger.warning("Hisobotdan oldin tasniflab bo'lmadi (model javob bermayapti): %s", exc)


async def _previous_complaints(day: date, chat_id: int) -> int | None:
    """Guruhning oldingi kundagi shikoyatlari. Bot bu guruhda shu davrgacha birorta xabar
    ko'rmagan bo'lsa (guruh yangi qo'shilgan) -- None: solishtirish uchun ma'lumot yo'q."""
    if not await monitor_db.has_messages_before(chat_id, monitor_db.period_bounds(day)[0]):
        return None
    return (await monitor_db.compute_day_stats(day - timedelta(days=1), chat_id)).complaints


async def build_group_report(day: date, chat_id: int, title: str) -> GroupReport:
    group = GroupReport(
        chat_id=chat_id,
        title=title,
        stats=await monitor_db.compute_day_stats(day, chat_id),
        missed=await monitor_db.missed_requests(day, chat_id),
        previous_complaints=await _previous_complaints(day, chat_id),
    )
    try:
        await summarize_group(day, group)
    except Exception:
        # Bitta guruhdagi kutilmagan xato butun hisobotni to'xtatmasin
        logger.exception("'%s' guruhining xulosasini tuzishda xatolik", title)
        group.category_summaries = {
            stat.category: _fallback(stat) if stat.total else EMPTY_CATEGORY_TEXT for stat in group.stats.categories
        }
        group.overall_summary = (
            f"Shu kuni kompaniyaga oid {_numbers(group.stats)}. "
            f"{comparison_sentences(group.stats, group.previous_complaints)}"
        )
    return group


async def build_report(day: date) -> Report:
    """`day` uchun hisobot ma'lumotini tayyorlaydi va kunlik sonlar/xulosalarni bazaga yozadi."""
    await _classify_remaining(day)
    await monitor_db.resolve_checks()
    stats = await monitor_db.compute_day_stats(day)
    await monitor_db.save_day_stats(stats)
    report = Report(
        day=day,
        stats=stats,
        missed=await monitor_db.missed_requests(day),
        previous_complaints=await monitor_db.stored_complaints(day - timedelta(days=1)),
    )
    for stat in stats.categories:
        report.category_summaries[stat.category] = await summarize_category(day, stat)
    report.overall_summary = await summarize_overall(report)
    for category, text in report.category_summaries.items():
        await monitor_db.save_summary(day, category, text)
    await monitor_db.save_summary(day, SUMMARY_OVERALL, report.overall_summary)

    slots = asyncio.Semaphore(_GROUP_CONCURRENCY)

    async def one(chat_id: int, title: str) -> GroupReport:
        async with slots:
            return await build_group_report(day, chat_id, title)

    report.groups = list(await asyncio.gather(*(one(chat_id, title) for chat_id, title in await monitor_db.report_groups(day))))
    return report


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------

_THIN = Side(style="thin", color="999999")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_HEADER_FILL = PatternFill("solid", fgColor="D9D9D9")
_GRAY_FILL = PatternFill("solid", fgColor="F2F2F2")
_BOLD = Font(bold=True)
_WRAP = Alignment(wrap_text=True, vertical="top")
_CENTER = Alignment(horizontal="center", vertical="center")
_STATS_HEADERS = ("Toifa", "Jami", "Shikoyat", "Ijobiy", "Neytral", "Ulush")
NO_MANAGER_TEXT = "menejer yo'q"
NO_LATER_ANSWER_TEXT = "Yo'q"
# Varaq ustunlari: A-F -- toifalar jadvali; javobsiz so'rovlar jadvali G-I ni ham ishlatadi
_COLUMN_WIDTHS = {"A": 34, "B": 11, "C": 11, "D": 11, "E": 11, "F": 11, "G": 30, "H": 30, "I": 18}
# Javobsiz so'rovlar jadvali: (sarlavha, birinchi ustun, oxirgi ustun) -- bir nechta ustun birlashtiriladi
_MISSED_COLUMNS_OVERALL = (
    ("Guruh", "A", "A"), ("Vaqt", "B", "B"), ("Mijoz", "C", "D"), ("So'rov mazmuni", "E", "G"),
    ("Keyin kimdir javob berdimi", "H", "H"), ("Menejer", "I", "I"),
)
_MISSED_COLUMNS_GROUP = (
    ("Mijoz", "A", "A"), ("Vaqt", "B", "B"), ("So'rov mazmuni", "C", "F"),
    ("Keyin kimdir javob berdimi", "G", "G"), ("Menejer", "H", "H"),
)
_SHEET_NAME_LIMIT = 31
_INVALID_SHEET_CHARS = re.compile(r"[\\/*?:\[\]]")


def sheet_name(title: str, used: set[str]) -> str:
    """Excel varaq nomi: taqiqlangan belgilarsiz, 31 belgidan oshmaydi, takrorlanmaydi."""
    base = _INVALID_SHEET_CHARS.sub("-", title or "").strip().strip("'").strip() or "Guruh"
    if base.lower() == "history":  # Excel'da band nom
        base = "History guruhi"
    name = base[:_SHEET_NAME_LIMIT].strip()
    number = 2
    while name.lower() in used:
        suffix = f" ({number})"
        name = base[:_SHEET_NAME_LIMIT - len(suffix)].strip() + suffix
        number += 1
    used.add(name.lower())
    return name


def _width(first: str, last: str) -> float:
    return sum(_COLUMN_WIDTHS[chr(code)] for code in range(ord(first), ord(last) + 1))


def _lines(text: str, width: float) -> int:
    return sum(max(1, math.ceil(len(part) / max(width, 1))) for part in str(text).split("\n"))


def _text_block(sheet, row: int, text: str, last_column: str = "F") -> None:
    """Bir necha ustunni birlashtirib, o'ralib (wrap) o'qiladigan matn qatori yozadi. Excel
    birlashtirilgan kataklar balandligini o'zi moslamaydi -- shuning uchun matn uzunligiga qarab qo'yiladi."""
    sheet.merge_cells(f"A{row}:{last_column}{row}")
    cell = sheet.cell(row=row, column=1, value=text)
    cell.alignment = _WRAP
    sheet.row_dimensions[row].height = max(18, _lines(text, _width("A", last_column) * 1.05) * 15.5 + 4)


def _write_stats_table(sheet, row: int, stats: DayStats) -> int:
    """Toifalar jadvali. Keyingi bo'sh qatorni qaytaradi."""
    sheet.cell(row=row, column=1, value="Toifalar jadvali").font = _BOLD
    row += 1
    for column, header in enumerate(_STATS_HEADERS, start=1):
        cell = sheet.cell(row=row, column=column, value=header)
        cell.font, cell.fill, cell.border, cell.alignment = _BOLD, _HEADER_FILL, _BORDER, _CENTER
    row += 1

    def write_row(values, *, bold: bool = False, gray: bool = False) -> None:
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=column, value=value)
            cell.border = _BORDER
            cell.alignment = Alignment(vertical="center") if column == 1 else _CENTER
            if bold:
                cell.font = _BOLD
            if gray:
                cell.font, cell.fill = Font(color="7F7F7F"), _GRAY_FILL
            if column == 6 and value is not None:
                cell.number_format = "0%"

    for stat in stats.categories:
        share = stat.share / 100 if stat.share is not None else None
        write_row((stat.category, stat.total, stat.complaints, stat.positive, stat.neutral, share))
        row += 1
    write_row(
        (monitor_db.STAT_TOTAL, stats.total, stats.complaints, stats.positive, stats.neutral, 1 if stats.total else None),
        bold=True,
    )
    row += 1
    write_row((monitor_db.STAT_FILTERED, stats.filtered, None, None, None, None), gray=True)
    row += 1
    if stats.unclassified:
        write_row(("Tasniflab bo'lmagan xabarlar", stats.unclassified, None, None, None, None), gray=True)
        row += 1
    return row


def _write_missed_table(sheet, row: int, missed: list[MissedRequest], columns) -> int:
    """Javobsiz so'rovlar ro'yxati. Keyingi bo'sh qatorni qaytaradi."""
    sheet.cell(row=row, column=1, value=f"Javobsiz so'rovlar ({len(missed)} ta)").font = Font(bold=True, size=12)
    row += 1
    if not missed:
        _text_block(sheet, row, f"Barcha so'rovlarga {settings.SLA_MINUTES} daqiqada javob berilgan")
        return row + 1

    def write_row(values, header: bool = False) -> None:
        height = 1
        for (_title, first, last), value in zip(columns, values):
            if first != last:
                sheet.merge_cells(f"{first}{row}:{last}{row}")
            cell = sheet[f"{first}{row}"]
            cell.value = value
            cell.alignment = _CENTER if header else _WRAP
            if header:
                cell.font, cell.fill = _BOLD, _HEADER_FILL
            for code in range(ord(first), ord(last) + 1):
                sheet[f"{chr(code)}{row}"].border = _BORDER
            height = max(height, _lines(value, _width(first, last) * 1.05))
        sheet.row_dimensions[row].height = max(18, height * 15.5 + 4)

    write_row([title for title, _first, _last in columns], header=True)
    row += 1
    with_group = columns is _MISSED_COLUMNS_OVERALL
    for request in missed:
        later = f"Ha — {request.later_answer}" if request.later_answer else NO_LATER_ANSWER_TEXT
        values = [request.at.strftime("%H:%M"), request.customer, request.summary, later, request.manager or NO_MANAGER_TEXT]
        if with_group:
            values = [request.group, values[0], values[1], values[2], values[3], values[4]]
        else:
            values = [values[1], values[0], values[2], values[3], values[4]]
        write_row(values)
        row += 1
    return row


def _write_report_sheet(sheet, heading: str, section: Report | GroupReport, *, overall: bool) -> None:
    """Bitta varaq: sarlavha, toifalar jadvali, toifalar bo'yicha xulosa, umumiy xulosa, ostida
    javobsiz so'rovlar."""
    for column, width in _COLUMN_WIDTHS.items():
        sheet.column_dimensions[column].width = width
    sheet.merge_cells("A1:I1")
    sheet["A1"] = heading
    sheet["A1"].font = Font(bold=True, size=14)

    row = _write_stats_table(sheet, 3, section.stats) + 1
    for index, category in enumerate(CATEGORIES, start=1):
        sheet.cell(row=row, column=1, value=f"{index}) {category} — umumiy xulosa").font = _BOLD
        _text_block(sheet, row + 1, section.category_summaries.get(category, EMPTY_CATEGORY_TEXT))
        row += 3
    sheet.cell(row=row, column=1, value="Umumiy xulosa" if overall else "Guruh bo'yicha xulosa").font = Font(
        bold=True, size=12
    )
    _text_block(sheet, row + 1, section.overall_summary)
    _write_missed_table(sheet, row + 3, section.missed, _MISSED_COLUMNS_OVERALL if overall else _MISSED_COLUMNS_GROUP)


def report_path(day: date) -> Path:
    return Path(settings.REPORTS_DIR) / f"hisobot_{day.isoformat()}.xlsx"


def write_excel(report: Report) -> Path:
    """Hisobotni `REPORTS_DIR/hisobot_YYYY-MM-DD.xlsx` qilib yozadi: "Umumiy" varaq va har bir
    guruhga bitta varaq."""
    period = format_period(report.day)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = OVERALL_SHEET
    _write_report_sheet(sheet, f"Umumiy (barcha guruhlar) — {period}", report, overall=True)
    used = {OVERALL_SHEET.lower()}
    for group in report.groups:
        _write_report_sheet(
            workbook.create_sheet(sheet_name(group.title, used)), f"{group.title} — {period}", group, overall=False
        )
    path = report_path(report.day)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


# ---------------------------------------------------------------------------
# Yuborish
# ---------------------------------------------------------------------------


def build_caption(report: Report) -> str:
    stats = report.stats
    at = monitor_db.report_time()
    period = report.day.isoformat()
    if at != time.min:
        period += f" {at:%H:%M} – {(report.day + timedelta(days=1)).isoformat()} {at:%H:%M}"
    return (
        f"📊 Kunlik hisobot — {period}\n"
        f"Jami (kompaniyaga oid): {stats.total}\n"
        f"Shikoyatlar: {stats.complaints}\n"
        f"Javobsiz so'rovlar: {stats.missed}"
    )


def admin_chat_id() -> int | None:
    return settings.ADMIN_CHAT_ID or settings.ODOO_ALERT_CHAT_ID


async def send_report(bot: Bot, path: Path, report: Report) -> bool:
    """Faylni hisobot guruhiga yuboradi (bir necha urinish). Bo'lmasa admin chatga xabar beradi.

    `REPORT_THREAD_ID` bo'sh bo'lsa `message_thread_id` umuman yuborilmaydi -- forum-guruhda xabar
    aynan shunda "General" topigiga tushadi (General'ning ID'si 1 bo'lsa-da, uni ochiq berib
    bo'lmaydi: Telegram "message thread not found" deb rad etadi)."""
    if not settings.REPORT_CHAT_ID:
        logger.error("REPORT_CHAT_ID berilmagan -- hisobot faqat faylga yozildi: %s", path)
        return False
    error: Exception | None = None
    for delay in _SEND_RETRY_DELAYS:
        if delay:
            await asyncio.sleep(delay)
        try:
            sent = await bot.send_document(
                settings.REPORT_CHAT_ID, FSInputFile(path, filename=path.name), caption=build_caption(report),
                message_thread_id=settings.REPORT_THREAD_ID,
            )
            logger.info(
                "Hisobot yuborildi: %s -> chat %s, topik: %s",
                path.name, settings.REPORT_CHAT_ID, getattr(sent, "message_thread_id", None) or "General",
            )
            return True
        except TelegramRetryAfter as exc:
            error = exc
            await asyncio.sleep(exc.retry_after)
        except TelegramAPIError as exc:
            error = exc
            logger.warning("Hisobotni yuborib bo'lmadi (%s): %s", path.name, exc)
    logger.error("Hisobot yuborilmadi (%s): %s", path.name, error)
    if admin_chat_id():
        try:
            await bot.send_message(
                admin_chat_id(),
                f"⚠️ Kunlik hisobotni ({report.day.isoformat()}) guruhga yuborib bo'lmadi: {error}\n"
                f"Fayl serverda: {path}",
            )
        except TelegramAPIError:
            logger.exception("Admin chatga ham xabar berib bo'lmadi")
    return False


async def run_report(bot: Bot, day: date, *, mark_sent: bool = True) -> bool:
    """Hisobotni tuzadi, faylga yozadi va yuboradi. Yuborilgan bo'lsa True."""
    report = await build_report(day)
    path = await asyncio.to_thread(write_excel, report)
    logger.info(
        "Hisobot tuzildi: %s (jami %s, shikoyat %s, javobsiz %s, guruhlar %s)",
        path, report.stats.total, report.stats.complaints, report.stats.missed, len(report.groups),
    )
    sent = await send_report(bot, path, report)
    if sent and mark_sent:
        await monitor_db.mark_report_sent(day)
    return sent


async def backup() -> None:
    try:
        path = await asyncio.to_thread(monitor_db.backup_database, settings.BACKUP_DIR, settings.BACKUP_KEEP)
        logger.info("Baza zaxira nusxasi olindi: %s", path)
    except Exception:
        logger.exception("Baza zaxira nusxasini olishda xatolik")


def seconds_until_report(now: datetime) -> float:
    """`now`dan (mahalliy vaqtda) keyingi hisobot vaqtigacha (`REPORT_TIME`) qolgan soniyalar."""
    next_run = datetime.combine(now.date(), monitor_db.report_time(), tzinfo=now.tzinfo)
    if next_run <= now:
        next_run += timedelta(days=1)
    return (next_run - now).total_seconds()


async def send_unsent_reports(bot: Bot, today: date, *, include_yesterday: bool) -> None:
    """Yuborilmagan kunlarning hisobotini eskisidan boshlab yuboradi. `include_yesterday` --
    kecha bitta ham xabar bo'lmagan bo'lsa ham kechagi hisobot yuboriladi (hisobot vaqtidagi ish)."""
    days = await monitor_db.unsent_days(today)
    yesterday = today - timedelta(days=1)
    if include_yesterday and yesterday not in days and yesterday not in await monitor_db.sent_days(today):
        days.append(yesterday)
    for day in sorted(days):
        try:
            await run_report(bot, day)
        except Exception:
            logger.exception("%s kungi hisobotni tuzishda xatolik", day)


async def report_loop(bot: Bot) -> None:
    """Bot jarayonida BITTA nusxada ishlaydi (`app/bot/__init__.py`)."""
    tz = monitor_db.local_tz()
    # Bot o'chiq turgan paytda yuborilmay qolgan hisobotlar -- darhol
    await send_unsent_reports(bot, monitor_db.report_day(), include_yesterday=False)
    if not monitor_db.backup_exists():
        await backup()
    while True:
        # +2 soniya: uyg'onganda soat aniq hisobot vaqtidan o'tgan bo'ladi
        await asyncio.sleep(seconds_until_report(datetime.now(tz)) + 2)
        await send_unsent_reports(bot, monitor_db.report_day(), include_yesterday=True)
        await backup()
