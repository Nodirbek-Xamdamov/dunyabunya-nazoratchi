"""
Mijoz guruhlarini kuzatish (2026-10-07; 8 ta toifa, statistika va rasm tahlili -- 2026-10-12).

Bot mijozlar bor guruhlarga qo'shiladi va u yerda FAQAT O'QIYDI. Natijalar ichki (xodimlar)
forum-guruhiga (`FEEDBACK_HUB_GROUP_ID`) boradi:

  1. Har bir mijoz xabari tasniflanadi (`app/ai_processor.py` -> `app/group_ai.py`): matn va
     rasm -- asosiy model, ovozli xabar/audio/video -- avval matnga o'giriladi.
     Kompaniyaga aloqasi yo'q oddiy suhbat (yozishmalarning ko'pi) butunlay tashlab yuboriladi:
     bazaga yozilmaydi, hech qayerga tashlanmaydi, faqat soni sanaladi.
  2. Kompaniyaga oid xabar 8 ta toifadan biriga tushadi (`group_ai.CATEGORIES`) va
     `message_logs`ga yoziladi -- kunlik hisobot shundan yig'iladi (`app/daily_report.py`).
  3. Mijoz SHIKOYAT qilsa yoki IJOBIY FIKR bildirsa -- xabar ichki guruhning shu toifaga mos
     topigiga BIR MARTA tashlanadi: guruh nomi, kim yozgani, xabarning o'zi (matn/media) va
     xabarga havola. Kimdir "tovar yo'q ekan" desa -- "Tovar yo'q" topigiga.
  4. Mijoz savol/so'rov yozsa va javobsiz qolsa -- ogohlantirish (`GROUP_ALERT_MODE`):
       "dm"    -- xodimning shaxsiy chatiga (pastdagi `StaffReplyWait` qismi): mijoz aniq
                  xodimga murojaat qilgan bo'lsa (reply / @username) o'sha xodimga, hech kimga
                  qaratilmagan savol bo'lsa guruh menejer(lar)iga. Bot mijoz guruhiga hech
                  narsa yozmaydi.
       "group" -- guruhning o'ziga, menejerlarni belgilab (`app/group_timers.py`). Bot mijoz
                  guruhiga yozadigan yagona xabar shu.
     Ikkala rejimda ham muddat `STAFF_REPLY_TIMEOUT_MINUTES` (5) daqiqa; xodim "10 minutda
     javob beraman" desa 10 daqiqa kutiladi; muddat ichida javob yozilsa ogohlantirish bo'lmaydi.

"dunyabunya xodimi" = ichki guruh a'zosi (`is_staff_member`) yoki shu guruhga biriktirilgan
menejer (`app/group_registry.py`). Guruh menejeri = guruhda admin unvoniga "Menejer" deb yozib
qo'yilgan ichki guruh a'zosi yoki `/menejer_qosh` bilan qo'lda biriktirilgan odam.

Bot guruhdagi oddiy xabarlarni ko'rishi uchun shu guruhda ADMIN bo'lishi (yoki BotFather'da
privacy mode o'chirilgan bo'lishi) shart -- `group_mentions.py` izohiga qarang.

Bu router ENG BIRINCHI ro'yxatdan o'tadi va oxirida `SkipHandler` tashlaydi, shu sabab
keyingi handlerlar (masalan `group_mentions`) o'sha xabarni odatdagidek qayta ishlaydi.
Bitta guruhning xabarlari navbat bilan (kelgan tartibida), turli guruhlarniki parallel
qayta ishlanadi.
"""

import asyncio
import html
import logging
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Chat, Message, User
from sqlalchemy import delete, select

from app import ai_processor, group_registry, group_stats, group_timers
from app.config import settings
from app.database import async_session_maker
from app.group_ai import CATEGORIES
from app.group_registry import ManagerInfo
from app.models import HubTopic, StaffMember, StaffReplyWait, TelegramUser
from app.utils import normalize_username

logger = logging.getLogger("aiogram_bot")

router = Router(name="group_monitor")

_GROUP_CHAT_TYPES = {"group", "supergroup"}
_PRESENT_STATUSES = {"creator", "administrator", "member"}


@dataclass(frozen=True)
class _TopicKind:
    emoji: str
    name: str  # topikning asl nomi: bot topikni o'zi yaratsa shu nom bilan yaratadi
    needles: tuple[str, ...]  # qo'lda boshqacha nomlangan topikni tanish uchun bo'laklar


_TOPIC_KINDS: dict[str, _TopicKind] = {
    "delivery": _TopicKind("🚚", CATEGORIES["delivery"], ("dastavka", "yetkaz", "logist", "достав", "логист", "етказ")),
    "quality": _TopicKind("🧱", CATEGORIES["quality"], ("sifat", "nuqson", "brak", "сифат", "качеств", "брак")),
    "completeness": _TopicKind("🧮", CATEGORIES["completeness"], ("komplekt", "hajm", "комплект", "ҳажм")),
    "staff": _TopicKind("🧑‍💼", CATEGORIES["staff"], ("xodim", "xizmat", "ходим", "хизмат", "сотрудник", "обслуж")),
    "price": _TopicKind("💰", CATEGORIES["price"], ("narx", "to'lov", "tolov", "нарх", "тўлов", "цен", "оплат")),
    "returns": _TopicKind("↩️", CATEGORIES["returns"], ("qaytar", "kafolat", "қайтар", "кафолат", "возврат", "гарант")),
    "positive": _TopicKind("🟢", CATEGORIES["positive"], ("ijobiy", "ижобий", "sharh", "шарҳ", "отзыв")),
    "other": _TopicKind("📌", CATEGORIES["other"], ("boshqa", "бошқа", "прочее", "другое")),
    "stock": _TopicKind("📦", "Tovar yo'q", ("tovar", "товар", "mahsulot", "маҳсулот")),
    "unanswered": _TopicKind("⏰", "Javobsiz savollar", ("javobsiz", "жавобсиз", "без ответа")),
    # Umumiy shikoyatlar topigi: toifaning o'z topigi topilmasa (va yaratib bo'lmasa) shikoyat shu yerga tushadi
    "complaint": _TopicKind("🔴", "Shikoyatlar", ("shik", "шикоят", "жалоб")),
}
# Shikoyat tushishi mumkin bo'lgan toifalar -- o'z topigi bo'lmasa umumiy "Shikoyatlar"ga
_COMPLAINT_KINDS = ("delivery", "quality", "completeness", "staff", "price", "returns", "other")

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "`": "'", "ʻ": "'", "ʼ": "'"})

_SERVICE_CONTENT_TYPES = {
    "new_chat_members", "left_chat_member", "new_chat_title", "new_chat_photo", "delete_chat_photo",
    "group_chat_created", "supergroup_chat_created", "pinned_message", "migrate_to_chat_id",
    "migrate_from_chat_id", "forum_topic_created", "forum_topic_edited", "forum_topic_closed",
    "forum_topic_reopened", "video_chat_started", "video_chat_ended", "video_chat_scheduled",
    "video_chat_participants_invited", "message_auto_delete_timer_changed", "boost_added",
}
# Izoh (caption) bilan nusxalab bo'ladigan media turlari
_CAPTION_MEDIA = ("photo", "video", "voice", "audio", "document", "animation")

# Har bir guruhdagi oxirgi xabarlar (xotirada) -- model xabar kimga va nima haqida
# ekanini (kompaniya haqidami yoki mijozlarning o'zaro oddiy suhbatimi) tushunishi uchun
# tasniflash so'roviga kontekst sifatida qo'shiladi. Bazaga yozilmaydi.
_CONTEXT_MESSAGES = 8
_CONTEXT_TEXT_LIMIT = 300
_recent_messages: dict[int, deque[str]] = {}

_TRANSCRIPT_PREVIEW_LIMIT = 700
_QUOTE_LIMIT = 3000
_CAPTION_LIMIT = 1000  # Telegram chegarasi 1024 belgi

# group_id -> shu guruhda xodim oxirgi marta qachon yozgani. Mijozning ovozli xabari matnga
# o'girilayotgan bir necha soniya ichida xodim javob berib ulgursa, kechikib ochilgan
# "javob kutish" yolg'on eslatmaga aylanmasligi uchun kerak.
_last_staff_message_at: dict[int, datetime] = {}

# user_id -> (xodimmi, qachongacha amal qiladi) -- har xabarda getChatMember chaqirmaslik uchun
_STAFF_CACHE_TTL_SECONDS = 600
_staff_cache: dict[int, tuple[bool, float]] = {}

# group_id -> ({menejer chat_id: username}, qachongacha amal qiladi)
_managers_cache: dict[int, tuple[dict[int, str | None], float]] = {}

# Albom (bir nechta rasm/video bitta xabar bo'lib): Telegram har bir faylni alohida xabar
# qilib beradi, izoh faqat birinchisida. Faqat birinchisi tahlil qilinadi; qolganlari shu
# yerdagi yozuv bo'yicha (birinchisi tashlangan topiklarga) izohsiz qo'shib qo'yiladi.
_ALBUMS_LIMIT = 300
_albums: OrderedDict[str, list[str]] = OrderedDict()

# (guruh, xabar, topik turi) -- bitta xabar bitta topikka ikki marta tashlanmasligi uchun
_FORWARDED_LIMIT = 5000
_forwarded: OrderedDict[tuple[int, int, str], None] = OrderedDict()

# Bitta guruhning xabarlari navbat bilan qayta ishlanadi (aiogram har bir xabarni alohida
# vazifada ishga tushiradi; navbatsiz qolsa uzoq tahlil qilinayotgan ovozli savoldan keyin
# kelgan xodim javobi undan OLDIN ko'rib chiqilib, kontekst va javob kutish chalkashardi)
_chat_locks: dict[int, asyncio.Lock] = {}

_topic_lock = asyncio.Lock()
_TOPIC_CREATE_RETRY_SECONDS = 1800
_topic_create_blocked_until = 0.0


def reset_state() -> None:
    """Xotiradagi barcha keshlarni tozalaydi (testlar uchun)."""
    global _topic_create_blocked_until
    _topic_create_blocked_until = 0.0
    for cache in (
        _recent_messages, _last_staff_message_at, _staff_cache, _managers_cache, _albums, _forwarded, _chat_locks,
    ):
        cache.clear()


def _excluded_group_ids() -> set[int]:
    return {int(part) for part in settings.GROUP_MONITOR_EXCLUDED_GROUP_IDS.replace(" ", "").split(",") if part}


def _topic_override(kind: str) -> int | None:
    """`.env`da qo'lda belgilangan topik: `HUB_TOPIC_IDS` ("delivery=12,price=14") yoki eski
    alohida o'zgaruvchilar."""
    for pair in settings.HUB_TOPIC_IDS.replace(" ", "").split(","):
        key, _, value = pair.partition("=")
        if key == kind and value.isdigit():
            return int(value)
    return {
        "complaint": settings.HUB_TOPIC_COMPLAINTS_ID,
        "positive": settings.HUB_TOPIC_PRAISE_ID,
        "stock": settings.HUB_TOPIC_STOCK_ID,
    }.get(kind)


def get_replied_message(message: Message) -> Message | None:
    """Xabar HAQIQATAN reply qilingan xabarni qaytaradi. Forum-guruhlarda Telegram topik
    ichidagi har bir oddiy xabarga topikni yaratgan servis xabarini reply sifatida qo'shib
    beradi -- u reply hisoblanmaydi."""
    replied = message.reply_to_message
    if replied is None or replied.forum_topic_created:
        return None
    return replied


def build_message_link(chat: Chat, message_id: int) -> str | None:
    """Guruhdagi xabarga havola. Oddiy (supergroup bo'lmagan) guruhlarda havola mavjud emas."""
    if chat.username:
        return f"https://t.me/{chat.username}/{message_id}"
    chat_id = str(chat.id)
    if chat_id.startswith("-100"):
        return f"https://t.me/c/{chat_id[4:]}/{message_id}"
    return None


def take_context(message: Message, is_staff: bool, text: str = "") -> str:
    """Shu guruhdagi oldingi xabarlarni matn ko'rinishida qaytaradi va joriy xabarni
    (`text` -- yozilgan matni yoki ovozli xabarning matnga o'girilgani) keyingilar uchun
    eslab qoladi."""
    history = _recent_messages.setdefault(message.chat.id, deque(maxlen=_CONTEXT_MESSAGES))
    context = "\n".join(history)

    text = text.strip()
    if text:
        role = "xodim" if is_staff else "mijoz"
        line = f"[{role}] {message.from_user.full_name}"
        replied = get_replied_message(message)
        if replied and replied.from_user:
            line += f" ({replied.from_user.full_name}ga javoban)"
        history.append(f"{line}: {text[:_CONTEXT_TEXT_LIMIT]}")
    return context


def remember_image_note(message: Message, note: str) -> None:
    """Rasmda nima ko'ringanini keyingi xabarlar konteksti uchun eslab qoladi ("mana qarang,
    shunaqa kelibdi" degan keyingi xabar aynan shu rasm haqida bo'ladi)."""
    history = _recent_messages.setdefault(message.chat.id, deque(maxlen=_CONTEXT_MESSAGES))
    history.append(f"[mijoz] {message.from_user.full_name} rasm yubordi: {note}")


# ---------------------------------------------------------------------------
# Xodimlar
# ---------------------------------------------------------------------------


async def remember_staff(user: User) -> None:
    async with async_session_maker() as session:
        normalized = normalize_username(user.username) if user.username else None
        existing = await session.get(StaffMember, user.id)
        if existing:
            existing.normalized_username = normalized
            existing.full_name = user.full_name
        else:
            session.add(StaffMember(chat_id=user.id, normalized_username=normalized, full_name=user.full_name))
        await session.commit()


async def is_staff_member(bot: Bot, user_id: int) -> bool:
    """Foydalanuvchi ichki (xodimlar) guruhi a'zosimi. Natija 10 daqiqa keshlanadi."""
    cached = _staff_cache.get(user_id)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    try:
        member = await bot.get_chat_member(settings.FEEDBACK_HUB_GROUP_ID, user_id)
        is_staff = member.status in _PRESENT_STATUSES or (
            member.status == "restricted" and bool(getattr(member, "is_member", False))
        )
    except TelegramAPIError as exc:
        logger.warning("Xodimligini tekshirib bo'lmadi (user_id=%s): %s", user_id, exc)
        return False
    _staff_cache[user_id] = (is_staff, time.monotonic() + _STAFF_CACHE_TTL_SECONDS)
    return is_staff


async def find_staff_by_username(bot: Bot, username: str) -> int | None:
    """@username -> xodimning chat_id'si. Avval `staff_members`dan, topilmasa botga /start
    bosganlar (`telegram_users`) orasidan qidiradi va ichki guruh a'zoligini tekshiradi."""
    normalized = normalize_username(username)
    async with async_session_maker() as session:
        staff = (
            await session.execute(select(StaffMember).where(StaffMember.normalized_username == normalized))
        ).scalars().first()
        known = (
            await session.execute(select(TelegramUser).where(TelegramUser.normalized_username == normalized))
        ).scalars().first()
    # `staff_members`dagi yozuv eskirgan bo'lishi mumkin (xodim ichki guruhdan chiqib ketgan),
    # shu sabab a'zolik har doim Telegram'dan qayta tekshiriladi.
    candidate_id = staff.chat_id if staff else (known.chat_id if known else None)
    if candidate_id is not None and await is_staff_member(bot, candidate_id):
        return candidate_id
    return None


def forget_group_managers(group_id: int) -> None:
    """Menejerlar qo'lda o'zgartirilgach keshni tozalaydi (`hub_admin`)."""
    _managers_cache.pop(group_id, None)


async def find_group_managers(bot: Bot, group_id: int) -> dict[int, str | None]:
    """Guruhning mas'ul menejer(lar)i: admin unvonida (custom title) "menejer" so'zi bor va
    ichki guruh a'zosi bo'lgan xodimlar, hamda qo'lda biriktirilgan (Telegram ID'si ma'lum)
    menejerlar. {chat_id: username} qaytaradi, 10 daqiqa keshlanadi. Unvon bo'yicha
    topilganlar `group_managers` jadvaliga ham yozib qo'yiladi. Unvon faqat supergroup'larda
    va faqat adminlarga qo'yiladi."""
    cached = _managers_cache.get(group_id)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    keywords = [part.strip().lower() for part in settings.GROUP_MANAGER_TITLE_KEYWORDS.split(",") if part.strip()]
    managers: dict[int, str | None] = {}
    try:
        admins = await bot.get_chat_administrators(group_id)
    except TelegramAPIError as exc:
        # Unvonlarni bilib bo'lmadi -- jadvaldagi (oldin aniqlangan va qo'lda yozilgan) menejerlar qoladi
        logger.warning("Guruh adminlarini olib bo'lmadi (group_id=%s): %s", group_id, exc)
        admins = None
    if admins is not None:
        titled: dict[int, tuple[str | None, str | None]] = {}
        for admin in admins:
            title = (getattr(admin, "custom_title", None) or "").lower()
            if admin.user.is_bot or not any(keyword in title for keyword in keywords):
                continue
            if await is_staff_member(bot, admin.user.id):
                titled[admin.user.id] = (admin.user.username, admin.user.full_name)
                await remember_staff(admin.user)
        await group_registry.sync_title_managers(group_id, titled)
    for manager in await group_registry.list_managers(group_id):
        if manager.user_id is not None:
            managers.setdefault(manager.user_id, manager.username)
    if admins is not None:
        _managers_cache[group_id] = (managers, time.monotonic() + _STAFF_CACHE_TTL_SECONDS)
    return managers


async def get_alert_managers(bot: Bot, group_id: int) -> tuple[ManagerInfo, ...]:
    """Guruhga yoziladigan ogohlantirishda belgilanadigan hamma menejer: unvon bo'yicha
    aniqlanganlar (avval yangilab olinadi) va qo'lda biriktirilganlar."""
    await find_group_managers(bot, group_id)
    return await group_registry.list_managers(group_id)


# ---------------------------------------------------------------------------
# Ichki guruh topiklari
# ---------------------------------------------------------------------------


async def learn_hub_topic(message: Message) -> None:
    """Ichki guruhdagi xabardan topik ID'si va nomini o'rganib `hub_topics`ga yozadi."""
    thread_id = message.message_thread_id
    if not thread_id:
        return

    name: str | None = None
    overwrite = True
    if message.forum_topic_created:
        name = message.forum_topic_created.name
    elif message.forum_topic_edited:
        name = message.forum_topic_edited.name  # faqat ikonka o'zgarsa None
    elif message.reply_to_message and message.reply_to_message.forum_topic_created:
        # Topik ichidagi oddiy xabar: Telegram topikni yaratgan servis xabarini reply
        # sifatida qo'shib beradi. U yerdagi nom -- topik YARATILGAN paytdagi nom, shu
        # sabab keyin o'zgartirilgan (forum_topic_edited) nomni ustidan yozmaymiz.
        name = message.reply_to_message.forum_topic_created.name
        overwrite = False
    if not name:
        return

    async with async_session_maker() as session:
        existing = await session.get(HubTopic, thread_id)
        if existing is None:
            session.add(HubTopic(thread_id=thread_id, name=name))
            logger.info("Ichki guruh topigi aniqlandi: '%s' (thread_id=%s)", name, thread_id)
        elif overwrite and existing.name != name:
            existing.name = name
            logger.info("Ichki guruh topigi nomi o'zgardi: '%s' (thread_id=%s)", name, thread_id)
        await session.commit()


def _normalize_topic_name(name: str) -> str:
    return " ".join(name.translate(_APOSTROPHES).lower().split())


async def get_topic_thread_id(kind: str) -> int | None:
    """`kind` uchun mavjud topik: `.env`da belgilangani, nomi aynan mos keladigani yoki nomida
    tanish bo'lak bor topik. Topik yaratilmaydi."""
    override = _topic_override(kind)
    if override:
        return override
    async with async_session_maker() as session:
        topics = (await session.execute(select(HubTopic).order_by(HubTopic.thread_id))).scalars().all()
    names = {topic.thread_id: _normalize_topic_name(topic.name) for topic in topics}

    wanted = _normalize_topic_name(_TOPIC_KINDS[kind].name)
    for thread_id, name in names.items():
        if name == wanted:
            return thread_id
    # Boshqa toifaning o'z nomi bilan atalgan topik bo'laklar bo'yicha "o'g'irlanmaydi"
    # ("Mahsulot Sifati va Nuqsonlar"dagi "mahsulot" so'zi uni "Tovar yo'q" topigiga aylantirmaydi)
    owned = {_normalize_topic_name(spec.name) for other, spec in _TOPIC_KINDS.items() if other != kind}
    for thread_id, name in names.items():
        if name not in owned and any(needle in name for needle in _TOPIC_KINDS[kind].needles):
            return thread_id
    return None


async def _create_hub_topic(bot: Bot, kind: str) -> int | None:
    """Ichki guruhda `kind` uchun topik yaratadi. Huquq bo'lmasa keyingi urinish yarim soatdan keyin."""
    global _topic_create_blocked_until
    if not settings.HUB_AUTO_CREATE_TOPICS or time.monotonic() < _topic_create_blocked_until:
        return None
    name = _TOPIC_KINDS[kind].name
    try:
        topic = await bot.create_forum_topic(settings.FEEDBACK_HUB_GROUP_ID, name)
    except TelegramAPIError as exc:
        _topic_create_blocked_until = time.monotonic() + _TOPIC_CREATE_RETRY_SECONDS
        logger.warning(
            "'%s' topigini yaratib bo'lmadi (botga ichki guruhda \"Mavzularni boshqarish\" huquqi kerak): %s",
            name, exc,
        )
        return None
    async with async_session_maker() as session:
        await session.merge(HubTopic(thread_id=topic.message_thread_id, name=name))
        await session.commit()
    logger.info("Ichki guruhda topik yaratildi: '%s' (thread_id=%s)", name, topic.message_thread_id)
    return topic.message_thread_id


async def resolve_topic_thread_id(bot: Bot, kind: str) -> int | None:
    """Xabar tashlanadigan topik: mavjudi, bo'lmasa yangi yaratilgani, u ham bo'lmasa shikoyat
    toifalari uchun umumiy "Shikoyatlar" topigi. None -- ichki guruhning umumiy qismi."""
    async with _topic_lock:  # ikki xabar bir vaqtda bitta topikni ikki marta yaratmasligi uchun
        thread_id = await get_topic_thread_id(kind) or await _create_hub_topic(bot, kind)
    if thread_id is None and kind in _COMPLAINT_KINDS:
        thread_id = await get_topic_thread_id("complaint")
    if thread_id is None:
        logger.warning("'%s' uchun topik topilmadi -- xabar ichki guruhning umumiy qismiga yuboriladi", _TOPIC_KINDS[kind].name)
    return thread_id


def _esc(text: str) -> str:
    """Matn tuguni uchun: faqat & < > almashtiriladi (qo'shtirnoq va apostrof o'z holicha qoladi)."""
    return html.escape(text, quote=False)


def _visible_length(text: str) -> int:
    return len(html.unescape(re.sub(r"<[^>]+>", "", text)))


def _quote(text: str) -> str:
    if len(text) > _QUOTE_LIMIT:
        text = text[:_QUOTE_LIMIT] + "…"
    return f"\n<blockquote>{_esc(text)}</blockquote>"


def build_hub_header(
    message: Message, kind: str, *, label: str | None = None, transcript: str | None = None,
    image_note: str | None = None,
) -> str:
    """Topikka tashlanadigan xabarning sarlavhasi: toifa, guruh, kim yozgani, xabarga havola;
    ovozli/video xabar bo'lsa matni, rasm bo'lsa unda nima ko'ringani."""
    spec = _TOPIC_KINDS[kind]
    head = f"<b>{spec.emoji} {_esc(spec.name)}</b>"
    if label:
        head += f" · {label}"
    sender = message.from_user
    who = _esc(sender.full_name)
    if sender.username:
        who += f" (@{sender.username})"
    lines = [head, f"👥 {_esc(message.chat.title or '')}", f"👤 {who}"]
    if link := build_message_link(message.chat, message.message_id):
        lines.append(f'🔗 <a href="{link}">Xabarga o\'tish</a>')
    if image_note:
        lines.append(f"🖼 <i>{_esc(image_note)}</i>")
    if transcript:
        preview = transcript if len(transcript) <= _TRANSCRIPT_PREVIEW_LIMIT else transcript[:_TRANSCRIPT_PREVIEW_LIMIT] + "…"
        lines.append(f"🎙 <i>{_esc(preview)}</i>")
    return "\n".join(lines)


async def forward_to_hub_topic(
    bot: Bot, message: Message, kind: str, *, label: str | None = None, transcript: str | None = None,
    image_note: str | None = None,
) -> bool:
    """Xabarni ichki guruhning mos topigiga BIR MARTA tashlaydi. Iloji boricha bitta xabar
    qilib: matnli xabar -- sarlavha va matn iqtibosi; media -- o'zining nusxasi, sarlavha
    izohida. Izoh sig'masa yoki media izoh ko'tarmasa (dumaloq video) -- sarlavha alohida,
    ortidan medianing nusxasi. Tashlangan bo'lsa True."""
    key = (message.chat.id, message.message_id, kind)
    if key in _forwarded:
        return True
    hub_id = settings.FEEDBACK_HUB_GROUP_ID
    thread_id = await resolve_topic_thread_id(bot, kind)
    header = build_hub_header(message, kind, label=label, transcript=transcript, image_note=image_note)
    send = dict(message_thread_id=thread_id, parse_mode="HTML", disable_web_page_preview=True)
    try:
        if message.text:
            await bot.send_message(hub_id, header + _quote(message.text), **send)
        else:
            caption = header + (_quote(message.caption) if message.caption else "")
            copied = False
            if _visible_length(caption) <= _CAPTION_LIMIT and any(getattr(message, a, None) for a in _CAPTION_MEDIA):
                try:
                    await bot.copy_message(
                        hub_id, message.chat.id, message.message_id, message_thread_id=thread_id,
                        caption=caption, parse_mode="HTML",
                    )
                    copied = True
                except TelegramAPIError as exc:
                    logger.info("Xabarni izoh bilan nusxalab bo'lmadi (%s) -- sarlavha alohida yuboriladi", exc)
            if not copied:
                await bot.send_message(hub_id, caption, **send)
                try:
                    await bot.copy_message(hub_id, message.chat.id, message.message_id, message_thread_id=thread_id)
                except TelegramAPIError as exc:
                    # Guruhda "kontentni himoyalash" yoqilgan -- media ko'chmaydi, sarlavha va havola qoladi
                    logger.info("Xabar nusxasini ichki guruhga yuborib bo'lmadi: %s", exc)
    except TelegramAPIError:
        logger.exception("Xabarni ichki guruhga tashlashda xatolik (%s)", _TOPIC_KINDS[kind].name)
        return False

    _forwarded[key] = None
    if len(_forwarded) > _FORWARDED_LIMIT:
        _forwarded.popitem(last=False)
    album = _albums.get(getattr(message, "media_group_id", None) or "")
    if album is not None and kind not in album:
        album.append(kind)
    return True


async def _forward_album_item(bot: Bot, message: Message, kinds: list[str]) -> None:
    """Albomning keyingi fayli: birinchisi tashlangan topiklarga izohsiz qo'shib qo'yiladi."""
    for kind in kinds:
        try:
            await bot.copy_message(
                settings.FEEDBACK_HUB_GROUP_ID, message.chat.id, message.message_id,
                message_thread_id=await resolve_topic_thread_id(bot, kind),
            )
        except TelegramAPIError as exc:
            logger.info("Albom faylini ichki guruhga yuborib bo'lmadi: %s", exc)


async def get_unanswered_thread_id(bot: Bot) -> int | None:
    return await resolve_topic_thread_id(bot, "unanswered")


# ---------------------------------------------------------------------------
# Xodim javobini kutish, "dm" rejimi (har bir xodim uchun alohida, eslatma shaxsiy chatga)
# ---------------------------------------------------------------------------


def _mentioned_usernames_and_ids(message: Message) -> tuple[list[str], list[int]]:
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    usernames: list[str] = []
    user_ids: list[int] = []
    for entity in entities:
        if entity.type == "mention":
            usernames.append(entity.extract_from(text))
        elif entity.type == "text_mention" and entity.user:
            user_ids.append(entity.user.id)
    return usernames, user_ids


async def find_addressed_staff(bot: Bot, message: Message) -> dict[int, str | None]:
    """Mijoz xabari qaysi xodim(lar)ga qaratilganini aniqlaydi: xodim xabariga reply,
    @username yoki ismga bosiladigan mention. {chat_id: username} qaytaradi."""
    targets: dict[int, str | None] = {}

    replied = get_replied_message(message)
    if replied and replied.from_user and not replied.from_user.is_bot:
        if await is_staff_member(bot, replied.from_user.id):
            targets[replied.from_user.id] = replied.from_user.username

    usernames, user_ids = _mentioned_usernames_and_ids(message)
    for user_id in user_ids:
        if await is_staff_member(bot, user_id):
            targets.setdefault(user_id, None)
    for username in usernames:
        staff_chat_id = await find_staff_by_username(bot, username)
        if staff_chat_id:
            targets[staff_chat_id] = username.lstrip("@")
    return targets


def _staff_already_answered(message: Message) -> bool:
    answered_at = _last_staff_message_at.get(message.chat.id)
    return answered_at is not None and answered_at > message.date


async def start_reply_waits(
    message: Message, targets: dict[int, str | None], addressed: bool = True, text: str | None = None
) -> None:
    """Har bir mas'ul xodim uchun javob kutishni boshlaydi. Xodim uchun shu guruhda kutish
    allaqachon ketayotgan bo'lsa (hali eslatma yuborilmagan) -- muddat BIRINCHI javobsiz
    xabardan hisoblanaveradi, keyingi xabarlar uni surmaydi. `addressed=False` -- xabar hech
    kimga qaratilmagan, xodim guruh menejeri sifatida mas'ul."""
    minutes = settings.STAFF_REPLY_TIMEOUT_MINUTES
    if text is None:
        text = message.text or message.caption
    if not addressed and _staff_already_answered(message):
        return  # xabar tahlil qilinayotgan paytda xodim javob berib bo'ldi
    async with async_session_maker() as session:
        for staff_chat_id, staff_username in targets.items():
            existing = (
                await session.execute(
                    select(StaffReplyWait).where(
                        StaffReplyWait.group_id == message.chat.id,
                        StaffReplyWait.staff_chat_id == staff_chat_id,
                    )
                )
            ).scalar_one_or_none()
            if existing and existing.notified_at is None:
                existing.addressed = existing.addressed or addressed
                continue
            wait = existing or StaffReplyWait(group_id=message.chat.id, staff_chat_id=staff_chat_id)
            wait.group_title = message.chat.title
            wait.staff_username = staff_username
            wait.customer_chat_id = message.from_user.id
            wait.customer_name = message.from_user.full_name
            wait.message_id = message.message_id
            wait.message_text = text
            wait.message_link = build_message_link(message.chat, message.message_id)
            wait.addressed = addressed
            wait.wait_minutes = minutes
            wait.promised = False
            wait.due_at = message.date + timedelta(minutes=minutes)
            wait.notified_at = None
            session.add(wait)
            logger.info(
                "Javob kutilmoqda: guruh=%s, xodim=%s, %s daqiqa", message.chat.id, staff_chat_id, minutes
            )
        await session.commit()


async def set_wait_text(message: Message, text: str) -> None:
    """Javob kutish ovozli xabar matnga o'girilishidan OLDIN ochiladi -- matn tayyor bo'lgach
    shu yerda yozib qo'yiladi (eslatmada xodimga mijoz nima degani ko'rinishi uchun)."""
    async with async_session_maker() as session:
        waits = (
            await session.execute(
                select(StaffReplyWait).where(
                    StaffReplyWait.group_id == message.chat.id, StaffReplyWait.message_id == message.message_id
                )
            )
        ).scalars().all()
        for wait in waits:
            wait.message_text = text
        await session.commit()


async def settle_reply_waits(message: Message, promised_minutes: int | None) -> None:
    """Xodim guruhga yozdi. "N daqiqada javob beraman" degan bo'lsa -- kutish N daqiqaga
    suriladi; aks holda bu javob hisoblanadi va kutish o'chiriladi. Xodim boshqa xodimdan
    kutilayotgan mijoz xabariga reply qilib javob bergan bo'lsa -- o'sha kutish ham yopiladi.
    Hech kimga qaratilmagan savol bo'yicha menejer(lar)dan kutilayotgan javobni (addressed=
    False) ISTALGAN xodimning javobi yopadi -- savol kompaniyaga berilgan, kompaniya javob berdi."""
    group_id = message.chat.id
    staff_chat_id = message.from_user.id
    async with async_session_maker() as session:
        if replied := get_replied_message(message):
            await session.execute(
                delete(StaffReplyWait).where(
                    StaffReplyWait.group_id == group_id,
                    StaffReplyWait.message_id == replied.message_id,
                    StaffReplyWait.staff_chat_id != staff_chat_id,
                )
            )
        if not promised_minutes:
            await session.execute(
                delete(StaffReplyWait).where(
                    StaffReplyWait.group_id == group_id,
                    StaffReplyWait.addressed.is_(False),
                    StaffReplyWait.staff_chat_id != staff_chat_id,
                )
            )
        own = (
            await session.execute(
                select(StaffReplyWait).where(
                    StaffReplyWait.group_id == group_id, StaffReplyWait.staff_chat_id == staff_chat_id
                )
            )
        ).scalar_one_or_none()
        if own:
            if promised_minutes:
                own.wait_minutes = promised_minutes
                own.promised = True
                own.due_at = message.date + timedelta(minutes=promised_minutes)
                own.notified_at = None
                logger.info(
                    "Xodim %s daqiqada javob berishni aytdi: guruh=%s, xodim=%s",
                    promised_minutes, group_id, staff_chat_id,
                )
            else:
                await session.delete(own)
                logger.info("Xodim javob berdi, kutish yopildi: guruh=%s, xodim=%s", group_id, staff_chat_id)
        await session.commit()


def build_reminder_text(wait: StaffReplyWait) -> str:
    group = html.escape(wait.group_title or str(wait.group_id))
    customer = html.escape(wait.customer_name or "mijoz")
    if wait.promised:
        head = (
            f"⏰ «{group}» guruhida {customer}ga {wait.wait_minutes} daqiqada javob beraman degan edingiz — "
            f"{wait.wait_minutes} daqiqa bo'ldi, javob yozmadingiz."
        )
    elif wait.addressed:
        head = (
            f"⏰ «{group}» guruhida {customer} sizga murojaat qilganiga {wait.wait_minutes} daqiqa bo'ldi — "
            f"javob yozmadingiz."
        )
    else:
        head = (
            f"⏰ «{group}» guruhida {customer} savol yozganiga {wait.wait_minutes} daqiqa bo'ldi — "
            f"hech kim javob yozmadi. Siz bu guruhning menejerisiz."
        )
    lines = [head]
    if wait.message_text:
        snippet = wait.message_text if len(wait.message_text) <= 300 else wait.message_text[:300] + "…"
        lines.append(f"\n💬 {html.escape(snippet)}")
    if wait.message_link:
        lines.append(f'🔗 <a href="{wait.message_link}">Xabarga o\'tish</a>')
    return "\n".join(lines)


async def send_due_reminders(bot: Bot, now: datetime | None = None) -> int:
    """Muddati o'tgan kutishlar bo'yicha xodimlarga eslatma yuboradi. Yuborilganlar sonini qaytaradi."""
    now = now or datetime.now(timezone.utc)
    sent = 0
    async with async_session_maker() as session:
        due = (
            await session.execute(
                select(StaffReplyWait).where(StaffReplyWait.notified_at.is_(None), StaffReplyWait.due_at <= now)
            )
        ).scalars().all()
        for wait in due:
            text = build_reminder_text(wait)
            try:
                await bot.send_message(wait.staff_chat_id, text, parse_mode="HTML", disable_web_page_preview=True)
                sent += 1
            except TelegramAPIError as exc:
                # Xodim botga hali /start bosmagan (bot unga shaxsiy yoza olmaydi) -- eslatma
                # yo'qolib ketmasligi uchun ichki guruhga, xodimni belgilab yuboramiz.
                logger.warning("Xodimga (chat_id=%s) shaxsiy yozib bo'lmadi: %s", wait.staff_chat_id, exc)
                mention = (
                    f"@{wait.staff_username}"
                    if wait.staff_username
                    else f'<a href="tg://user?id={wait.staff_chat_id}">Xodim</a>'
                )
                try:
                    await bot.send_message(
                        settings.FEEDBACK_HUB_GROUP_ID,
                        f"{mention}\n{text}\n\n<i>Eslatmalarni shaxsiy olish uchun botga /start bosing.</i>",
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    sent += 1
                except TelegramAPIError:
                    logger.exception("Eslatmani ichki guruhga ham yuborib bo'lmadi")
            wait.notified_at = now
        await session.commit()
    if due:
        # Bitta javobsiz savol bir necha xodimga eslatilishi mumkin -- savollar soni sanaladi
        await group_stats.bump(group_stats.STAT_ALERTS, now, amount=len({(w.group_id, w.message_id) for w in due}))
    return sent


async def staff_reply_reminder_loop(bot: Bot) -> None:
    """Bot jarayonida BITTA nusxada ishlaydigan davriy tekshiruv (`app/bot/__init__.py`).
    Ikkala rejimning kutishlari ham tekshiriladi -- rejim almashtirilganda eski rejimdan
    qolgan kutishlar osilib qolmaydi."""
    if not settings.FEEDBACK_HUB_GROUP_ID:
        return
    while True:
        try:
            await send_due_reminders(bot)
            await group_timers.send_due_alerts(bot, get_alert_managers, get_unanswered_thread_id)
        except Exception:
            logger.exception("Javobsiz murojaatlarni tekshirishda xatolik")
        await asyncio.sleep(settings.STAFF_REPLY_CHECK_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def _album_follow_up(message: Message) -> list[str] | None:
    """Xabar albomning birinchi fayli bo'lmasa -- birinchisi tashlangan topiklar ro'yxati
    (bo'sh bo'lishi mumkin). Albom emas yoki birinchi fayl bo'lsa None."""
    album_id = getattr(message, "media_group_id", None)
    if not album_id:
        return None
    if album_id in _albums:
        return _albums[album_id]
    _albums[album_id] = []
    if len(_albums) > _ALBUMS_LIMIT:
        _albums.popitem(last=False)
    return None


async def process_group_message(message: Message) -> None:
    hub_id = settings.FEEDBACK_HUB_GROUP_ID
    if not hub_id:
        return
    bot = message.bot
    sender = message.from_user

    if message.chat.id == hub_id:
        await learn_hub_topic(message)
        if sender and not sender.is_bot:
            _staff_cache[sender.id] = (True, time.monotonic() + _STAFF_CACHE_TTL_SECONDS)
            await remember_staff(sender)
        return

    if message.chat.id in _excluded_group_ids():
        return
    # Servis xabarlari (kimdir qo'shildi/chiqdi va h.k.), botlar va anonim adminlar hisobga olinmaydi
    if sender is None or sender.is_bot or message.content_type in _SERVICE_CONTENT_TYPES:
        return
    if not await group_registry.ensure_group(message.chat.id, message.chat.title):
        return  # guruh kuzatuvdan chiqarilgan

    group_alerts = settings.GROUP_ALERT_MODE == "group"
    written = message.text or message.caption or ""
    is_hub_member = await is_staff_member(bot, sender.id)
    is_staff = is_hub_member or await group_registry.is_group_manager(message.chat.id, sender)

    if is_staff:
        _last_staff_message_at[message.chat.id] = message.date
        if is_hub_member:
            await remember_staff(sender)

    album_kinds = _album_follow_up(message)
    if album_kinds is not None:
        # Albomning birinchi fayli (izohi bilan) allaqachon tahlil qilingan
        if not is_staff:
            await _forward_album_item(bot, message, album_kinds)
        return

    targets: dict[int, str | None] = {}
    if not is_staff:
        targets = await find_addressed_staff(bot, message)
        if targets and not group_alerts:
            # "dm" rejimi: aniq xodimga qaratilgan xabar bo'yicha kutish darhol boshlanadi --
            # transkripsiya va tasniflash tugashini kutmasdan. "group" rejimida kutish faqat
            # model xabarni javob kutadigan savol deb topgach ochiladi: guruhga, mijozlar
            # ko'z o'ngida yoziladigan ogohlantirish "rahmat" kabi xabar uchun ketmasligi kerak.
            await start_reply_waits(message, targets)

    # Ovozli xabar / video -- matnga o'giriladi, surat va video kadrlari -- rasm sifatida
    # tayyorlanadi; hammasi yozilgan matn (izoh) bilan birga tahlil qilinadi
    media = await ai_processor.extract_media(bot, message, with_images=not is_staff)
    transcript = media.transcript
    text = f"{written}\n{transcript}".strip() if transcript else written
    context = take_context(message, is_staff, text)

    if is_staff:
        analysis = await ai_processor.analyze(text, True, context=context, media=media)
        await settle_reply_waits(message, analysis.promised_minutes)
        await group_timers.settle_timer(message, analysis.promised_minutes)
        await group_stats.bump(group_stats.STAT_STAFF, message.date)
    else:
        if transcript and targets and not group_alerts:
            await set_wait_text(message, text)
        analysis = await ai_processor.analyze(
            text, False, context=context, addressed_to_staff=bool(targets), media=media
        )
        if analysis.image_note:
            remember_image_note(message, analysis.image_note)
        logged_text = "\n".join(part for part in (text, f"[rasm: {analysis.image_note}]" if analysis.image_note else "") if part)

        if analysis.needs_reply and group_alerts:
            if not _staff_already_answered(message):
                await group_timers.start_timer(
                    message, logged_text or None, build_message_link(message.chat, message.message_id)
                )
        elif analysis.needs_reply and not targets:
            # Hech kimga qaratilmagan savol -- javob guruh menejeridan kutiladi
            managers = await find_group_managers(bot, message.chat.id)
            if managers:
                await start_reply_waits(message, managers, addressed=False, text=text)
            else:
                logger.info("Guruhda menejer unvonli xodim topilmadi (group_id=%s)", message.chat.id)

        forwarded = False
        if analysis.category in ("complaint", "praise"):
            forwarded = await forward_to_hub_topic(
                bot, message, analysis.category_key,
                label="🔴 Shikoyat" if analysis.category == "complaint" else None,
                transcript=transcript, image_note=analysis.image_note,
            )
        if analysis.company_related or analysis.out_of_stock:
            await group_stats.log_message(
                message, analysis, text=logged_text, media_type=media.media_type,
                link=build_message_link(message.chat, message.message_id), forwarded=forwarded,
            )
        else:
            await group_stats.bump(group_stats.STAT_FILTERED, message.date)

    if analysis.out_of_stock:
        await forward_to_hub_topic(bot, message, "stock", transcript=transcript, image_note=analysis.image_note)
        await group_stats.bump(group_stats.STAT_STOCK, message.date)


@router.message(F.chat.type.in_(_GROUP_CHAT_TYPES))
async def handle_group_message(message: Message) -> None:
    async with _chat_locks.setdefault(message.chat.id, asyncio.Lock()):
        try:
            await process_group_message(message)
        except Exception:
            logger.exception("Guruh xabarini kuzatishda xatolik (chat_id=%s)", message.chat.id)
    # Xabar keyingi handlerlarga (group_mentions va h.k.) ham yetib borishi kerak
    raise SkipHandler
