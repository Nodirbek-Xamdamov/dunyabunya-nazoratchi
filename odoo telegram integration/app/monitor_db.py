"""
Guruh xabarlari monitoringi bazasi (2026-10-13) -- SQLite, fayl yo'li `.env`dagi `DB_PATH`.

Baza bilan ishlaydigan HAMMA kod shu bitta modulda: jadvallar, so'rovlar, zaxira nusxa. Boshqa
modullar faqat shu yerdagi funksiyalarni chaqiradi, SQL yozmaydi -- keyin PostgreSQL'ga o'tish
uchun faqat `_database_url()` (va SQLite PRAGMA'lari) o'zgaradi. Botning asosiy (Odoo
buyurtmalari) PostgreSQL bazasi bunga aloqasi yo'q -- u `app/database.py`da.

Jadvallar:
  groups           -- bot ko'rgan guruhlar, adminlari va menejer(lar)i
  messages         -- guruhdagi har bir xabar (matn/izoh, media turi, tahrir); UNIQUE(chat_id, message_id)
  classifications  -- mijoz xabarining tasnifi (har xabarga bitta)
  manager_checks   -- "5 daqiqa qoidasi" tekshiruvlari (pending / answered / missed)
  daily_stats      -- kun bo'yicha sonlar (toifalar, jami, filtrlangan, javobsiz)
  daily_summaries  -- kun bo'yicha matnli xulosalar va hisobot yuborilgan vaqt

`daily_stats` va `daily_summaries` kun sayin to'planib boradi: keyingi oylik taqqoslash
("shikoyatlar kamaydimi") aynan shulardan qilinadi.

Vaqtlar bazada UTC'da saqlanadi (`UTCDateTime`); `local_date` -- `TIMEZONE` bo'yicha mahalliy kun.
"""

import logging
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, and_, case,
    event, func, inspect, or_, select, update,
)
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, aliased, mapped_column
from sqlalchemy.types import TypeDecorator

from app.config import settings

logger = logging.getLogger(__name__)

# Toifalar: nomlari aynan shunday, tartibi -- hisobotdagi tartib
CATEGORIES: tuple[str, ...] = (
    "Dastavka va Logistika",
    "Mahsulot Sifati va Nuqsonlar",
    "Komplektatsiya va Hajm",
    "Xodimlar va Xizmat Ko'rsatish",
    "Narx va To'lovlar",
    "Qaytarish va Kafolat",
    "Ijobiy Fikrlar",
    "Boshqa",
)
POSITIVE_CATEGORY = "Ijobiy Fikrlar"
COMPLAINT, POSITIVE, NEUTRAL = "Shikoyat", "Ijobiy", "Neytral"
SENTIMENTS: tuple[str, ...] = (COMPLAINT, POSITIVE, NEUTRAL)

# `daily_stats.category`dagi, toifalardan tashqari qatorlar
STAT_TOTAL = "Jami kompaniyaga oid"
STAT_FILTERED = "Kompaniyaga aloqasiz (filtrlangan)"
STAT_MISSED = "Javobsiz so'rovlar"
# `daily_summaries.category`dagi umumiy xulosa
SUMMARY_OVERALL = "umumiy"

# `messages.status`
PENDING, DONE, STAFF, FAILED = "pending", "done", "staff", "failed"
# `manager_checks.status`
CHECK_PENDING, CHECK_ANSWERED, CHECK_MISSED = "pending", "answered", "missed"


# ---------------------------------------------------------------------------
# Ulanish
# ---------------------------------------------------------------------------


def _database_url() -> str:
    return f"sqlite+aiosqlite:///{Path(settings.DB_PATH).as_posix()}"


engine = create_async_engine(_database_url())
session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")  # o'qish yozishni to'smaydi
    cursor.execute("PRAGMA busy_timeout=5000")  # band bo'lsa 5 soniya kutadi, darhol xato bermaydi
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


class UTCDateTime(TypeDecorator):
    """Vaqtni UTC'da (mintaqasiz) saqlaydi va o'qiganda UTC mintaqasi bilan qaytaradi."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None else None


class Base(DeclarativeBase):
    pass


# Jadval sinflari ichida `date` nomi ustunning o'zi bilan band -- tur shu nom bilan yoziladi
date_type = date


# ---------------------------------------------------------------------------
# Jadvallar
# ---------------------------------------------------------------------------


class Group(Base):
    __tablename__ = "groups"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str | None] = mapped_column(String(255))
    # Guruh adminlari (vergul bilan ajratilgan Telegram ID'lar) -- ularning xabari xodim xabari
    admin_user_ids: Mapped[str] = mapped_column(Text, default="")
    # Menejer(lar): unvonida (custom title) `MANAGER_TITLES`dagi so'z bor admin(lar)
    manager_user_id: Mapped[int | None] = mapped_column(BigInteger)  # birinchisi
    manager_user_ids: Mapped[str] = mapped_column(Text, default="")
    manager_username: Mapped[str | None] = mapped_column(String(255))  # "@ali, @bek"; yo'q bo'lsa NULL
    # Adminlar oxirgi marta qachon Telegram'dan olingan (NULL -- hali olinmagan / yangilash kerak)
    admins_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    # Bot hozir shu guruhdami (chiqarilsa False) -- hisobotda faqat bor guruhlarga varaq ochiladi
    is_present: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")

    @property
    def admin_ids(self) -> frozenset[int]:
        return frozenset(_parse_ids(self.admin_user_ids))

    @property
    def manager_ids(self) -> tuple[int, ...]:
        return _parse_ids(self.manager_user_ids)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="uq_messages_chat_message"),
        Index("ix_messages_chat_sent", "chat_id", "sent_at_utc"),
        Index("ix_messages_date_staff", "local_date", "is_staff"),
        Index("ix_messages_sent", "sent_at_utc"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.chat_id"), index=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
    sender_id: Mapped[int | None] = mapped_column(BigInteger)
    sender_username: Mapped[str | None] = mapped_column(String(128))
    sender_name: Mapped[str | None] = mapped_column(String(255))
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False)
    text: Mapped[str | None] = mapped_column(Text)  # matn yoki media izohi (caption)
    # text | photo | video | voice | audio | video_note | document | sticker | animation | other
    media_type: Mapped[str] = mapped_column(String(16), default="text")
    # Telegram fayl ID'si va (ovoz/video uchun) davomiyligi, soniyada -- ovozni matnga aylantirish uchun
    file_id: Mapped[str | None] = mapped_column(String(255))
    media_duration: Mapped[int | None] = mapped_column(Integer)
    # Ovozli xabar/audio/video nutqining matni: NULL -- hali aylantirilmagan, "" -- nutq topilmadi
    transcript: Mapped[str | None] = mapped_column(Text)
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger)
    sent_at_utc: Mapped[datetime] = mapped_column(UTCDateTime)
    local_date: Mapped[date] = mapped_column(Date)
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # pending -- tasnif kutyapti; done -- tasniflangan; staff -- xodim xabari (tasniflanmaydi);
    # failed -- bir necha urinishdan keyin ham tasniflab bo'lmadi
    status: Mapped[str] = mapped_column(String(12), default=PENDING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    @property
    def full_text(self) -> str:
        """Tasniflanadigan matn: yozilgan matn/izoh va (bo'lsa) nutq matni."""
        return "\n".join(part for part in (self.text, self.transcript) if part)


class Classification(Base):
    __tablename__ = "classifications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    message_ref: Mapped[int] = mapped_column(ForeignKey("messages.id"), unique=True)
    is_company_related: Mapped[bool] = mapped_column(Boolean)
    category: Mapped[str | None] = mapped_column(String(64), index=True)  # kompaniyaga oid bo'lmasa NULL
    sentiment: Mapped[str | None] = mapped_column(String(16))  # Shikoyat | Ijobiy | Neytral
    summary: Mapped[str | None] = mapped_column(Text)  # bir jumlalik o'zbekcha mazmun
    needs_manager: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str] = mapped_column(String(64))  # "ollama:gemma4:12b" yoki "rule" (AI'siz ajratilgan)
    classified_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ManagerCheck(Base):
    __tablename__ = "manager_checks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    message_ref: Mapped[int] = mapped_column(ForeignKey("messages.id"))  # tekshiruvni boshlagan xabar
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    first_message_id: Mapped[int] = mapped_column(BigInteger)  # o'sha xabarning Telegram ID'si
    manager_username: Mapped[str | None] = mapped_column(String(255))  # NULL -- guruhda menejer yo'q
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    deadline: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    status: Mapped[str] = mapped_column(String(12), default=CHECK_PENDING, index=True)
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    local_date: Mapped[date] = mapped_column(Date, index=True)


class DailyStat(Base):
    __tablename__ = "daily_stats"
    __table_args__ = (UniqueConstraint("date", "category", name="uq_daily_stats_date_category"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[date_type] = mapped_column(Date, index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    complaints: Mapped[int] = mapped_column(Integer, default=0)
    positive: Mapped[int] = mapped_column(Integer, default=0)
    neutral: Mapped[int] = mapped_column(Integer, default=0)
    share: Mapped[int | None] = mapped_column(Integer)  # butun foiz; hisoblab bo'lmasa NULL


class DailySummary(Base):
    __tablename__ = "daily_summaries"
    __table_args__ = (UniqueConstraint("date", "category", name="uq_daily_summaries_date_category"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[date_type] = mapped_column(Date, index=True)
    category: Mapped[str] = mapped_column(String(64))  # toifa nomi yoki "umumiy"
    summary_text: Mapped[str] = mapped_column(Text)
    # Shu kunning hisoboti Telegram'ga yuborilgan vaqt ("umumiy" qatorida)
    report_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


# ---------------------------------------------------------------------------
# Yordamchilar
# ---------------------------------------------------------------------------

_tz: tzinfo | None = None


def local_tz() -> tzinfo:
    """`TIMEZONE`. Windows'da `tzdata` paketi bo'lmasa IANA nomlari topilmaydi -- u holda
    kompyuterning o'z vaqt mintaqasi olinadi."""
    global _tz
    if _tz is None:
        try:
            _tz = ZoneInfo(settings.TIMEZONE)
        except (ZoneInfoNotFoundError, ValueError):
            _tz = datetime.now().astimezone().tzinfo
            logger.warning("Vaqt mintaqasi '%s' topilmadi -- kompyuter vaqti ishlatiladi: %s", settings.TIMEZONE, _tz)
    return _tz


def local_day(moment: datetime | None = None) -> date:
    moment = moment or datetime.now(timezone.utc)
    return moment.astimezone(local_tz()).date()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def report_time() -> time:
    """`REPORT_TIME` ("HH:MM") -- hisobot yuboriladigan vaqt. Noto'g'ri yozilgan bo'lsa 00:00."""
    try:
        return datetime.strptime(settings.REPORT_TIME.strip(), "%H:%M").time()
    except ValueError:
        logger.warning("REPORT_TIME noto'g'ri yozilgan: %r (masalan 00:00 yoki 11:00)", settings.REPORT_TIME)
        return time.min


def period_bounds(day: date) -> tuple[datetime, datetime]:
    """`day` hisobot kunining boshi va oxiri (UTC'da): `day` kuni `REPORT_TIME`dan ertasi kuni
    `REPORT_TIME`gacha. `REPORT_TIME=00:00` bo'lsa bu oddiy kalendar kuni."""
    start = datetime.combine(day, report_time(), tzinfo=local_tz())
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def report_day(moment: datetime | None = None) -> date:
    """`moment` (standart -- hozir) qaysi hisobot kuniga tushadi: `REPORT_TIME`dan oldingi vaqt
    hali oldingi hisobot kuniga tegishli."""
    moment = (moment or utc_now()).astimezone(local_tz())
    at = report_time()
    return (moment - timedelta(hours=at.hour, minutes=at.minute)).date()


def _in_period(column, day: date):
    start, end = period_bounds(day)
    return (column >= start) & (column < end)


def _parse_ids(value: str | None) -> tuple[int, ...]:
    return tuple(int(part) for part in (value or "").split(",") if part.strip().lstrip("-").isdigit())


def _join_ids(ids) -> str:
    return ",".join(str(item) for item in ids)


async def init_db() -> None:
    """Baza faylini (papkasi bilan) va jadvallarni yaratadi (mavjud bo'lsa tegmaydi)."""
    Path(settings.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(_upgrade_schema)
    logger.info("Monitoring bazasi tayyor: %s", settings.DB_PATH)


def _upgrade_schema(connection) -> None:
    """Mavjud bazaga keyin qo'shilgan ustun va indekslarni qo'shadi (`create_all` faqat yo'q
    jadvalni yaratadi, bor jadvalni o'zgartirmaydi)."""
    added = (
        ("messages", "media_duration", "INTEGER"),
        ("messages", "transcript", "TEXT"),
        ("groups", "is_present", "BOOLEAN NOT NULL DEFAULT 1"),
    )
    inspector = inspect(connection)
    for table, name, ddl in added:
        if name not in {column["name"] for column in inspector.get_columns(table)}:
            connection.execute(sql_text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
            logger.info("Monitoring bazasi: %s.%s ustuni qo'shildi", table, name)
    connection.execute(sql_text("CREATE INDEX IF NOT EXISTS ix_messages_sent ON messages (sent_at_utc)"))


# ---------------------------------------------------------------------------
# Guruhlar
# ---------------------------------------------------------------------------


async def upsert_group(chat_id: int, title: str | None) -> Group:
    """Guruhni ro'yxatga oladi (yo'q bo'lsa) va nomini yangilaydi."""
    async with session_maker() as session:
        group = await session.get(Group, chat_id)
        if group is None:
            group = Group(chat_id=chat_id, title=title, updated_at=utc_now())
            session.add(group)
            try:
                await session.commit()
            except IntegrityError:  # parallel xabar guruhni yaratib ulgurdi
                await session.rollback()
                return await session.get(Group, chat_id)
            logger.info("Yangi guruh: '%s' (chat_id=%s)", title, chat_id)
        elif (group.title != title and title) or not group.is_present:
            group.title = title or group.title
            group.is_present = True  # xabar keldi yoki bot qayta qo'shildi -- demak bot guruhda
            group.updated_at = utc_now()
            await session.commit()
        return group


async def set_group_present(chat_id: int, present: bool) -> None:
    """Bot guruhdan chiqarildi (False) yoki qayta qo'shildi (True)."""
    async with session_maker() as session:
        await session.execute(update(Group).where(Group.chat_id == chat_id).values(is_present=present))
        await session.commit()


async def report_groups(day: date) -> list[tuple[int, str]]:
    """Hisobotda alohida varaq oladigan guruhlar, nomi bo'yicha alifbo tartibida: bot hozir a'zo
    bo'lgan guruhlar va shu davrda xabari bo'lgan guruhlar. (chat_id, nomi) qaytaradi."""
    start, end = period_bounds(day)
    active = select(Message.chat_id).where(Message.sent_at_utc >= start, Message.sent_at_utc < end)
    async with session_maker() as session:
        rows = (
            await session.execute(
                select(Group.chat_id, Group.title).where(or_(Group.is_present.is_(True), Group.chat_id.in_(active)))
            )
        ).all()
    return sorted(((chat_id, title or str(chat_id)) for chat_id, title in rows), key=lambda row: row[1].lower())


async def has_messages_before(chat_id: int, moment: datetime) -> bool:
    """Bot bu guruhda `moment`dan oldin birorta xabar ko'rganmi (oldingi kun bilan solishtirish uchun)."""
    async with session_maker() as session:
        row = await session.execute(
            select(Message.id).where(Message.chat_id == chat_id, Message.sent_at_utc < moment).limit(1)
        )
        return row.first() is not None


async def get_group(chat_id: int) -> Group | None:
    async with session_maker() as session:
        return await session.get(Group, chat_id)


async def save_group_admins(
    chat_id: int, admin_ids: set[int], managers: list[tuple[int, str | None, str | None]]
) -> Group | None:
    """Guruh adminlari va menejerlarini yozadi. `managers`: (user_id, username, ism)."""
    labels = [f"@{username}" if username else (name or str(user_id)) for user_id, username, name in managers]
    async with session_maker() as session:
        group = await session.get(Group, chat_id)
        if group is None:
            return None
        group.admin_user_ids = _join_ids(sorted(admin_ids))
        group.manager_user_ids = _join_ids(user_id for user_id, _username, _name in managers)
        group.manager_user_id = managers[0][0] if managers else None
        group.manager_username = ", ".join(labels) or None
        group.admins_checked_at = group.updated_at = utc_now()
        await session.commit()
        return group


async def invalidate_group_admins(chat_id: int) -> None:
    """Admin o'zgardi -- keyingi tekshiruvda adminlar Telegram'dan qayta olinadi."""
    async with session_maker() as session:
        await session.execute(update(Group).where(Group.chat_id == chat_id).values(admins_checked_at=None))
        await session.commit()


async def stale_group_ids(older_than: datetime, limit: int) -> list[int]:
    """Adminlari `older_than`dan beri yangilanmagan guruhlar."""
    async with session_maker() as session:
        rows = await session.execute(
            select(Group.chat_id)
            .where(
                Group.is_present.is_(True),
                (Group.admins_checked_at.is_(None)) | (Group.admins_checked_at < older_than),
            )
            .order_by(Group.admins_checked_at)
            .limit(limit)
        )
        return list(rows.scalars())


# ---------------------------------------------------------------------------
# Xabarlar
# ---------------------------------------------------------------------------


async def save_message(
    *,
    chat_id: int,
    message_id: int,
    sender_id: int | None,
    sender_username: str | None,
    sender_name: str | None,
    is_staff: bool,
    text: str | None,
    media_type: str,
    file_id: str | None,
    reply_to_message_id: int | None,
    sent_at: datetime,
    edited_at: datetime | None = None,
    only_update: bool = False,
    media_duration: int | None = None,
) -> bool:
    """Xabarni yozadi. Shu xabar bazada bor bo'lsa (tahrirlangan) -- matnini yangilaydi.
    `only_update` -- bazada yo'q xabar yangi qilib yozilmaydi. Yangi xabar yozilgan bo'lsa True."""
    async with session_maker() as session:
        for _attempt in range(2):
            existing = (
                await session.execute(
                    select(Message).where(Message.chat_id == chat_id, Message.message_id == message_id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.text = text
                existing.media_type = media_type
                existing.edited_at = edited_at or existing.edited_at
                await session.commit()
                return False
            if only_update:
                return False
            session.add(
                Message(
                    chat_id=chat_id, message_id=message_id, sender_id=sender_id, sender_username=sender_username,
                    sender_name=sender_name, is_staff=is_staff, text=text, media_type=media_type, file_id=file_id,
                    media_duration=media_duration, reply_to_message_id=reply_to_message_id, sent_at_utc=sent_at,
                    local_date=local_day(sent_at), edited_at=edited_at, status=STAFF if is_staff else PENDING,
                )
            )
            try:
                await session.commit()
                return True
            except IntegrityError:  # xabar va uning tahriri bir vaqtda keldi
                await session.rollback()
        return False


async def pending_messages(limit: int, day: date | None = None) -> list[Message]:
    """Tasnif kutayotgan mijoz xabarlari: guruh bo'yicha, kelgan tartibida (`day` berilsa --
    faqat shu hisobot kuninikini)."""
    statement = select(Message).where(Message.status == PENDING).order_by(Message.chat_id, Message.message_id)
    if day is not None:
        statement = statement.where(_in_period(Message.sent_at_utc, day))
    async with session_maker() as session:
        return list((await session.execute(statement.limit(limit))).scalars())


async def pending_count(day: date) -> int:
    async with session_maker() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(Message)
                .where(Message.status == PENDING, _in_period(Message.sent_at_utc, day))
            )
        ).scalar_one()


async def set_transcript(message_pk: int, transcript: str) -> None:
    """Ovozli xabar nutqining matnini yozadi ("" -- nutq topilmadi yoki aylantirib bo'lmadi)."""
    async with session_maker() as session:
        await session.execute(update(Message).where(Message.id == message_pk).values(transcript=transcript))
        await session.commit()


async def conversation(chat_id: int, first_message_id: int, last_message_id: int, context: int) -> list[Message]:
    """`first..last` oralig'idagi hamma xabar (xodimlarniki ham) va undan oldingi `context` ta
    xabar, kelgan tartibida -- tasniflovchiga suhbat konteksti sifatida."""
    async with session_maker() as session:
        before = (
            await session.execute(
                select(Message)
                .where(Message.chat_id == chat_id, Message.message_id < first_message_id)
                .order_by(Message.message_id.desc())
                .limit(context)
            )
        ).scalars().all()
        between = (
            await session.execute(
                select(Message)
                .where(
                    Message.chat_id == chat_id,
                    Message.message_id >= first_message_id,
                    Message.message_id <= last_message_id,
                )
                .order_by(Message.message_id)
            )
        ).scalars().all()
    return list(reversed(before)) + list(between)


@dataclass(frozen=True)
class ClassificationResult:
    message_pk: int
    is_company_related: bool
    category: str | None
    sentiment: str | None
    summary: str | None
    needs_manager: bool


async def save_classifications(results: list[ClassificationResult], model: str) -> list[int]:
    """Tasnif natijalarini yozadi va xabarlarni "done" qiladi. Har xabar faqat bir marta
    tasniflanadi: allaqachon tasniflangani o'tkazib yuboriladi. Yozilganlarning ID'larini qaytaradi."""
    saved: list[int] = []
    now = utc_now()
    async with session_maker() as session:
        for result in results:
            message = await session.get(Message, result.message_pk)
            if message is None or message.status == DONE:
                continue
            try:
                # Savepoint: shu xabarni boshqa jarayon (masalan qo'lda ishga tushirilgan hisobot)
                # bir vaqtda tasniflab ulgurgan bo'lsa, faqat shu bittasi o'tkazib yuboriladi
                async with session.begin_nested():
                    session.add(
                        Classification(
                            message_ref=result.message_pk, is_company_related=result.is_company_related,
                            category=result.category, sentiment=result.sentiment, summary=result.summary,
                            needs_manager=result.needs_manager, model=model, classified_at=now,
                        )
                    )
                    message.status = DONE
            except IntegrityError:
                await session.refresh(message)
                continue
            saved.append(result.message_pk)
        await session.commit()
    return saved


async def record_failed_attempt(message_pks: list[int], max_attempts: int) -> None:
    """Tasniflab bo'lmagan xabarlarning urinishlar sonini oshiradi; `max_attempts`ga yetgani
    "failed" bo'ladi va boshqa urinilmaydi."""
    if not message_pks:
        return
    async with session_maker() as session:
        await session.execute(
            update(Message).where(Message.id.in_(message_pks)).values(attempts=Message.attempts + 1)
        )
        await session.execute(
            update(Message)
            .where(Message.id.in_(message_pks), Message.attempts >= max_attempts)
            .values(status=FAILED)
        )
        await session.commit()


# ---------------------------------------------------------------------------
# 5 daqiqa qoidasi
# ---------------------------------------------------------------------------


def _manager_message(group: Group | None):
    """Shu guruhda "menejer javobi" hisoblanadigan xabar sharti: menejer(lar)ning xabari;
    guruhga menejer belgilanmagan bo'lsa -- istalgan xodim (admin) xabari."""
    manager_ids = group.manager_ids if group else ()
    return Message.sender_id.in_(manager_ids) if manager_ids else Message.is_staff.is_(True)


def answers_query(
    chat_id: int, first_message_id: int, asker_id: int | None, group: Group | None, *, anyone: bool | None = None
):
    """So'rovdan keyingi, unga JAVOB hisoblanadigan xabarlar (eng oldingisi birinchi).

    `anyone=True` (standart, `SLA_REQUIRE_MANAGER=false`) -- mijozga kimdir javob bergan bo'lsa
    yetarli: so'ragan mijozning o'zidan boshqa odamning xabari va u (a) xodim xabari, yoki (b) so'ragan
    mijoz xabariga reply, yoki (c) tasniflangan va o'zi so'rov bo'lmagan matnli xabar (boshqa mijozning
    yangi savoli, stiker yoki "+" javob emas -- reply qilinmagan bo'lsa).
    `anyone=False` -- faqat menejer xabari (guruhda menejer bo'lmasa -- istalgan xodim xabari)."""
    if anyone is None:
        anyone = not settings.SLA_REQUIRE_MANAGER
    statement = select(Message).where(Message.chat_id == chat_id, Message.message_id > first_message_id)
    if not anyone:
        return statement.where(_manager_message(group)).order_by(Message.message_id)
    replied = aliased(Message)
    return (
        statement.outerjoin(Classification, Classification.message_ref == Message.id)
        .outerjoin(replied, and_(replied.chat_id == Message.chat_id, replied.message_id == Message.reply_to_message_id))
        .where(
            Message.sender_id != asker_id,
            or_(
                Message.is_staff.is_(True),
                replied.sender_id == asker_id,
                and_(Classification.model != "rule", Classification.needs_manager.is_(False)),
            ),
        )
        .order_by(Message.message_id)
    )


async def _asker_id(session: AsyncSession, check: "ManagerCheck") -> int | None:
    first = await session.get(Message, check.message_ref)
    return first.sender_id if first is not None else None


async def open_check(message_pk: int, sla_minutes: int) -> bool:
    """Javob kerak bo'lgan mijoz xabari uchun tekshiruv ochadi. Shu guruhda muddati tugamagan
    tekshiruv bo'lsa (va undan keyin hali javob yozilmagan bo'lsa) yangi ochilmaydi -- ketma-ket
    kelgan so'rovlar bitta tekshiruvga birlashadi, muddat birinchi xabardan hisoblanadi.
    Yangi tekshiruv ochilsa True."""
    async with session_maker() as session:
        message = await session.get(Message, message_pk)
        if message is None:
            return False
        group = await session.get(Group, message.chat_id)
        current = (
            await session.execute(
                select(ManagerCheck)
                .where(
                    ManagerCheck.chat_id == message.chat_id,
                    ManagerCheck.first_message_id <= message.message_id,
                    ManagerCheck.deadline >= message.sent_at_utc,
                )
                .order_by(ManagerCheck.first_message_id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if current is not None:
            asker = await _asker_id(session, current)
            answered_in_between = (
                await session.execute(
                    answers_query(message.chat_id, current.first_message_id, asker, group)
                    .where(Message.message_id < message.message_id)
                    .limit(1)
                )
            ).first()
            if not answered_in_between:
                return False  # shu so'rovlar bitta tekshiruv
        session.add(
            ManagerCheck(
                message_ref=message.id, chat_id=message.chat_id, first_message_id=message.message_id,
                manager_username=group.manager_username if group else None,
                started_at=message.sent_at_utc, deadline=message.sent_at_utc + timedelta(minutes=sla_minutes),
                status=CHECK_PENDING, local_date=message.local_date,
            )
        )
        await session.commit()
        return True


# Muddat ichida boshqalar yozgan xabar hali tasniflanmagan bo'lsa (u javobmi yoki yangi savolmi --
# noma'lum), tekshiruv shuncha vaqtgacha yopilmay kutadi
_UNCLASSIFIED_WAIT = timedelta(minutes=15)


async def _others_unclassified(session: AsyncSession, check: "ManagerCheck", asker_id: int | None) -> bool:
    row = await session.execute(
        select(Message.id)
        .where(
            Message.chat_id == check.chat_id,
            Message.message_id > check.first_message_id,
            Message.sent_at_utc <= check.deadline,
            Message.sender_id != asker_id,
            Message.status == PENDING,
        )
        .limit(1)
    )
    return row.first() is not None


async def resolve_checks(now: datetime | None = None, grace_seconds: int = 15) -> tuple[int, int]:
    """Kutayotgan tekshiruvlarni yopadi: muddat ichida javob yozilgan bo'lsa -- "answered",
    muddat o'tib ketgan bo'lsa -- "missed". (answered, missed) sonlarini qaytaradi.
    `grace_seconds` -- muddat tugashi bilan kelgan javob bazaga yozilib ulgurishi uchun."""
    now = now or utc_now()
    answered = missed = 0
    async with session_maker() as session:
        checks = (
            await session.execute(select(ManagerCheck).where(ManagerCheck.status == CHECK_PENDING))
        ).scalars().all()
        for check in checks:
            group = await session.get(Group, check.chat_id)
            asker = await _asker_id(session, check)
            reply = (
                await session.execute(
                    answers_query(check.chat_id, check.first_message_id, asker, group)
                    .where(Message.sent_at_utc <= check.deadline)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if reply is not None:
                check.status, check.answered_at = CHECK_ANSWERED, reply.sent_at_utc
                answered += 1
            elif now >= check.deadline + timedelta(seconds=grace_seconds):
                if (
                    not settings.SLA_REQUIRE_MANAGER
                    and now < check.deadline + _UNCLASSIFIED_WAIT
                    and await _others_unclassified(session, check, asker)
                ):
                    continue
                check.status = CHECK_MISSED
                check.manager_username = group.manager_username if group else None
                missed += 1
        await session.commit()
    return answered, missed


@dataclass(frozen=True)
class MissedRequest:
    at: datetime  # mahalliy vaqt
    chat_id: int
    group: str
    customer: str
    summary: str
    manager: str | None  # None -- guruhda menejer yo'q
    # Muddatdan keyin bo'lsa ham kimdir javob berdimi: "Ism (@username), 25 daqiqadan keyin"; None -- yo'q
    later_answer: str | None = None


def _after_text(delta: timedelta) -> str:
    """"25 daqiqadan keyin", "2 soat 10 daqiqadan keyin"."""
    minutes = max(1, math.ceil(delta.total_seconds() / 60))
    if minutes < 60:
        return f"{minutes} daqiqadan keyin"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} soat {minutes} daqiqadan keyin" if minutes else f"{hours} soatdan keyin"


def _sender_label(message: Message) -> str:
    name = message.sender_name or ""
    if message.sender_username:
        name = f"{name} (@{message.sender_username})" if name else f"@{message.sender_username}"
    return name or str(message.sender_id or "")


async def missed_requests(day: date, chat_id: int | None = None) -> list[MissedRequest]:
    """`day` kuni (va `chat_id` berilsa -- shu guruhda) javobsiz qolgan so'rovlar. Har biri uchun
    muddatdan keyin bo'lsa ham kimdir (menejer bo'lishi shart emas) javob berganmi -- shu ham aniqlanadi."""
    statement = (
        select(ManagerCheck, Message, Classification.summary, Group)
        .join(Message, Message.id == ManagerCheck.message_ref)
        .outerjoin(Classification, Classification.message_ref == Message.id)
        .outerjoin(Group, Group.chat_id == ManagerCheck.chat_id)
        .where(_in_period(ManagerCheck.started_at, day), ManagerCheck.status == CHECK_MISSED)
        .order_by(ManagerCheck.started_at)
    )
    if chat_id is not None:
        statement = statement.where(ManagerCheck.chat_id == chat_id)
    requests: list[MissedRequest] = []
    async with session_maker() as session:
        for check, message, summary, group in (await session.execute(statement)).all():
            later = (
                await session.execute(
                    answers_query(check.chat_id, check.first_message_id, message.sender_id, group, anyone=True).limit(1)
                )
            ).scalar_one_or_none()
            later_text = None
            if later is not None:
                later_text = f"{_sender_label(later)}, {_after_text(later.sent_at_utc - check.started_at)}"
            requests.append(
                MissedRequest(
                    at=check.started_at.astimezone(local_tz()),
                    chat_id=check.chat_id,
                    group=(group.title if group else None) or str(check.chat_id),
                    customer=_sender_label(message),
                    summary=summary or message.full_text[:300],
                    manager=check.manager_username,
                    later_answer=later_text,
                )
            )
    return requests


# ---------------------------------------------------------------------------
# Kunlik statistika va xulosalar
# ---------------------------------------------------------------------------


@dataclass
class CategoryStat:
    category: str
    total: int = 0
    complaints: int = 0
    positive: int = 0
    neutral: int = 0
    share: int | None = None  # butun foiz


@dataclass
class DayStats:
    day: date
    categories: list[CategoryStat] = field(default_factory=list)
    filtered: int = 0  # kompaniyaga aloqasiz mijoz xabarlari
    unclassified: int = 0  # tasniflab bo'lmagan (pending/failed) mijoz xabarlari
    missed: int = 0  # javobsiz qolgan so'rovlar

    @property
    def total(self) -> int:
        return sum(row.total for row in self.categories)

    @property
    def complaints(self) -> int:
        return sum(row.complaints for row in self.categories)

    @property
    def positive(self) -> int:
        return sum(row.positive for row in self.categories)

    @property
    def neutral(self) -> int:
        return sum(row.neutral for row in self.categories)


async def compute_day_stats(day: date, chat_id: int | None = None) -> DayStats:
    """`day` uchun barcha sonlarni SQL bilan hisoblaydi (faqat mijoz xabarlari). `chat_id`
    berilsa -- faqat shu guruh bo'yicha."""
    count_if = lambda condition: func.coalesce(func.sum(case((condition, 1), else_=0)), 0)  # noqa: E731
    customer = [_in_period(Message.sent_at_utc, day), Message.is_staff.is_(False)]
    checks = [_in_period(ManagerCheck.started_at, day), ManagerCheck.status == CHECK_MISSED]
    if chat_id is not None:
        customer.append(Message.chat_id == chat_id)
        checks.append(ManagerCheck.chat_id == chat_id)
    async with session_maker() as session:
        rows = (
            await session.execute(
                select(
                    Classification.category,
                    func.count(),
                    count_if(Classification.sentiment == COMPLAINT),
                    count_if(Classification.sentiment == POSITIVE),
                    count_if(Classification.sentiment == NEUTRAL),
                )
                .join(Message, Message.id == Classification.message_ref)
                .where(*customer, Classification.is_company_related.is_(True))
                .group_by(Classification.category)
            )
        ).all()
        filtered = (
            await session.execute(
                select(func.count())
                .select_from(Classification)
                .join(Message, Message.id == Classification.message_ref)
                .where(*customer, Classification.is_company_related.is_(False))
            )
        ).scalar_one()
        unclassified = (
            await session.execute(
                select(func.count()).select_from(Message).where(*customer, Message.status.in_((PENDING, FAILED)))
            )
        ).scalar_one()
        missed = (await session.execute(select(func.count()).select_from(ManagerCheck).where(*checks))).scalar_one()

    by_category = {category: CategoryStat(category) for category in CATEGORIES}
    for category, total, complaints, positive, neutral in rows:
        # Ro'yxatda yo'q nom (bo'lmasligi kerak) "Boshqa"ga qo'shiladi
        stat = by_category.get(category) or by_category["Boshqa"]
        stat.total += total
        stat.complaints += complaints
        stat.positive += positive
        stat.neutral += neutral
    stats = DayStats(day, list(by_category.values()), filtered=filtered, unclassified=unclassified, missed=missed)
    for stat in stats.categories:
        if stat.complaints + stat.positive + stat.neutral != stat.total:
            logger.error(
                "%s: Shikoyat+Ijobiy+Neytral (%s) Jami (%s) ga teng emas", stat.category,
                stat.complaints + stat.positive + stat.neutral, stat.total,
            )
            stat.neutral = stat.total - stat.complaints - stat.positive
        stat.share = round(stat.total * 100 / stats.total) if stats.total else None
    return stats


async def save_day_stats(stats: DayStats) -> None:
    """Kunlik sonlarni `daily_stats`ga yozadi (qayta hisoblansa -- yangilaydi)."""
    rows = [
        (stat.category, stat.total, stat.complaints, stat.positive, stat.neutral, stat.share)
        for stat in stats.categories
    ]
    rows.append((STAT_TOTAL, stats.total, stats.complaints, stats.positive, stats.neutral, 100 if stats.total else None))
    rows.append((STAT_FILTERED, stats.filtered, 0, 0, 0, None))
    rows.append((STAT_MISSED, stats.missed, 0, 0, 0, None))
    async with session_maker() as session:
        existing = {
            row.category: row
            for row in (await session.execute(select(DailyStat).where(DailyStat.date == stats.day))).scalars()
        }
        for category, total, complaints, positive, neutral, share in rows:
            row = existing.get(category)
            if row is None:
                row = DailyStat(date=stats.day, category=category)
                session.add(row)
            row.total, row.complaints, row.positive, row.neutral, row.share = total, complaints, positive, neutral, share
        await session.commit()


async def stored_complaints(day: date) -> int | None:
    """`day` uchun saqlangan shikoyatlar soni (o'sha kun hisoblanmagan bo'lsa None)."""
    async with session_maker() as session:
        return (
            await session.execute(
                select(DailyStat.complaints).where(DailyStat.date == day, DailyStat.category == STAT_TOTAL)
            )
        ).scalar_one_or_none()


@dataclass(frozen=True)
class CategoryMessage:
    group: str
    sentiment: str
    summary: str
    text: str


async def category_messages(
    day: date, category: str, limit: int, chat_id: int | None = None
) -> list[CategoryMessage]:
    """Toifaning shu kungi xabarlari (xulosa yozish uchun): avval shikoyatlar, keyin ijobiy, keyin
    neytral. `chat_id` berilsa -- faqat shu guruhniki."""
    order = case((Classification.sentiment == COMPLAINT, 0), (Classification.sentiment == POSITIVE, 1), else_=2)
    statement = (
        select(
            Group.title, Classification.sentiment, Classification.summary,
            func.coalesce(Message.text, Message.transcript), Message.chat_id,
        )
        .join(Message, Message.id == Classification.message_ref)
        .outerjoin(Group, Group.chat_id == Message.chat_id)
        .where(
            _in_period(Message.sent_at_utc, day), Message.is_staff.is_(False),
            Classification.is_company_related.is_(True), Classification.category == category,
        )
        .order_by(order, Message.sent_at_utc)
        .limit(limit)
    )
    if chat_id is not None:
        statement = statement.where(Message.chat_id == chat_id)
    async with session_maker() as session:
        rows = (await session.execute(statement)).all()
    return [
        CategoryMessage(title or str(chat_id), sentiment or NEUTRAL, summary or "", text or "")
        for title, sentiment, summary, text, chat_id in rows
    ]


async def save_summary(day: date, category: str, text: str) -> None:
    async with session_maker() as session:
        row = (
            await session.execute(
                select(DailySummary).where(DailySummary.date == day, DailySummary.category == category)
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(DailySummary(date=day, category=category, summary_text=text))
        else:
            row.summary_text = text
        await session.commit()


async def mark_report_sent(day: date) -> None:
    async with session_maker() as session:
        row = (
            await session.execute(
                select(DailySummary).where(DailySummary.date == day, DailySummary.category == SUMMARY_OVERALL)
            )
        ).scalar_one_or_none()
        if row is None:
            row = DailySummary(date=day, category=SUMMARY_OVERALL, summary_text="")
            session.add(row)
        row.report_sent_at = utc_now()
        await session.commit()


async def sent_days(today: date, lookback_days: int = 7) -> set[date]:
    """Oxirgi `lookback_days` kun ichida hisoboti yuborilgan kunlar."""
    since = today - timedelta(days=lookback_days)
    async with session_maker() as session:
        rows = await session.execute(
            select(DailySummary.date).where(
                DailySummary.category == SUMMARY_OVERALL, DailySummary.report_sent_at.is_not(None),
                DailySummary.date >= since,
            )
        )
        return set(rows.scalars())


async def unsent_days(today: date, lookback_days: int = 7) -> list[date]:
    """`today`dan oldingi, xabari bor, lekin hisoboti yuborilmagan hisobot kunlari (eskisidan boshlab)."""
    sent = await sent_days(today, lookback_days)
    days: list[date] = []
    async with session_maker() as session:
        for offset in range(lookback_days, 0, -1):
            day = today - timedelta(days=offset)
            if day in sent:
                continue
            has_messages = (
                await session.execute(select(Message.id).where(_in_period(Message.sent_at_utc, day)).limit(1))
            ).first()
            if has_messages:
                days.append(day)
    return days


# ---------------------------------------------------------------------------
# Zaxira nusxa
# ---------------------------------------------------------------------------


def _backup_path(backup_dir: str, day: date | None = None) -> Path:
    return Path(backup_dir) / f"monitor_{(day or local_day()).isoformat()}.db"


def backup_exists(day: date | None = None) -> bool:
    """Shu kunning (standart -- bugungi) zaxira nusxasi bormi."""
    return _backup_path(settings.BACKUP_DIR, day).exists()


def backup_database(backup_dir: str, keep: int, day: date | None = None) -> Path:
    """SQLite backup API bilan bazaning to'liq nusxasini oladi (bot ishlab turganda ham xavfsiz)
    va eng yangi `keep` tasini qoldirib, eskilarini o'chiradi. Kuniga bitta fayl:
    `monitor_YYYY-MM-DD.db`. Bloklovchi -- `asyncio.to_thread` orqali chaqiring."""
    folder = Path(backup_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = _backup_path(backup_dir, day)
    source = sqlite3.connect(settings.DB_PATH)
    destination = sqlite3.connect(target)
    try:
        with destination:
            source.backup(destination)
    finally:
        destination.close()
        source.close()
    for old in sorted(folder.glob("monitor_*.db"), reverse=True)[keep:]:
        old.unlink(missing_ok=True)
    return target
