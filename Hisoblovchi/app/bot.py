"""Telegram side. In the group a reply to a document photo such as "Nodir, 12,10,2026 +" becomes a payment reminder.
On its day, from REMIND_AT on, the reminder is given to the private chats that pressed /start, never to the group:
in a group the bot writes nothing, it only puts a reaction on the reply it has dealt with. In the private chat two
buttons show the lists: who is to pay today, and who is to pay in any range of days."""
from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import date, datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart, or_f
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message,
    ReactionTypeEmoji, ReplyKeyboardMarkup,
)

from app import excel, picker
from app.config import settings
from app.dates import Reply, parse_reply
from app.db import Reminders
from app.extract import ExtractError, Extraction, read_photo

log = logging.getLogger(__name__)
router = Router()

GROUP = F.chat.type.in_({"group", "supergroup"})
PRIVATE = F.chat.type == "private"
LIST_LIMIT = 30
# reminders in one message; the rest of a longer list is behind the "Davomini chiqarish" button
PAGE = 20
MEMBER = {"creator", "administrator", "member"}
# the reactions the bot answers with in a group: the reply was taken / was refused
TAKEN, REFUSED = "👍", "👎"
TITLE = "🔔 <b>To‘lov eslatmasi</b>"
BUTTON_TODAY, BUTTON_RANGE = "📋 Bugungi", "🗓 Oraliq"
MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BUTTON_TODAY), KeyboardButton(text=BUTTON_RANGE)]],
    resize_keyboard=True, is_persistent=True,
)
# what a "Davomi" or "Excel" button under a list points at: "d:<day>" is what the chat was given on that day,
# "r:<first>:<last>" is everyone who is to pay in those days
LIST_KEY = r"d:\d{4}-\d{2}-\d{2}|r:\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2}"
HELP = (
    "Assalomu alaykum! Guruhda sverka rasmiga javob (reply) qilib, mijoz bilan gaplashgan hodim ismi va to‘lov "
    "sanasini yozing:\n<code>Nodir, 12,10,2026 +</code>\n\n"
    "Rasmdan buyurtma raqami va mijoz ismini o‘qib olaman. Guruhda hech narsa yozmayman: reply qabul qilinsa unga "
    f"{TAKEN}, qabul qilinmasa {REFUSED} qo‘yaman. To‘lov kuni soat {settings.remind_at:%H:%M} da eslatmani botga "
    "/start bosgan guruh a’zolarining shaxsiy chatiga yuboraman; bugungi sana yoki «bugun» yozilsa — shu kuni "
    f"soat {settings.evening_at:%H:%M} da.\n\n"
    f"{BUTTON_TODAY} — bugun to‘lov qilishi kerak bo‘lganlar\n"
    f"{BUTTON_RANGE} — tanlangan sanalar oralig‘idagi ro‘yxat\n"
    "Guruhda rasmga reply qilib /bekor yozilsa, kutilayotgan eslatma bekor qilinadi."
)

# due reminders nobody could be given yet; each is reported in the log once, not every minute
_waiting: set[int] = set()
# (group, /start chat) pairs already reported in the log as "pressed /start but is not in that group"
_outsiders: set[tuple[int, int]] = set()
# one hand-out at a time: the minute loop, a fresh reply and /start must not give the same reminder twice
_delivering = asyncio.Lock()


def _now() -> datetime:
    return datetime.now(settings.tz)


def _allowed(message: Message) -> bool:
    return not settings.allowed_chat_ids or message.chat.id in settings.allowed_chat_ids


async def _is_member(bot: Bot, group_id: int, user_id: int, seen: dict[tuple[int, int], bool]) -> bool:
    """Whether this person is in the group the document was posted in; `seen` saves asking Telegram twice."""
    key = (group_id, user_id)
    if key not in seen:
        try:
            member = await bot.get_chat_member(group_id, user_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            # Telegram does not know this person, or the bot is no longer in that group
            seen[key] = False
        else:
            seen[key] = member.status in MEMBER or bool(getattr(member, "is_member", False))
    return seen[key]


@router.message.outer_middleware()
async def log_message(handler, message: Message, data):
    """One line per incoming message: without it the log cannot tell which chat or picture an update was about."""
    reply = f", reply to {message.reply_to_message.message_id}" if message.reply_to_message else ""
    log.info(
        "message %s in chat %s (%s): %s%s", message.message_id, message.chat.id,
        message.chat.title or message.chat.full_name, message.content_type.value, reply,
    )
    return await handler(message, data)


async def _react(message: Message, emoji: str) -> None:
    """The bot's whole answer inside a group: a reaction on the message it has dealt with."""
    try:
        await message.react([ReactionTypeEmoji(emoji=emoji)])
    except TelegramAPIError as exc:
        # a group may allow only some reactions, or none at all
        log.warning("no %s on message %s in chat %s: %s", emoji, message.message_id, message.chat.id, exc)


def _image(message: Message) -> tuple[str, str] | None:
    """file_id and mime type of the picture in a message: a photo, or an image sent as a file."""
    if message.photo:
        return message.photo[-1].file_id, "image/jpeg"
    document = message.document
    if document and (document.mime_type or "").startswith("image/"):
        return document.file_id, document.mime_type
    return None


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def who(row) -> str:
    """Who talked to the customer: the name written in the reply; without one, whoever wrote the reply."""
    return (row["tag"] or "").replace("#", "").strip() or row["created_by"] or "—"


def _photo_url(row) -> str | None:
    """Link to the document photo in its group; only supergroups have message links."""
    chat = str(row["chat_id"])
    if not chat.startswith("-100"):
        return None
    thread = f"{row['thread_id']}/" if row["thread_id"] else ""
    return f"https://t.me/c/{chat[4:]}/{thread}{row['photo_message_id']}"


def unread(row) -> bool:
    """Whether the order number or the customer could not be read from the photo: then the photo itself is sent."""
    return not (row["order_number"] and row["customer"])


def line(row, day: date | None = None) -> str:
    """A reminder as one line: order number (customer) - who talked to them.

    What could not be read is marked as being in the photo. The payment day is not written: a list is about its
    day. Only in the list of one `day` a reminder of another day (one that went out late) shows its own date.
    """
    order = html.escape(_cut(row["order_number"], 40)) if row["order_number"] else "📷 rasmda"
    if url := _photo_url(row):
        # the order number opens the document photo in its group
        order = f'<a href="{url}">{order}</a>'
    if row["customer"]:
        order += f" ({html.escape(_cut(row['customer'], 80))})"
    elif row["order_number"]:
        order += " (📷 rasmda)"
    due = date.fromisoformat(row["due_date"])
    late = f" · {due:%d.%m.%Y}" if day is not None and due != day else ""
    return f"{order} - {html.escape(_cut(who(row), 40))}{late}"


def page_text(rows, start: int, title: str, day: date | None) -> str:
    """Up to PAGE reminders of a list, numbered from start + 1; a list over several days is headed day by day."""
    page = rows[start:start + PAGE]
    lines, heading = [title], None
    if day is not None:
        lines.append("")
    for number, row in enumerate(page, start + 1):
        if day is None and row["due_date"] != heading:
            heading = row["due_date"]
            lines += ["", f"<b>{date.fromisoformat(heading):%d.%m.%Y}</b>"]
        lines.append(f"{number}. {line(row, day)}")
    if len(rows) > PAGE:
        lines += ["", f"Jami {len(rows)} ta, bu yerda {start + 1}–{start + len(page)}."]
    return "\n".join(lines)


def page_keyboard(key: str, shown: int, total: int) -> InlineKeyboardMarkup | None:
    """Buttons under a page of a list: the next page if there is more, and the whole list as an Excel file.

    Excel is offered under a list somebody asked for ("Bugungi", "Oraliq") and under one too long for one message,
    not under every reminder the bot sends by itself.
    """
    buttons = []
    if shown < total:
        more = f"▶️ Davomini chiqarish ({total - shown})"
        buttons.append([InlineKeyboardButton(text=more, callback_data=f"more:{key}:{shown}")])
    if key.startswith("r:") or total > PAGE:
        buttons.append([InlineKeyboardButton(text="📊 Excel", callback_data=f"xlsx:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None


async def _send_photo(bot: Bot, chat_id: int, row, caption: str, keyboard=None) -> bool:
    """Send the document photo itself, for a reminder whose photo could not be read. False if it cannot be sent."""
    if not row["file_id"]:
        return False
    # the copy keeps the "no forwarding" the group may have set for its documents
    extra = {"caption": caption, "reply_markup": keyboard, "protect_content": True}
    try:
        try:
            await bot.send_photo(chat_id, row["file_id"], **extra)
        except TelegramBadRequest:
            # Telegram will not reuse it as it is (an image sent as a file, a protected group): upload it anew
            buffer = await bot.download(row["file_id"])
            await bot.send_photo(chat_id, BufferedInputFile(buffer.read(), filename="hujjat.jpg"), **extra)
    except TelegramBadRequest as exc:
        log.warning("photo of reminder %s was not sent to chat %s: %s", row["id"], chat_id, exc)
        return False
    return True


async def send_page(bot: Bot, chat_id: int, rows, start: int, key: str, title: str, day: date | None) -> None:
    """One page of the list `key` stands for; the photos that could not be read follow it, each under its number."""
    page = rows[start:start + PAGE]
    # a list with no buttons of its own brings the two menu buttons along, for a chat that does not have them yet
    keyboard = page_keyboard(key, start + len(page), len(rows)) or MENU
    alone = len(page) == 1 and unread(page[0])
    # a single reminder whose photo was not read: the photo itself is the whole message
    if alone and await _send_photo(bot, chat_id, page[0], f"{title}\n\n{start + 1}. {line(page[0], day)}", keyboard):
        return
    await bot.send_message(chat_id, page_text(rows, start, title, day), reply_markup=keyboard)
    if alone:
        return
    for number, row in enumerate(page, start + 1):
        if unread(row):
            try:
                await _send_photo(bot, chat_id, row, f"{number}. {line(row, day)}")
            except TelegramAPIError as exc:
                # the list itself has gone out, so this is not worth failing (and repeating) the whole page for
                log.warning("photo of reminder %s was not sent to chat %s: %s", row["id"], chat_id, exc)


async def _listed(bot: Bot, reminders: Reminders, chat_id: int, key: str) -> tuple[list, date | None, str]:
    """The reminders a list key stands for, the one day they are about (None for several days), and the list's name."""
    kind, *days = key.split(":")
    first, last = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    if kind == "d":
        return reminders.delivered_on(chat_id, first), first, f"{first:%d.%m.%Y}"
    # a document is shown only to people of the group it was posted in
    seen: dict[tuple[int, int], bool] = {}
    rows = [row for row in reminders.between(first, last) if await _is_member(bot, row["chat_id"], chat_id, seen)]
    if first == last:
        return rows, first, f"{first:%d.%m.%Y}"
    return rows, None, f"{first:%d.%m.%Y} – {last:%d.%m.%Y}"


async def send_excel(bot: Bot, chat_id: int, rows, key: str, name: str) -> None:
    """The whole list `key` stands for as an Excel file in the accountants' layout."""
    items = [(row["order_number"], row["customer"], who(row), date.fromisoformat(row["due_date"])) for row in rows]
    await bot.send_document(
        chat_id,
        BufferedInputFile(excel.build(items), filename=f"eslatmalar_{key[2:].replace(':', '_')}.xlsx"),
        caption=f"📊 {name} — jami {len(rows)} ta",
    )


async def send_range(bot: Bot, reminders: Reminders, chat_id: int, first: date, last: date, today: date) -> None:
    """Everyone who is to pay from `first` to `last`, whether already reminded or not."""
    key = f"r:{first.isoformat()}:{last.isoformat()}"
    rows, day, name = await _listed(bot, reminders, chat_id, key)
    todays = first == last == today
    if not rows:
        when = "Bugun" if todays else f"{name}:"
        await bot.send_message(chat_id, f"{when} to‘lov qilishi kerak bo‘lganlar yo‘q.", reply_markup=MENU)
        return
    title = "📋 <b>Bugungi to‘lovlar</b>" if todays else f"🗓 <b>{name}</b>"
    await send_page(bot, chat_id, rows, 0, key, f"{title} — jami {len(rows)} ta", day)


def ripe(row, now: datetime) -> bool:
    """Whether it is time to remind of this payment.

    A payment promised for the very day the reply was written is asked about that evening; any other one is
    announced on the morning of its day, and one whose day has passed without a reminder goes out with the next.
    """
    same_day = row["due_date"] == now.date().isoformat() == row["created_at"][:10]
    return now.time() >= (settings.evening_at if same_day else settings.remind_at)


async def _owed(bot: Bot, reminders: Reminders, chat_id: int, now: datetime, seen: dict[tuple[int, int], bool]) -> list:
    """Reminders whose time has come and which a /start chat has not been given yet.

    A document is shown only to people of the group it was posted in.
    """
    owed = []
    for row in reminders.undelivered(chat_id, now.date()):
        if not ripe(row, now):
            continue
        if await _is_member(bot, row["chat_id"], chat_id, seen):
            owed.append(row)
        elif (row["chat_id"], chat_id) not in _outsiders:
            _outsiders.add((row["chat_id"], chat_id))
            log.info(
                "chat %s pressed /start but is not in group %s: it gets none of its reminders", chat_id, row["chat_id"]
            )
    return owed


async def send_due(bot: Bot, reminders: Reminders, now: datetime) -> None:
    """Give every /start chat the reminders whose time has come and which it has not been given yet."""
    if now.time() < min(settings.remind_at, settings.evening_at):
        return
    today = now.date()
    async with _delivering:
        seen: dict[tuple[int, int], bool] = {}
        for chat_id in reminders.subscribers():
            try:
                new = await _owed(bot, reminders, chat_id, now, seen)
                if not new:
                    continue
                # the numbers continue the list this chat already got today
                before = reminders.delivered_on(chat_id, today)
                await send_page(bot, chat_id, before + new, len(before), f"d:{today.isoformat()}", TITLE, today)
            except TelegramForbiddenError:
                log.info("chat %s blocked the bot: unsubscribed", chat_id)
                reminders.unsubscribe(chat_id)
            except TelegramAPIError as exc:
                log.warning("chat %s was not given its reminders yet, will retry: %s", chat_id, exc)
            else:
                reminders.deliver([row["id"] for row in new], chat_id, now)
                log.info("chat %s was given reminders %s", chat_id, [row["id"] for row in new])
        for row in reminders.due(today):
            if ripe(row, now) and row["id"] not in _waiting:
                # it stays pending and goes out as soon as someone from that group presses /start
                _waiting.add(row["id"])
                log.warning("reminder %s waits: no /start chat is a member of group %s", row["id"], row["chat_id"])


@router.message(CommandStart(), PRIVATE)
async def on_start(message: Message, bot: Bot, reminders: Reminders) -> None:
    seen: dict[tuple[int, int], bool] = {}
    groups = settings.allowed_chat_ids
    if groups and not any([await _is_member(bot, group, message.chat.id, seen) for group in groups]):
        # the bot serves known groups only, and this person is in none of them
        log.info("chat %s pressed /start but is in none of the bot's groups", message.chat.id)
        await message.answer("Bu bot faqat o‘z guruhi a’zolari uchun. Avval guruhga qo‘shiling, keyin /start bosing.")
        return
    reminders.subscribe(message.chat.id, message.from_user.full_name if message.from_user else None)
    log.info("subscribed: chat %s", message.chat.id)
    await message.answer(HELP + "\n\n✅ Eslatmalar endi shu chatga keladi.", reply_markup=MENU)
    # today's reminders are not kept from someone who pressed /start after the others got them
    await send_due(bot, reminders, _now())


@router.message(Command("chatid"), PRIVATE)
async def on_chatid(message: Message) -> None:
    await message.reply(f"Chat ID: <code>{message.chat.id}</code>")


@router.message(PRIVATE, or_f(Command("bugun"), F.text == BUTTON_TODAY))
async def on_today(message: Message, bot: Bot, reminders: Reminders) -> None:
    today = _now().date()
    await send_range(bot, reminders, message.chat.id, today, today, today)


@router.message(PRIVATE, or_f(Command("oraliq"), F.text == BUTTON_RANGE))
async def on_range(message: Message) -> None:
    today = _now().date()
    await message.answer("🗓 Qaysi sanadan?", reply_markup=picker.keyboard(today.replace(day=1), None, today))


@router.message(Command("eslatmalar"), PRIVATE)
async def on_list(message: Message, bot: Bot, reminders: Reminders) -> None:
    # the pending reminders of every group this person is in
    seen: dict[tuple[int, int], bool] = {}
    rows = [row for row in reminders.pending() if await _is_member(bot, row["chat_id"], message.chat.id, seen)]
    if not rows:
        await message.reply("Kutilayotgan eslatma yo‘q.")
        return
    lines = [
        f"{date.fromisoformat(row['due_date']):%d.%m.%Y} — " + html.escape(", ".join([
            _cut(row["order_number"] or "raqam o‘qilmagan", 30), _cut(row["customer"] or "mijoz o‘qilmagan", 40),
            _cut(who(row), 25),
        ]))
        for row in rows[:LIST_LIMIT]
    ]
    if len(rows) > LIST_LIMIT:
        lines.append(f"… yana {len(rows) - LIST_LIMIT} ta")
    await message.reply("📋 Kutilayotgan eslatmalar:\n" + "\n".join(lines))


@router.message(Command("bekor"), GROUP)
async def on_cancel(message: Message, reminders: Reminders) -> None:
    if not _allowed(message):
        return
    photo = message.reply_to_message
    existing = None
    if photo is not None and _image(photo) is not None:
        existing = reminders.pending_for_photo(message.chat.id, photo.message_id)
    if existing is None:
        # not written as a reply to a document photo, or nothing is pending for that photo
        await _react(message, REFUSED)
        return
    reminders.set_status(existing["id"], "cancelled")
    log.info("reminder %s cancelled: chat %s photo %s", existing["id"], message.chat.id, photo.message_id)
    await _react(message, TAKEN)


# a reply corrected by editing it is read again like a new one
@router.edited_message(GROUP, F.reply_to_message, F.text)
@router.message(GROUP, F.reply_to_message, F.text)
async def on_date_reply(message: Message, bot: Bot, reminders: Reminders) -> None:
    photo = message.reply_to_message
    if not _allowed(message):
        return
    image = _image(photo)
    if image is None:
        log.info("reply in chat %s to a %s, not a picture: ignored", message.chat.id, photo.content_type.value)
        return
    today = _now().date()
    try:
        reply = parse_reply(message.text, today)
    except ValueError:
        # a day that does not exist, like 31.11: refused below
        reply = Reply(due=None, tag=None)
    if reply is None:
        log.info(
            "reply to picture %s in chat %s has no date: %r", photo.message_id, message.chat.id, message.text[:60]
        )
        return
    due = reply.due
    if due is None or due < today:
        # written for the bot, but without a date it can read, or with a day that is already gone
        log.info("reply to picture %s in chat %s refused: %r", photo.message_id, message.chat.id, message.text[:60])
        await _react(message, REFUSED)
        return
    if reminders.sent_for_photo(message.chat.id, photo.message_id, due) is not None:
        # this photo's reminder for this day has already gone out: nothing to add
        await _react(message, TAKEN)
        return

    # a new date for the same photo moves the payment day; the picture is not read again
    existing = reminders.pending_for_photo(message.chat.id, photo.message_id)
    if existing is not None:
        reminders.reschedule(existing["id"], due, reply.tag, _now())
    else:
        file_id, mime = image
        try:
            buffer = await bot.download(file_id)
            found = await read_photo(buffer.read(), mime)
        except (ExtractError, TelegramAPIError) as exc:
            log.error("photo %s in chat %s was not read: %s", photo.message_id, message.chat.id, exc)
            found = Extraction()
        reminders.add(
            chat_id=message.chat.id,
            # in an ordinary group a reply also carries a thread id, which is not a forum topic
            thread_id=message.message_thread_id if message.is_topic_message else None,
            photo_message_id=photo.message_id,
            due_date=due,
            customer=found.customer,
            order_number=found.order_number,
            created_by=message.from_user.full_name if message.from_user else None,
            tag=reply.tag,
            file_id=file_id,
            now=_now(),
        )
    saved = reminders.pending_for_photo(message.chat.id, photo.message_id)
    # the group is not told what was read from the photo, so the log keeps it
    log.info(
        "reminder %s saved: chat %s photo %s due %s, order %r, customer %r, %s", saved["id"], message.chat.id,
        photo.message_id, due, saved["order_number"], saved["customer"], who(saved),
    )
    await _react(message, TAKEN)
    if due == today:
        # written for today after the evening hour: there is no later round today to wait for
        await send_due(bot, reminders, _now())


@router.callback_query(F.data.regexp(rf"^more:({LIST_KEY}):(\d+)$").as_("found"))
async def on_more(callback: CallbackQuery, bot: Bot, reminders: Reminders, found: re.Match[str]) -> None:
    key, start = found[1], int(found[2])
    rows, day, name = await _listed(bot, reminders, callback.from_user.id, key)
    if start >= len(rows):
        await callback.answer("Ro‘yxat tugadi.")
        return
    title = TITLE if key.startswith("d:") else f"🗓 <b>{name}</b>"
    await send_page(bot, callback.from_user.id, rows, start, key, f"{title} (davomi)", day)
    await callback.answer()
    if isinstance(callback.message, Message):
        try:
            # the next page carries the buttons now; a second press here would only repeat it
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass


@router.callback_query(F.data.regexp(rf"^xlsx:({LIST_KEY})$").as_("found"))
async def on_excel(callback: CallbackQuery, bot: Bot, reminders: Reminders, found: re.Match[str]) -> None:
    key = found[1]
    rows, _, name = await _listed(bot, reminders, callback.from_user.id, key)
    if not rows:
        await callback.answer("Ro‘yxat bo‘sh.")
        return
    await callback.answer("Excel tayyorlanmoqda…")
    await send_excel(bot, callback.from_user.id, rows, key, name)
    log.info("chat %s was sent the Excel of %s (%s rows)", callback.from_user.id, key, len(rows))


@router.callback_query(F.data.regexp(r"^cal:(-|\d{4}-\d{2}-\d{2}):(\d{4})-(\d{2})$").as_("found"))
async def on_month(callback: CallbackQuery, found: re.Match[str]) -> None:
    """Another month of the "Oraliq" calendar."""
    first = None if found[1] == "-" else date.fromisoformat(found[1])
    month = date(int(found[2]), int(found[3]), 1)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=picker.keyboard(month, first, _now().date()))


@router.callback_query(F.data.regexp(r"^pick:(-|\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})$").as_("found"))
async def on_pick(callback: CallbackQuery, bot: Bot, reminders: Reminders, found: re.Match[str]) -> None:
    """A day pressed in the "Oraliq" calendar: the first press is where the range starts, the second where it ends."""
    picked, today = date.fromisoformat(found[2]), _now().date()
    shown = callback.message if isinstance(callback.message, Message) else None
    await callback.answer()
    if found[1] == "-":
        if shown is not None:
            await shown.edit_text(
                f"🗓 {picked:%d.%m.%Y} dan. Qaysi sanagacha?",
                reply_markup=picker.keyboard(picked.replace(day=1), picked, today),
            )
        return
    # pressed in either order
    first, last = sorted((date.fromisoformat(found[1]), picked))
    if shown is not None:
        await shown.edit_text(f"🗓 {first:%d.%m.%Y} – {last:%d.%m.%Y}")
    await send_range(bot, reminders, callback.from_user.id, first, last, today)


@router.callback_query(F.data == picker.IDLE)
async def on_idle(callback: CallbackQuery) -> None:
    await callback.answer()


async def reminder_loop(bot: Bot, reminders: Reminders) -> None:
    while True:
        try:
            await send_due(bot, reminders, _now())
        except Exception:
            log.exception("reminder loop failed")
        await asyncio.sleep(60)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not settings.telegram_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN .env faylida yo'q")
    bot = Bot(settings.telegram_token, default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True))
    reminders = Reminders(settings.db_path)
    dp = Dispatcher(reminders=reminders)
    dp.include_router(router)

    me = await bot.get_me()
    log.info(
        "started as @%s, reminders at %s %s, photos read by %s", me.username, settings.remind_at, settings.tz,
        settings.openai_model,
    )
    if not settings.openai_api_key:
        log.error("OPENAI_API_KEY is not set: reminders will be saved without order number and customer")
    if not me.can_read_all_group_messages:
        log.warning("privacy mode is on: the bot sees replies to photos only where it is a group admin")

    loop_task = asyncio.create_task(reminder_loop(bot, reminders))
    try:
        await dp.start_polling(bot, allowed_updates=["message", "edited_message", "callback_query"])
    finally:
        loop_task.cancel()
