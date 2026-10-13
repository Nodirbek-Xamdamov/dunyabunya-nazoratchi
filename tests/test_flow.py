"""The bot driven by raw Telegram updates; Telegram itself and the vision model are replaced by stand-ins."""
import io
import itertools
import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    AnswerCallbackQuery, EditMessageReplyMarkup, EditMessageText, GetChatMember, SendDocument, SendMessage, SendPhoto,
    SetMessageReaction,
)
from aiogram.types import (
    BufferedInputFile, Chat, ChatMemberLeft, ChatMemberMember, InlineKeyboardMarkup, Message, User,
)
from openpyxl import load_workbook

import app.bot as hisoblovchi
from app.db import Reminders
from app.extract import Extraction, ExtractError

GROUP = -1001234567890
NODIR, BOSS, STRANGER = 501, 502, 503
# after the evening hour: a payment promised for today is reminded of at once
NOW = datetime(2026, 10, 12, 18, 30)
TODAY = NOW.date()
STAMP = int(NOW.timestamp())
TITLE = "🔔 <b>To‘lov eslatmasi</b>\n\n"
LINK = '<a href="https://t.me/c/1234567890/{}">{}</a>'
REMINDER = TITLE + "1. " + LINK.format(100, "S88784") + " (Karimov Jasur) - Nodir"

dp = Dispatcher()
dp.include_router(hisoblovchi.router)
_ids = itertools.count(1)


class FakeTelegram(BaseSession):
    """Records every call the bot makes and answers the way Telegram would."""

    def __init__(self):
        super().__init__()
        self.calls = []
        # like a group with protected content: a photo cannot be sent on by its file id, only uploaded anew
        self.refuse_file_ids = False

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetChatMember):
            user = User(id=method.user_id, is_bot=False, first_name="x")
            return ChatMemberMember(user=user) if method.user_id in (NODIR, BOSS) else ChatMemberLeft(user=user)
        if isinstance(method, SendPhoto) and isinstance(method.photo, str) and self.refuse_file_ids:
            raise TelegramBadRequest(method=method, message="wrong file identifier")
        if isinstance(method, (SendMessage, SendDocument, SendPhoto)):
            return Message(message_id=next(_ids), date=NOW, chat=Chat(id=method.chat_id, type="private"))
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""


def _user(user_id: int) -> dict:
    return {"id": user_id, "is_bot": False, "first_name": f"User{user_id}"}


def _private(user_id: int) -> dict:
    return {"id": user_id, "type": "private", "first_name": f"User{user_id}"}


def private_text(user_id: int, text: str) -> dict:
    message = {"message_id": next(_ids), "date": STAMP, "chat": _private(user_id), "from": _user(user_id), "text": text}
    return {"update_id": next(_ids), "message": message}


def group_text(user_id: int, text: str, reply_to: dict | None = None) -> dict:
    chat = {"id": GROUP, "type": "supergroup", "title": "Sverka"}
    message = {"message_id": next(_ids), "date": STAMP, "chat": chat, "from": _user(user_id), "text": text}
    if reply_to is not None:
        message["reply_to_message"] = {"date": STAMP, "chat": chat, "from": _user(NODIR), **reply_to}
    return {"update_id": next(_ids), "message": message}


def group_reply(user_id: int, text: str, photo_id: int = 100) -> dict:
    """A text written in the group in reply to a document photo."""
    photo = [{"file_id": "F", "file_unique_id": "U", "width": 800, "height": 1000}]
    return group_text(user_id, text, {"message_id": photo_id, "photo": photo})


def button(user_id: int, data: str) -> dict:
    shown = {"message_id": next(_ids), "date": STAMP, "chat": _private(user_id), "text": "list"}
    query = {"id": str(next(_ids)), "from": _user(user_id), "chat_instance": "x", "data": data, "message": shown}
    return {"update_id": next(_ids), "callback_query": query}


class FlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.telegram = FakeTelegram()
        self.bot = Bot("42:TEST", session=self.telegram, default=DefaultBotProperties(parse_mode="HTML"))
        self.reminders = dp["reminders"] = Reminders(":memory:")
        self.now = NOW
        self.read_photo = AsyncMock(return_value=Extraction("Karimov Jasur", "S88784"))
        # the settings of the tests, whatever the server's own .env says
        plain = replace(hisoblovchi.settings, allowed_chat_ids=frozenset(), remind_at=time(9), evening_at=time(18))
        for patcher in (
            patch.object(hisoblovchi, "settings", plain),
            patch.object(hisoblovchi, "_now", lambda: self.now),
            patch.object(hisoblovchi, "read_photo", self.read_photo),
            patch.object(Bot, "download", AsyncMock(side_effect=lambda *args, **kwargs: io.BytesIO(b"jpeg"))),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def feed(self, *updates: dict) -> None:
        for update in updates:
            await dp.feed_raw_update(self.bot, update)

    def sent(self, chat_id: int) -> list[SendMessage]:
        return [call for call in self.telegram.calls if isinstance(call, SendMessage) and call.chat_id == chat_id]

    def texts(self, chat_id: int) -> list[str]:
        return [call.text for call in self.sent(chat_id)]

    def photos(self, chat_id: int) -> list[SendPhoto]:
        return [call for call in self.telegram.calls if isinstance(call, SendPhoto) and call.chat_id == chat_id]

    def edits(self) -> list[str]:
        return [call.text for call in self.telegram.calls if isinstance(call, EditMessageText)]

    def reactions(self) -> list[str]:
        """The reactions the bot has put on messages of the group, in order."""
        calls = [call for call in self.telegram.calls if isinstance(call, SetMessageReaction)]
        self.assertTrue(all(call.chat_id == GROUP for call in calls))
        return [call.reaction[0].emoji for call in calls]

    def written_in_group(self) -> list:
        """Everything the bot posted into the group: there must never be anything."""
        return [call for call in self.telegram.calls
                if isinstance(call, (SendMessage, SendDocument, SendPhoto)) and call.chat_id == GROUP]

    def answers(self) -> list[str | None]:
        return [call.text for call in self.telegram.calls if isinstance(call, AnswerCallbackQuery)]

    def buttons(self, chat_id: int) -> list[list[str]]:
        """What is under each message sent to the chat: the data of its inline buttons, or the two menu buttons."""
        under = []
        for markup in (call.reply_markup for call in self.sent(chat_id)):
            if isinstance(markup, InlineKeyboardMarkup):
                under.append([key.callback_data for row in markup.inline_keyboard for key in row])
            else:
                under.append([key.text for row in markup.keyboard for key in row] if markup else [])
        return under

    def _add(self, day: date, photo: int, **fields) -> None:
        """A reminder written the day before NOW, so one that belongs to the morning of its day."""
        values = {"customer": "Mijoz", "order_number": f"S{photo}", "tag": "Nodir", "file_id": None, **fields}
        self.reminders.add(
            chat_id=GROUP, thread_id=None, photo_message_id=photo, due_date=day, created_by="Tester",
            now=NOW - timedelta(days=1), **values,
        )

    def _add_many(self, count: int, first_photo: int = 200) -> None:
        for n in range(count):
            self._add(TODAY, first_photo + n, customer=f"Mijoz {n + 1}", order_number=f"S{n + 1:05}")

    async def test_todays_date_is_given_at_once_to_everyone_who_pressed_start(self):
        await self.feed(*(private_text(user, "/start") for user in (NODIR, BOSS, STRANGER)))
        self.assertEqual(self.buttons(NODIR), [["📋 Bugungi", "🗓 Oraliq"]])
        self.telegram.calls.clear()

        await self.feed(group_reply(NODIR, "#Nodir, 12,10,2026 +"))
        # in the group: a reaction on the reply and not a word
        self.assertEqual(self.reactions(), ["👍"])
        self.assertEqual(self.written_in_group(), [])
        self.assertEqual(self.texts(NODIR), [REMINDER])
        self.assertEqual(self.texts(BOSS), [REMINDER])
        # somebody who is not in the group gets nothing, although they pressed /start
        self.assertEqual(self.texts(STRANGER), [])
        # a reminder the bot sends by itself carries no Excel button, only the two menu buttons
        self.assertEqual(self.buttons(NODIR), [["📋 Bugungi", "🗓 Oraliq"]])

        # the same reply once more does not make a second reminder
        await self.feed(group_reply(BOSS, "#Nodir, 12,10,2026 +"))
        self.assertEqual(self.reactions(), ["👍", "👍"])
        self.assertEqual(len(self.texts(NODIR)), 1)

        # the next document of the day continues the numbering; the name needs no "#"
        await self.feed(group_reply(BOSS, "SAIDAHMAD \n12.10.2026\n+", photo_id=101))
        second = "2. " + LINK.format(101, "S88784") + " (Karimov Jasur) - SAIDAHMAD"
        self.assertEqual(self.texts(NODIR)[1], TITLE + second)
        self.assertEqual(self.written_in_group(), [])

    async def test_start_pressed_after_the_reply_still_gets_todays_reminder(self):
        await self.feed(group_reply(NODIR, "#Nodir, 12,10,2026 +"))
        self.assertEqual(self.reactions(), ["👍"])
        self.assertEqual(len(self.reminders.due(TODAY)), 1)

        await self.feed(private_text(NODIR, "/start"))
        self.assertIn("Eslatmalar endi shu chatga keladi", self.texts(NODIR)[0])
        self.assertEqual(self.texts(NODIR)[1:], [REMINDER])
        await self.feed(private_text(BOSS, "/start"))
        self.assertEqual(self.texts(BOSS)[1:], [REMINDER])

        # pressing /start again repeats the greeting, not the reminder
        await self.feed(private_text(NODIR, "/start"))
        self.assertEqual(len(self.texts(NODIR)), 3)

    async def test_future_date_goes_out_on_its_morning(self):
        await self.feed(private_text(NODIR, "/start"), group_reply(BOSS, "Nodir, 13,10,2026 +"))
        self.assertEqual(len(self.texts(NODIR)), 1)

        await hisoblovchi.send_due(self.bot, self.reminders, datetime(2026, 10, 13, 8, 59))
        self.assertEqual(len(self.texts(NODIR)), 1)
        for minute in (0, 1):
            await hisoblovchi.send_due(self.bot, self.reminders, datetime(2026, 10, 13, 9, minute))
        # sent on its own day, so the day is not written
        self.assertEqual(self.texts(NODIR)[1:], [REMINDER])

    async def test_reminder_that_went_out_late_shows_its_day(self):
        await self.feed(private_text(NODIR, "/start"), group_reply(BOSS, "Nodir, 13,10,2026 +"))
        await hisoblovchi.send_due(self.bot, self.reminders, datetime(2026, 10, 15, 9, 0))
        self.assertEqual(self.texts(NODIR)[1], REMINDER + " · 13.10.2026")

    async def test_payment_promised_for_today_is_reminded_of_in_the_evening(self):
        self.now = datetime(2026, 10, 12, 10, 0)
        await self.feed(
            private_text(NODIR, "/start"), group_reply(NODIR, "#Nodir, 12,10,2026 +"),
            # "bugun" is today as well
            group_reply(BOSS, "Ali bugun", photo_id=101),
        )
        self.assertEqual(self.reactions(), ["👍", "👍"])
        for hour, minute in ((10, 1), (17, 59)):
            await hisoblovchi.send_due(self.bot, self.reminders, datetime(2026, 10, 12, hour, minute))
        self.assertEqual(len(self.texts(NODIR)), 1)

        await hisoblovchi.send_due(self.bot, self.reminders, datetime(2026, 10, 12, 18, 0))
        second = "\n2. " + LINK.format(101, "S88784") + " (Karimov Jasur) - Ali"
        self.assertEqual(self.texts(NODIR)[1:], [REMINDER + second])

    async def test_date_moved_to_today_also_waits_for_the_evening(self):
        self._add(date(2026, 10, 20), 100, customer="Karimov Jasur", order_number="S88784")
        self.now = datetime(2026, 10, 12, 10, 0)
        await self.feed(private_text(NODIR, "/start"), group_reply(NODIR, "Nodir bugun"))
        await hisoblovchi.send_due(self.bot, self.reminders, datetime(2026, 10, 12, 12, 0))
        self.assertEqual(len(self.texts(NODIR)), 1)
        await hisoblovchi.send_due(self.bot, self.reminders, datetime(2026, 10, 12, 18, 0))
        self.assertEqual(self.texts(NODIR)[1:], [REMINDER])

    async def test_reply_corrected_by_editing_moves_the_reminder(self):
        wrong = group_reply(NODIR, "Nodir, 21,10,2026 +")
        fixed = {"update_id": next(_ids), "edited_message": {**wrong["message"], "text": "Nodir, 20,10,2026 +"}}
        await self.feed(wrong, fixed)
        self.assertEqual(self.reactions(), ["👍", "👍"])
        self.assertEqual([row["due_date"] for row in self.reminders.pending()], ["2026-10-20"])
        self.assertEqual(self.read_photo.await_count, 1)

    async def test_start_from_outside_the_bots_groups_is_turned_away(self):
        only = replace(hisoblovchi.settings, allowed_chat_ids=frozenset({GROUP}))
        with patch.object(hisoblovchi, "settings", only):
            await self.feed(private_text(STRANGER, "/start"), private_text(NODIR, "/start"))
        self.assertIn("faqat o‘z guruhi a’zolari uchun", self.texts(STRANGER)[0])
        self.assertEqual(self.reminders.subscribers(), [NODIR])

    async def test_long_list_is_cut_at_twenty_with_buttons_for_the_rest(self):
        await self.feed(private_text(NODIR, "/start"))
        self._add_many(25)
        self.telegram.calls.clear()

        await hisoblovchi.send_due(self.bot, self.reminders, NOW)
        [first] = self.texts(NODIR)
        self.assertIn("\n20. ", first)
        self.assertNotIn("\n21. ", first)
        self.assertIn("Jami 25 ta, bu yerda 1–20.", first)
        self.assertEqual(self.buttons(NODIR), [["more:d:2026-10-12:20", "xlsx:d:2026-10-12"]])

        await self.feed(button(NODIR, "more:d:2026-10-12:20"))
        second = self.texts(NODIR)[1]
        self.assertIn("(davomi)\n\n21. ", second)
        self.assertIn("\n25. ", second)
        self.assertEqual(self.buttons(NODIR)[1], ["xlsx:d:2026-10-12"])
        self.assertTrue(any(isinstance(call, EditMessageReplyMarkup) for call in self.telegram.calls))
        await self.feed(button(NODIR, "more:d:2026-10-12:25"))
        self.assertEqual(self.answers()[-1], "Ro‘yxat tugadi.")
        self.assertEqual(len(self.texts(NODIR)), 2)

        await self.feed(button(NODIR, "xlsx:d:2026-10-12"))
        [document] = [call for call in self.telegram.calls if isinstance(call, SendDocument)]
        self.assertEqual((document.chat_id, document.document.filename), (NODIR, "eslatmalar_2026-10-12.xlsx"))
        sheet = load_workbook(io.BytesIO(document.document.data)).active
        # the accountants' sheet: 25 rows under the headings, then the total of the sums they will type in
        self.assertEqual((sheet.title, sheet.max_row), ("Hujjat", 27))
        self.assertEqual(
            [cell.value for cell in sheet[26]],
            ["S00025", datetime(2026, 10, 12), "Dushanba", "Mijoz 25", None, "✕", "Nodir"],
        )
        self.assertEqual((sheet["D27"].value, sheet["E27"].value), ("Jami:", "=SUM(E2:E26)"))

        # one more document later the same day: only the new one, numbered on
        self._add_many(1, first_photo=300)
        await hisoblovchi.send_due(self.bot, self.reminders, NOW.replace(hour=19))
        self.assertIn("\n26. ", self.texts(NODIR)[2])
        self.assertNotIn("25. ", self.texts(NODIR)[2])
        self.assertEqual(self.buttons(NODIR)[2], ["xlsx:d:2026-10-12"])

    async def test_buttons_show_a_chat_only_its_own_list(self):
        await self.feed(private_text(NODIR, "/start"))
        self._add_many(25)
        await hisoblovchi.send_due(self.bot, self.reminders, NOW)
        self.telegram.calls.clear()

        await self.feed(
            button(STRANGER, "xlsx:d:2026-10-12"), button(STRANGER, "more:d:2026-10-12:20"),
            # the list of a range is closed to people outside the group as well
            button(STRANGER, "xlsx:r:2026-10-01:2026-10-31"), private_text(STRANGER, "📋 Bugungi"),
        )
        self.assertEqual(self.answers(), ["Ro‘yxat bo‘sh.", "Ro‘yxat tugadi.", "Ro‘yxat bo‘sh."])
        self.assertEqual(self.texts(STRANGER), ["Bugun to‘lov qilishi kerak bo‘lganlar yo‘q."])
        self.assertFalse(any(isinstance(call, SendDocument) for call in self.telegram.calls))

    async def test_bugungi_button_lists_everyone_who_is_to_pay_today(self):
        await self.feed(private_text(NODIR, "/start"), private_text(NODIR, "📋 Bugungi"))
        self.assertEqual(self.texts(NODIR)[1], "Bugun to‘lov qilishi kerak bo‘lganlar yo‘q.")

        # also before nine, and also what the chat was already reminded of; tomorrow's is not in it
        self.now = datetime(2026, 10, 12, 8, 30)
        self._add(TODAY, 100, customer="Karimov Jasur", order_number="S88784")
        self._add(date(2026, 10, 13), 101)
        await self.feed(private_text(NODIR, "📋 Bugungi"))
        todays = "📋 <b>Bugungi to‘lovlar</b> — jami 1 ta\n\n" + REMINDER.removeprefix(TITLE)
        self.assertEqual(self.texts(NODIR)[2], todays)
        # a list that was asked for can be had as an Excel file
        self.assertEqual(self.buttons(NODIR)[2], ["xlsx:r:2026-10-12:2026-10-12"])

        await hisoblovchi.send_due(self.bot, self.reminders, datetime(2026, 10, 12, 9, 0))
        self.assertEqual(self.texts(NODIR)[3], REMINDER)
        await self.feed(private_text(NODIR, "/bugun"))
        self.assertEqual(self.texts(NODIR)[4], todays)

    async def test_oraliq_button_asks_for_two_days_and_lists_them(self):
        for day, photo in ((11, 1), (12, 2), (13, 3), (13, 4), (20, 5)):
            self._add(date(2026, 10, day), photo)
        await self.feed(private_text(NODIR, "/start"), private_text(NODIR, "🗓 Oraliq"))
        asked = self.sent(NODIR)[-1]
        self.assertEqual(asked.text, "🗓 Qaysi sanadan?")
        days = [key.callback_data for row in asked.reply_markup.inline_keyboard for key in row]
        self.assertIn("pick:-:2026-10-12", days)

        # the later day first: the two presses may come in either order
        await self.feed(button(NODIR, "pick:-:2026-10-13"))
        self.assertEqual(self.edits(), ["🗓 13.10.2026 dan. Qaysi sanagacha?"])
        await self.feed(button(NODIR, "pick:2026-10-13:2026-10-12"))
        self.assertEqual(self.edits()[1], "🗓 12.10.2026 – 13.10.2026")
        self.assertEqual(self.texts(NODIR)[-1], "\n".join([
            "🗓 <b>12.10.2026 – 13.10.2026</b> — jami 3 ta",
            "", "<b>12.10.2026</b>", "1. " + LINK.format(2, "S2") + " (Mijoz) - Nodir",
            "", "<b>13.10.2026</b>", "2. " + LINK.format(3, "S3") + " (Mijoz) - Nodir",
            "3. " + LINK.format(4, "S4") + " (Mijoz) - Nodir",
        ]))

        # first the list in the chat; the Excel file only when its button under the list is pressed
        self.assertEqual(self.buttons(NODIR)[-1], ["xlsx:r:2026-10-12:2026-10-13"])
        self.assertFalse(any(isinstance(call, SendDocument) for call in self.telegram.calls))
        await self.feed(button(NODIR, "xlsx:r:2026-10-12:2026-10-13"))
        [document] = [call for call in self.telegram.calls if isinstance(call, SendDocument)]
        self.assertEqual(document.chat_id, NODIR)
        self.assertEqual(document.document.filename, "eslatmalar_2026-10-12_2026-10-13.xlsx")
        sheet = load_workbook(io.BytesIO(document.document.data)).active
        self.assertEqual([row[0].value for row in sheet.iter_rows(min_row=2, max_row=4)], ["S2", "S3", "S4"])

        # another month of the calendar, and a range with nobody in it
        await self.feed(button(NODIR, "cal:-:2026-11"), button(NODIR, "pick:2026-11-01:2026-11-05"))
        self.assertTrue(any(isinstance(call, EditMessageReplyMarkup) for call in self.telegram.calls))
        self.assertEqual(self.texts(NODIR)[-1], "01.11.2026 – 05.11.2026: to‘lov qilishi kerak bo‘lganlar yo‘q.")

    async def test_long_range_has_its_own_pages_and_excel(self):
        self._add_many(25)
        await self.feed(button(NODIR, "pick:2026-10-01:2026-10-31"))
        self.assertEqual(self.buttons(NODIR), [["more:r:2026-10-01:2026-10-31:20", "xlsx:r:2026-10-01:2026-10-31"]])
        await self.feed(button(NODIR, "more:r:2026-10-01:2026-10-31:20"), button(NODIR, "xlsx:r:2026-10-01:2026-10-31"))
        second = "🗓 <b>01.10.2026 – 31.10.2026</b> (davomi)\n\n<b>12.10.2026</b>\n21. "
        self.assertTrue(self.texts(NODIR)[1].startswith(second))
        [document] = [call for call in self.telegram.calls if isinstance(call, SendDocument)]
        self.assertEqual(document.document.filename, "eslatmalar_2026-10-01_2026-10-31.xlsx")
        self.assertEqual(load_workbook(io.BytesIO(document.document.data)).active.max_row, 27)

    async def test_refused_replies_get_a_thumbs_down_and_chat_gets_nothing(self):
        await self.feed(private_text(NODIR, "/start"))
        self.telegram.calls.clear()

        # ordinary talk under a photo, a bare "+", and a dated reply to something that is not a picture
        await self.feed(
            group_reply(NODIR, "rahmat"),
            group_reply(NODIR, "+"),
            group_text(NODIR, "#Nodir, 12,10,2026 +", {"message_id": 90, "text": "salom"}),
        )
        self.assertEqual(self.telegram.calls, [])

        # named but no date, a day that does not exist, a day already gone
        await self.feed(
            group_reply(NODIR, "#Nodir +"),
            group_reply(NODIR, "Nodir, 31,11,2026 +"),
            group_reply(NODIR, "Nodir, 11,10,2026 +"),
        )
        self.assertEqual(self.reactions(), ["👎", "👎", "👎"])
        self.assertEqual(self.written_in_group(), [])
        self.assertEqual(self.reminders.pending(), [])
        self.assertEqual(self.texts(NODIR), [])

    async def test_commands_typed_in_the_group_are_not_answered(self):
        texts = ("/start", "/eslatmalar", "/chatid", "/bugun", "/oraliq", "📋 Bugungi", "🗓 Oraliq")
        await self.feed(*(group_text(NODIR, text) for text in texts))
        self.assertEqual(self.telegram.calls, [])
        self.assertEqual(self.reminders.subscribers(), [])

    async def test_bekor_cancels_a_pending_reminder(self):
        await self.feed(group_reply(NODIR, "#Nodir, 20,10,2026 +"), group_reply(BOSS, "/bekor"))
        self.assertEqual(self.reactions(), ["👍", "👍"])
        self.assertEqual(self.reminders.pending(), [])

        # nothing is pending for the photo any more; and /bekor on its own points at no photo
        await self.feed(group_reply(BOSS, "/bekor"), group_text(BOSS, "/bekor"))
        self.assertEqual(self.reactions()[2:], ["👎", "👎"])
        self.assertEqual(self.written_in_group(), [])

    UNREAD = LINK.format(100, "📷 rasmda")

    async def test_photo_that_could_not_be_read_is_sent_itself(self):
        self.read_photo.side_effect = ExtractError("model is down")
        await self.feed(private_text(NODIR, "/start"), group_reply(BOSS, "12,10,2026"))
        self.assertEqual(self.reactions(), ["👍"])
        # the photo is the reminder: no separate text, and a date without a name shows who wrote it
        [photo] = self.photos(NODIR)
        self.assertEqual(photo.caption, f"{TITLE}1. {self.UNREAD} - User502")
        self.assertEqual((photo.photo, photo.protect_content), ("F", True))
        self.assertEqual(len(self.texts(NODIR)), 1)
        self.assertEqual(self.written_in_group(), [])

    async def test_unread_photo_is_uploaded_anew_when_its_file_id_cannot_be_reused(self):
        self.read_photo.side_effect = ExtractError("model is down")
        self.telegram.refuse_file_ids = True
        await self.feed(private_text(NODIR, "/start"), group_reply(BOSS, "#Nodir 12,10,2026"))
        refused, uploaded = self.photos(NODIR)
        self.assertEqual(refused.photo, "F")
        self.assertIsInstance(uploaded.photo, BufferedInputFile)
        self.assertEqual(uploaded.photo.data, b"jpeg")
        self.assertEqual(uploaded.caption, f"{TITLE}1. {self.UNREAD} - Nodir")
        self.assertEqual(len(self.texts(NODIR)), 1)

    async def test_in_a_list_unread_photos_follow_under_their_numbers(self):
        await self.feed(private_text(NODIR, "/start"))
        self._add_many(1)
        for photo_id, file_id in ((300, "F2"), (301, None)):
            self._add(TODAY, photo_id, customer=None, order_number="S7", tag="Ali", file_id=file_id)
        await hisoblovchi.send_due(self.bot, self.reminders, NOW)
        listed = self.texts(NODIR)[1].split("\n")
        self.assertEqual(listed[3:], [
            "2. " + LINK.format(300, "S7") + " (📷 rasmda) - Ali", "3. " + LINK.format(301, "S7") + " (📷 rasmda) - Ali",
        ])
        # only the one whose photo the bot still has is sent; an older reminder without it stays text
        [photo] = self.photos(NODIR)
        self.assertEqual((photo.photo, photo.caption), ("F2", listed[3]))

    async def test_new_date_moves_a_pending_reminder_without_reading_the_photo_again(self):
        await self.feed(group_reply(NODIR, "#Nodir, 20,10,2026 +"), group_reply(NODIR, "Ali, 25,10,2026 +"))
        self.assertEqual(self.reactions(), ["👍", "👍"])
        self.assertEqual(self.read_photo.await_count, 1)
        [row] = self.reminders.pending()
        self.assertEqual((row["due_date"], row["tag"]), ("2026-10-25", "Ali"))

        await self.feed(private_text(NODIR, "/eslatmalar"))
        self.assertIn("25.10.2026 — S88784, Karimov Jasur, Ali", self.texts(NODIR)[0])

    async def test_name_saved_with_a_hash_before_is_shown_without_it(self):
        self._add(TODAY, 100, customer="Karimov Jasur", order_number="S88784", tag="#Nodir")
        await self.feed(private_text(NODIR, "/start"))
        self.assertEqual(self.texts(NODIR)[1], REMINDER)


if __name__ == "__main__":
    unittest.main()
