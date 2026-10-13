"""
Kunlik hisobot (2026-10-12; 2026-10-13 dan oddiy jadval ko'rinishida).

Har kuni yarim tunda (`REPORT_TIMEZONE` bo'yicha 00:00) bot jarayonidagi fon vazifa
(`daily_report_loop`) o'tgan kun -- oxirgi 24 soat -- uchun hisobot tuzadi:

  1. `message_logs`dan (kompaniyaga oid mijoz xabarlari) va `daily_stats` sanoqlaridan
     (kompaniyaga aloqasiz xabarlar, "tovar yo'q", javobsiz savollar) ma'lumot yig'adi;
  2. 8 ta toifaning shu kungi sonini `daily_stats`ga yozib qo'yadi;
  3. HTML fayl yozadi: `DAILY_REPORT_DIR/stats_YYYY-MM-DD.html` -- BITTA ODDIY JADVAL,
     toifalarga bo'lingan: har bir toifa ostida shu kungi shikoyatlar ("Ijobiy Fikrlar" ostida --
     ijobiy fikrlar): vaqt, guruh, mijoz, xabar. Karta, diagramma, skript yo'q (foydalanuvchi
     talabi, 2026-10-13);
  4. `DAILY_REPORT_CHAT_ID` berilgan bo'lsa faylni shu chatga yuboradi;
  5. `MESSAGE_LOG_RETENTION_DAYS`dan eski `message_logs` yozuvlarini o'chiradi.

Qo'lda ishga tushirish (loyiha papkasidan):

    .venv\\Scripts\\python.exe -m app.daily_report                  # kechagi kun
    .venv\\Scripts\\python.exe -m app.daily_report --date 2026-10-11 --send

yoki ichki guruhda `/hisobot` buyrug'i (`app/bot/handlers/hub_admin.py`).
"""

import argparse
import asyncio
import html
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile
from sqlalchemy import delete, select, update

from app import group_registry, group_stats
from app.config import settings
from app.database import async_session_maker
from app.group_ai import CATEGORIES
from app.models import DailyStats, MessageLog

logger = logging.getLogger("aiogram_bot")

_MONTHS = (
    "yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr",
)
_WEEKDAYS = ("dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba")
_TEXT_LIMIT = 1000


# ---------------------------------------------------------------------------
# Ma'lumot yig'ish
# ---------------------------------------------------------------------------


@dataclass
class MessageRow:
    at: datetime  # mahalliy vaqt
    group: str
    user: str
    text: str
    link: str | None


@dataclass
class CategoryRow:
    name: str
    total: int = 0  # shu toifadagi barcha kompaniyaga oid xabarlar (savol va buyurtmalar ham)
    complaints: int = 0
    positive: int = 0
    # Jadvalga chiqadigan xabarlar: shikoyatlar ("Ijobiy Fikrlar" toifasida -- ijobiy fikrlar)
    messages: list[MessageRow] = field(default_factory=list)


@dataclass
class ReportData:
    day: date
    categories: list[CategoryRow]
    counters: dict[str, int]

    @property
    def total(self) -> int:
        return sum(row.total for row in self.categories)

    @property
    def complaints_total(self) -> int:
        return sum(row.complaints for row in self.categories)

    @property
    def positive_total(self) -> int:
        return sum(row.positive for row in self.categories)

    @property
    def filtered(self) -> int:
        return self.counters.get(group_stats.STAT_FILTERED, 0)

    @property
    def is_empty(self) -> bool:
        return not self.total and not any(self.counters.values())


def _user_label(log: MessageLog) -> str:
    name = log.full_name or ""
    if log.username:
        name = f"{name} (@{log.username})" if name else f"@{log.username}"
    return name or str(log.user_id)


async def collect_report_data(day: date) -> ReportData:
    """`day` (mahalliy kun) uchun hisobot ma'lumotini yig'adi."""
    start, end = group_stats.day_bounds(day)
    tz = group_stats.report_tz()
    async with async_session_maker() as session:
        logs = (
            await session.execute(
                select(MessageLog)
                .where(MessageLog.timestamp >= start, MessageLog.timestamp < end)
                .order_by(MessageLog.timestamp)
            )
        ).scalars().all()
        counter_rows = (
            await session.execute(
                select(DailyStats.category_name, DailyStats.count).where(
                    DailyStats.date == day, DailyStats.category_name.in_(group_stats.COUNTER_NAMES)
                )
            )
        ).all()
    titles = await group_registry.group_titles()

    categories = {name: CategoryRow(name) for name in CATEGORIES.values()}
    for log in logs:
        # Toifa nomi keyinchalik o'zgartirilgan bo'lsa eski yozuvlar "Boshqa"ga qo'shiladi
        category = categories.get(log.category) or categories[CATEGORIES["other"]]
        category.total += 1
        if log.sentiment not in ("negative", "positive"):
            continue  # oddiy savol/buyurtma -- jadvalga chiqmaydi
        if log.sentiment == "negative":
            category.complaints += 1
        else:
            category.positive += 1
        category.messages.append(
            MessageRow(
                group_stats.as_utc(log.timestamp).astimezone(tz),
                titles.get(log.group_id, str(log.group_id)),
                _user_label(log),
                log.message_text or "",
                log.message_link,
            )
        )
    return ReportData(
        day=day,
        categories=list(categories.values()),
        counters={name: 0 for name in group_stats.COUNTER_NAMES} | dict(counter_rows),
    )


async def save_category_counts(data: ReportData) -> None:
    """8 ta toifaning shu kungi sonini `daily_stats`ga yozadi (qayta ishga tushirilsa -- yangilaydi)."""
    async with async_session_maker() as session:
        for row in data.categories:
            changed = await session.execute(
                update(DailyStats)
                .where(DailyStats.date == data.day, DailyStats.category_name == row.name)
                .values(count=row.total)
            )
            if not changed.rowcount:
                session.add(DailyStats(date=data.day, category_name=row.name, count=row.total))
        await session.commit()


async def delete_old_logs(today: date) -> int:
    if settings.MESSAGE_LOG_RETENTION_DAYS <= 0:
        return 0
    cutoff, _end = group_stats.day_bounds(today - timedelta(days=settings.MESSAGE_LOG_RETENTION_DAYS))
    async with async_session_maker() as session:
        result = await session.execute(delete(MessageLog).where(MessageLog.timestamp < cutoff))
        await session.commit()
    return result.rowcount or 0


# ---------------------------------------------------------------------------
# HTML: bitta oddiy jadval, toifalarga bo'lingan
# ---------------------------------------------------------------------------

_CSS = """
body { font-family: Arial, sans-serif; font-size: 14px; margin: 16px; color: #111; background: #fff; }
h1 { font-size: 18px; margin: 0 0 12px; }
.wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #bbb; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #e9e9e9; }
tr.cat td { background: #f3f3f3; font-weight: bold; }
td.time { white-space: nowrap; }
"""


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def format_day(day: date) -> str:
    return f"{day.year}-yil {day.day}-{_MONTHS[day.month - 1]}, {_WEEKDAYS[day.weekday()]}"


def _message_row(row: MessageRow) -> str:
    text = row.text if len(row.text) <= _TEXT_LIMIT else row.text[:_TEXT_LIMIT] + "…"
    message = _esc(text).replace("\n", "<br>") or "(matnsiz)"
    # Havola faqat Telegram manziliga -- bazadagi qiymat hujjatga tekshiruvsiz tushmaydi
    if row.link and row.link.startswith("https://t.me/"):
        message += f' <a href="{_esc(row.link)}">ochish</a>'
    return (
        f'<tr><td class="time">{row.at:%H:%M}</td><td>{_esc(row.group)}</td><td>{_esc(row.user)}</td>'
        f"<td>{message}</td></tr>"
    )


def render_report_html(data: ReportData) -> str:
    """Hisobotni bitta oddiy jadval qilib qaytaradi: har bir toifa -- sarlavha qatori (nomi va
    soni), ostida shu toifadagi xabarlar."""
    rows: list[str] = []
    for category in data.categories:
        rows.append(f'<tr class="cat"><td colspan="4">{_esc(category.name)} — {len(category.messages)}</td></tr>')
        rows.extend(_message_row(message) for message in category.messages)
    title = f"Kunlik hisobot — {_esc(format_day(data.day))}"
    return f"""<!doctype html>
<html lang="uz">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{_CSS}</style>
</head>
<body>
<h1>{title}</h1>
<div class="wrap">
<table>
<thead><tr><th>Vaqt</th><th>Guruh</th><th>Mijoz</th><th>Xabar</th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody>
</table>
</div>
</body>
</html>
"""


def build_summary(data: ReportData) -> str:
    """Fayl bilan birga yuboriladigan qisqa matn."""
    counters = data.counters
    return (
        f"📊 Kunlik hisobot — {format_day(data.day)}\n"
        f"Kompaniyaga oid xabarlar: {data.total}\n"
        f"🔴 Shikoyatlar: {data.complaints_total}   🟢 Ijobiy fikrlar: {data.positive_total}\n"
        f"⏰ Javobsiz qolgan savollar: {counters[group_stats.STAT_ALERTS]}   "
        f"📦 Tovar yo'q: {counters[group_stats.STAT_STOCK]}\n"
        f"Kompaniyaga aloqasiz (filtrlangan): {data.filtered}"
    )


# ---------------------------------------------------------------------------
# Ishga tushirish
# ---------------------------------------------------------------------------


def report_path(day: date) -> Path:
    return Path(settings.DAILY_REPORT_DIR) / f"stats_{day.isoformat()}.html"


async def generate_report(day: date) -> tuple[Path, ReportData]:
    """`day` uchun hisobotni tuzib faylga yozadi va toifalar sonini `daily_stats`ga saqlaydi."""
    data = await collect_report_data(day)
    await save_category_counts(data)
    path = report_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report_html(data), encoding="utf-8")
    logger.info("Kunlik hisobot yozildi: %s (kompaniyaga oid xabarlar: %s)", path, data.total)
    return path, data


async def send_report(
    bot: Bot, path: Path, data: ReportData, chat_id: int | None = None, thread_id: int | None = None
) -> bool:
    chat_id = chat_id or settings.DAILY_REPORT_CHAT_ID
    if not chat_id:
        return False
    if thread_id is None and chat_id == settings.DAILY_REPORT_CHAT_ID:
        thread_id = settings.DAILY_REPORT_TOPIC_ID
    try:
        await bot.send_document(
            chat_id, FSInputFile(path, filename=path.name), caption=build_summary(data), message_thread_id=thread_id
        )
        return True
    except TelegramAPIError:
        logger.exception("Kunlik hisobotni yuborib bo'lmadi (chat_id=%s)", chat_id)
        return False


async def run_daily_report(bot: Bot | None, day: date) -> Path:
    path, data = await generate_report(day)
    # Hech narsa bo'lmagan kunning bo'sh hisoboti faylga yoziladi, lekin chatga yuborilmaydi
    if bot is not None and not data.is_empty:
        await send_report(bot, path, data)
    deleted = await delete_old_logs(day)
    if deleted:
        logger.info("Eski message_logs yozuvlari o'chirildi: %s ta", deleted)
    return path


def seconds_until_midnight(now: datetime) -> float:
    """`now`dan (hisobot vaqt mintaqasida) keyingi 00:00 gacha qolgan soniyalar."""
    next_midnight = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=now.tzinfo)
    return (next_midnight - now).total_seconds()


async def daily_report_loop(bot: Bot) -> None:
    """Bot jarayonida BITTA nusxada ishlaydigan fon vazifa (`app/bot/__init__.py`): har yarim
    tunda o'tgan kun hisobotini tuzadi. Bot yarim tunda o'chiq bo'lgan bo'lsa, ishga tushganda
    kechagi kunning tuzilmay qolgan hisobotini tuzib oladi."""
    if not settings.DAILY_REPORT_ENABLED or not settings.FEEDBACK_HUB_GROUP_ID:
        return
    tz = group_stats.report_tz()
    try:
        yesterday = datetime.now(tz).date() - timedelta(days=1)
        if not report_path(yesterday).exists() and not (await collect_report_data(yesterday)).is_empty:
            await run_daily_report(bot, yesterday)
    except Exception:
        logger.exception("Kechagi kun hisobotini tuzishda xatolik")
    while True:
        # +2 soniya: uyg'onganda soat aniq 00:00 dan o'tgan bo'ladi
        await asyncio.sleep(seconds_until_midnight(datetime.now(tz)) + 2)
        try:
            await run_daily_report(bot, datetime.now(tz).date() - timedelta(days=1))
        except Exception:
            logger.exception("Kunlik hisobotni tuzishda xatolik")


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Kunlik hisobotni qo'lda tuzish")
    parser.add_argument("--date", help="YYYY-MM-DD (berilmasa -- kechagi kun)")
    parser.add_argument("--send", action="store_true", help="DAILY_REPORT_CHAT_ID ga ham yuborish")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    day = date.fromisoformat(args.date) if args.date else group_stats.local_day() - timedelta(days=1)
    path, data = await generate_report(day)
    print(path.resolve())
    if args.send:
        bot = Bot(token=settings.BOT_TOKEN)
        try:
            print("yuborildi" if await send_report(bot, path, data) else "yuborilmadi (DAILY_REPORT_CHAT_ID?)")
        finally:
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(_main())
