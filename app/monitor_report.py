"""
Kunlik Excel hisobot (2026-10-13).

Har kuni `REPORT_TIME` da (`TIMEZONE` bo'yicha; ishlab chiqarishda 00:00) undan oldingi 24 soat
uchun `hisobot_YYYY-MM-DD.xlsx` tuziladi, `REPORTS_DIR`ga saqlanadi va hisobot guruhining
(`REPORT_CHAT_ID`) "General" topigiga yuboriladi. `REPORT_TIME=00:00` bo'lsa bu o'tgan kalendar
kuni; boshqa vaqt bo'lsa (masalan sinovdagi 11:00) -- kechagi 11:00 dan bugungi 11:00 gacha,
sana sifatida davr boshlangan kun yoziladi. Bot hisobot vaqtida o'chiq bo'lgan bo'lsa, ishga
tushishi bilan yuborilmagan kunlarning hisobotini yuboradi.

Tartib (`build_report`):
  1. o'sha kunning tasniflanmagan xabarlari tasniflanadi, kutayotgan tekshiruvlar yopiladi;
  2. BARCHA RAQAMLARNI SQL/kod hisoblaydi (`monitor_db.compute_day_stats`) va `daily_stats`ga yozadi;
  3. model faqat matnli xulosa yozadi: unga tayyor raqamlar va xabarlar mazmuni beriladi, u
     hech narsa sanamaydi. Xabari yo'q toifa uchun model chaqirilmaydi. Kechagi kun bilan
     solishtirish va javobsiz so'rovlar soni haqidagi jumlalarni kod yozadi;
  4. xulosalar `daily_summaries`da saqlanadi (keyingi oylik taqqoslash uchun).

1-varaq "Kunlik hisobot": toifalar jadvali, har toifa bo'yicha xulosa, umumiy xulosa.
2-varaq "Javobsiz so'rovlar": muddatida menejer javob bermagan so'rovlar.

Yuborishda xato bo'lsa qayta uriniladi; baribir bo'lmasa admin chatga xabar beriladi va hisobot
"yuborilmagan" bo'lib qoladi (keyingi yarim tunda yoki bot qayta ishga tushganda yana uriniladi).
Hisobotdan keyin bazaning kunlik zaxira nusxasi olinadi.
"""

import asyncio
import logging
import math
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
_MONTHS = (
    "yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr",
)
_WEEKDAYS = ("dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba")
_SUMMARY_MESSAGES_LIMIT = 40
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
class Report:
    day: date
    stats: DayStats
    category_summaries: dict[str, str] = field(default_factory=dict)
    overall_summary: str = ""
    missed: list[MissedRequest] = field(default_factory=list)
    previous_complaints: int | None = None  # undan oldingi kunning shikoyatlari (ma'lumot bo'lmasa None)


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


async def _ask_summary(user_text: str) -> str | None:
    try:
        data = await llm_client.request_json(SUMMARY_SYSTEM_PROMPT, user_text, _SUMMARY_SCHEMA, "summary")
    except LLMUnavailable as exc:
        logger.warning("Xulosa uchun modelga yetib bo'lmadi: %s", exc)
        return None
    text = " ".join(str((data or {}).get("summary") or "").split())
    return text or None


def _numbers(stat: CategoryStat | DayStats) -> str:
    return f"jami {stat.total} ta xabar: shikoyat {stat.complaints}, ijobiy {stat.positive}, neytral {stat.neutral}"


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
    """Bitta toifa bo'yicha xulosa: 2-4 jumla, aniq misollar bilan ("Boshqa" uchun -- faqat
    umumiy mazmun). Xabari yo'q toifa uchun model chaqirilmaydi."""
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
    return text or f"Bu toifada {_numbers(stat)}. (Matnli xulosa tuzilmadi: model javob bermadi.)"


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
        missed = f"Javobsiz qolgan so'rovlar: {stats.missed} ta (\"Javobsiz so'rovlar\" varag'ida)."
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


async def _classify_remaining(day: date) -> None:
    """Hisobotdan oldin o'sha kunning tasniflanmagan xabarlarini tasniflaydi."""
    try:
        for _pass in range(_CLASSIFY_PASSES):
            if not await monitor_db.pending_count(day):
                return
            await monitor_classifier.classify_pending(day)
    except LLMUnavailable as exc:
        logger.warning("Hisobotdan oldin tasniflab bo'lmadi (model javob bermayapti): %s", exc)


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
_STATS_WIDTHS = (38, 11, 11, 11, 11, 11)
_MISSED_HEADERS = ("Vaqt", "Guruh", "Mijoz", "So'rov mazmuni", "Menejer (@username)")
_MISSED_WIDTHS = (9, 34, 28, 60, 24)
NO_MANAGER_TEXT = "menejer yo'q"


def _set_widths(sheet, widths) -> None:
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index)].width = width


def _text_block(sheet, row: int, text: str, last_column: str = "F", chars_per_line: int = 92) -> None:
    """Bir necha ustunni birlashtirib, o'ralib (wrap) o'qiladigan matn qatori yozadi. Excel
    birlashtirilgan kataklar balandligini o'zi moslamaydi -- shuning uchun matn uzunligiga qarab qo'yiladi."""
    sheet.merge_cells(f"A{row}:{last_column}{row}")
    cell = sheet.cell(row=row, column=1, value=text)
    cell.alignment = _WRAP
    lines = sum(max(1, math.ceil(len(part) / chars_per_line)) for part in text.split("\n"))
    sheet.row_dimensions[row].height = max(18, lines * 15.5 + 4)


def _write_stats_sheet(sheet, report: Report) -> None:
    stats = report.stats
    sheet.title = "Kunlik hisobot"
    _set_widths(sheet, _STATS_WIDTHS)

    sheet.merge_cells("A1:F1")
    sheet["A1"] = f"Kunlik hisobot — {format_period(report.day)}"
    sheet["A1"].font = Font(bold=True, size=14)
    sheet["A3"] = "Toifalar jadvali"
    sheet["A3"].font = _BOLD

    for column, header in enumerate(_STATS_HEADERS, start=1):
        cell = sheet.cell(row=4, column=column, value=header)
        cell.font, cell.fill, cell.border, cell.alignment = _BOLD, _HEADER_FILL, _BORDER, _CENTER

    def write_row(row: int, values, *, bold: bool = False, gray: bool = False) -> None:
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

    row = 5
    for stat in stats.categories:
        share = stat.share / 100 if stat.share is not None else None
        write_row(row, (stat.category, stat.total, stat.complaints, stat.positive, stat.neutral, share))
        row += 1
    write_row(
        row, (monitor_db.STAT_TOTAL, stats.total, stats.complaints, stats.positive, stats.neutral, 1 if stats.total else None),
        bold=True,
    )
    row += 1
    write_row(row, (monitor_db.STAT_FILTERED, stats.filtered, None, None, None, None), gray=True)
    row += 1
    if stats.unclassified:
        write_row(row, ("Tasniflab bo'lmagan xabarlar", stats.unclassified, None, None, None, None), gray=True)
        row += 1

    row += 1
    for index, category in enumerate(CATEGORIES, start=1):
        sheet.cell(row=row, column=1, value=f"{index}) {category} — umumiy xulosa").font = _BOLD
        _text_block(sheet, row + 1, report.category_summaries.get(category, EMPTY_CATEGORY_TEXT))
        row += 3
    sheet.cell(row=row, column=1, value="Umumiy xulosa").font = Font(bold=True, size=12)
    _text_block(sheet, row + 1, report.overall_summary)


def _write_missed_sheet(sheet, report: Report) -> None:
    sheet.title = "Javobsiz so'rovlar"
    _set_widths(sheet, _MISSED_WIDTHS)
    if not report.missed:
        _text_block(sheet, 1, f"Barcha so'rovlarga {settings.SLA_MINUTES} daqiqada javob berilgan", last_column="E")
        sheet["A1"].font = _BOLD
        return
    for column, header in enumerate(_MISSED_HEADERS, start=1):
        cell = sheet.cell(row=1, column=column, value=header)
        cell.font, cell.fill, cell.border, cell.alignment = _BOLD, _HEADER_FILL, _BORDER, _CENTER
    for row, request in enumerate(report.missed, start=2):
        values = (
            request.at.strftime("%H:%M"), request.group, request.customer, request.summary,
            request.manager or NO_MANAGER_TEXT,
        )
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=column, value=value)
            cell.border, cell.alignment = _BORDER, _WRAP
    sheet.freeze_panes = "A2"


def report_path(day: date) -> Path:
    return Path(settings.REPORTS_DIR) / f"hisobot_{day.isoformat()}.xlsx"


def write_excel(report: Report) -> Path:
    """Hisobotni `REPORTS_DIR/hisobot_YYYY-MM-DD.xlsx` qilib yozadi."""
    workbook = Workbook()
    _write_stats_sheet(workbook.active, report)
    _write_missed_sheet(workbook.create_sheet(), report)
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
        "Hisobot tuzildi: %s (jami %s, shikoyat %s, javobsiz %s)",
        path, report.stats.total, report.stats.complaints, report.stats.missed,
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
    kecha bitta ham xabar bo'lmagan bo'lsa ham kechagi hisobot yuboriladi (yarim tundagi ish)."""
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
