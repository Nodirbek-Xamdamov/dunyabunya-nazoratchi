import base64
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from openpyxl import load_workbook
from PIL import Image

from app import excel, extract, picker
from app.bot import PAGE, line, page_keyboard, page_text
from app.db import Reminders
from app.extract import ExtractError, parse_answer

DAY = date(2026, 10, 15)
MORNING = datetime.combine(DAY, time(9, 0))
NOON = datetime.combine(DAY, time(12, 0))
GROUP, USER, OTHER = -1001234567890, 500, 600
# not a supergroup: its messages have no links
PLAIN_GROUP = -4123456


def _add(reminders: Reminders, due: date, photo: int = 10, chat_id: int = GROUP, **fields) -> int:
    values = {"customer": "Aliyev Vali", "order_number": "S01234", "thread_id": None, "tag": "Nodir",
              "now": NOON - timedelta(days=7), **fields}
    return reminders.add(chat_id=chat_id, photo_message_id=photo, due_date=due, created_by="Tester", **values)


def _ids(rows) -> list[int]:
    return [row["id"] for row in rows]


def _buttons(markup) -> list[str]:
    return [button.callback_data for line in markup.inline_keyboard for button in line]


class StorageTest(unittest.TestCase):
    def test_reschedule_and_cancel(self):
        reminders = Reminders(":memory:")
        rid = _add(reminders, DAY)
        self.assertEqual(reminders.pending_for_photo(GROUP, 10)["id"], rid)
        self.assertEqual(len(reminders.pending()), 1)
        reminders.reschedule(rid, DAY + timedelta(days=3))
        self.assertEqual(reminders.due(DAY), [])
        moved = reminders.due(DAY + timedelta(days=3))
        self.assertEqual((_ids(moved), moved[0]["tag"]), ([rid], "Nodir"))
        reminders.reschedule(rid, DAY, "Ali")
        self.assertEqual(reminders.due(DAY)[0]["tag"], "Ali")
        reminders.set_status(rid, "cancelled")
        self.assertIsNone(reminders.pending_for_photo(GROUP, 10))
        self.assertEqual(reminders.pending(GROUP), [])
        self.assertEqual(reminders.undelivered(USER, DAY), [])

    def test_subscribers(self):
        reminders = Reminders(":memory:")
        for chat_id in (USER, USER, OTHER):
            reminders.subscribe(chat_id, "Tester")
        self.assertEqual(sorted(reminders.subscribers()), [USER, OTHER])
        reminders.unsubscribe(USER)
        self.assertEqual(reminders.subscribers(), [OTHER])

    def test_each_chat_is_given_a_reminder_once(self):
        reminders = Reminders(":memory:")
        rid = _add(reminders, DAY)
        _add(reminders, DAY + timedelta(days=1), photo=11)
        self.assertEqual(_ids(reminders.undelivered(USER, DAY)), [rid])

        reminders.deliver([rid], USER, NOON)
        self.assertEqual(reminders.undelivered(USER, DAY), [])
        self.assertEqual(reminders.due(DAY), [])
        self.assertIsNotNone(reminders.sent_for_photo(GROUP, 10, DAY))
        # a chat that pressed /start later the same day still gets it
        self.assertEqual(_ids(reminders.undelivered(OTHER, DAY)), [rid])
        self.assertEqual(_ids(reminders.delivered_on(USER, DAY)), [rid])
        self.assertEqual(reminders.delivered_on(OTHER, DAY), [])

    def test_old_reminder_is_not_repeated_to_a_chat_that_came_later(self):
        reminders = Reminders(":memory:")
        rid = _add(reminders, DAY - timedelta(days=2))
        reminders.deliver([rid], USER, NOON - timedelta(days=2))
        self.assertEqual(reminders.undelivered(OTHER, DAY), [])

    def test_missed_reminder_reaches_everyone_on_the_day_it_goes_out(self):
        reminders = Reminders(":memory:")
        rid = _add(reminders, DAY - timedelta(days=2))
        self.assertEqual(_ids(reminders.undelivered(USER, DAY)), [rid])
        reminders.deliver([rid], USER, NOON)
        self.assertEqual(_ids(reminders.undelivered(OTHER, DAY)), [rid])
        self.assertEqual(reminders.undelivered(OTHER, DAY + timedelta(days=1)), [])

    def test_day_list_keeps_the_order_it_was_given_in(self):
        reminders = Reminders(":memory:")
        first, second, late = (_add(reminders, DAY, photo=photo) for photo in (10, 11, 12))
        reminders.deliver([second, late], USER, MORNING)
        reminders.deliver([first], USER, NOON)
        self.assertEqual(_ids(reminders.delivered_on(USER, DAY)), [second, late, first])
        self.assertEqual(reminders.delivered_on(USER, DAY + timedelta(days=1)), [])

    def test_database_made_before_the_tag_gets_the_column(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            path = Path(folder) / "old.db"
            old = sqlite3.connect(path)
            old.execute(
                "CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,"
                " thread_id INTEGER, photo_message_id INTEGER NOT NULL, due_date TEXT NOT NULL, customer TEXT,"
                " order_number TEXT, created_by TEXT, created_at TEXT NOT NULL,"
                " status TEXT NOT NULL DEFAULT 'pending')"
            )
            old.execute(
                "INSERT INTO reminders (chat_id, photo_message_id, due_date, created_at) VALUES (?, 1, ?, 'x')",
                (GROUP, DAY.isoformat()),
            )
            old.commit()
            old.close()

            reminders = Reminders(path)
            _add(reminders, DAY, photo=2, file_id="F")
            kept = [(row["tag"], row["file_id"]) for row in reminders.due(DAY)]
            self.assertEqual(kept, [(None, None), ("Nodir", "F")])
            reminders._db.close()


class TextTest(unittest.TestCase):
    def _row(self, **fields):
        reminders = Reminders(":memory:")
        _add(reminders, DAY, **fields)
        return reminders.due(DAY)[0]

    def test_line_is_order_customer_and_who(self):
        self.assertEqual(line(self._row()), '<a href="https://t.me/c/1234567890/10">S01234</a> (Aliyev Vali) - Nodir')
        # a forum topic is part of the link; an ordinary group has no message links at all
        self.assertIn("https://t.me/c/1234567890/7/10", line(self._row(thread_id=7)))
        self.assertEqual(line(self._row(chat_id=PLAIN_GROUP)), "S01234 (Aliyev Vali) - Nodir")
        # the day is written only where the list is about another day
        self.assertEqual(line(self._row(chat_id=PLAIN_GROUP), DAY), "S01234 (Aliyev Vali) - Nodir")
        late = line(self._row(chat_id=PLAIN_GROUP), DAY + timedelta(days=2))
        self.assertEqual(late, "S01234 (Aliyev Vali) - Nodir · 15.10.2026")

    def test_line_of_a_photo_that_was_not_read(self):
        def text(**fields) -> str:
            return line(self._row(chat_id=PLAIN_GROUP, **fields))

        # without a name in the reply, whoever wrote it stands in
        self.assertEqual(text(customer=None, order_number=None, tag=None), "📷 rasmda - Tester")
        self.assertEqual(text(customer=None), "S01234 (📷 rasmda) - Nodir")
        self.assertEqual(text(order_number=None), "📷 rasmda (Aliyev Vali) - Nodir")

    def test_line_escapes_html_and_cuts_long_names(self):
        text = line(self._row(customer="OOO <Baraka> & Co " + "x" * 200, tag="a<b", chat_id=PLAIN_GROUP))
        self.assertIn("OOO &lt;Baraka&gt; &amp; Co", text)
        self.assertTrue(text.endswith(" - a&lt;b"))
        self.assertLess(len(text), 200)

    def test_pages_and_buttons(self):
        reminders = Reminders(":memory:")
        for photo in range(PAGE + 5):
            _add(reminders, DAY, photo=photo)
        rows = reminders.due(DAY)

        short = page_text(rows[:2], 0, "T", DAY).split("\n")
        self.assertEqual(short[:2], ["T", ""])
        self.assertTrue(short[2].startswith("1. ") and short[3].startswith("2. "))
        # a short reminder list has no buttons; a list that was asked for offers its Excel file
        self.assertIsNone(page_keyboard("d:2026-10-15", 2, 2))
        self.assertEqual(_buttons(page_keyboard("r:2026-10-15:2026-10-15", 2, 2)), ["xlsx:r:2026-10-15:2026-10-15"])

        first = page_text(rows, 0, "T", DAY)
        self.assertIn(f"\n{PAGE}. ", first)
        self.assertNotIn(f"\n{PAGE + 1}. ", first)
        self.assertTrue(first.endswith(f"\n\nJami {PAGE + 5} ta, bu yerda 1–{PAGE}."))
        self.assertLess(len(first), 4096)
        self.assertEqual(
            _buttons(page_keyboard("d:2026-10-15", PAGE, len(rows))),
            [f"more:d:2026-10-15:{PAGE}", "xlsx:d:2026-10-15"],
        )

        last = page_text(rows, PAGE, "T", DAY)
        self.assertTrue(last.split("\n")[2].startswith(f"{PAGE + 1}. "))
        self.assertEqual(_buttons(page_keyboard("d:2026-10-15", len(rows), len(rows))), ["xlsx:d:2026-10-15"])

    def test_list_over_several_days_is_headed_day_by_day(self):
        reminders = Reminders(":memory:")
        _add(reminders, DAY, photo=1, chat_id=PLAIN_GROUP)
        _add(reminders, DAY + timedelta(days=1), photo=2, chat_id=PLAIN_GROUP, tag="Ali")
        _add(reminders, DAY + timedelta(days=1), photo=3, chat_id=PLAIN_GROUP)
        _add(reminders, DAY + timedelta(days=9), photo=4, chat_id=PLAIN_GROUP)
        rows = reminders.between(DAY, DAY + timedelta(days=1))
        self.assertEqual(page_text(rows, 0, "T", None), "\n".join([
            "T", "", "<b>15.10.2026</b>", "1. S01234 (Aliyev Vali) - Nodir",
            "", "<b>16.10.2026</b>", "2. S01234 (Aliyev Vali) - Ali", "3. S01234 (Aliyev Vali) - Nodir",
        ]))

    def test_calendar(self):
        october = picker.keyboard(date(2026, 10, 1), None, DAY).inline_keyboard
        self.assertEqual([key.callback_data for key in october[0]], ["cal:-:2026-09", "noop", "cal:-:2026-11"])
        self.assertEqual(october[0][1].text, "Oktabr 2026")
        # 1 October 2026 is a Thursday: three empty cells before it
        self.assertEqual([key.text for key in october[2]], [" ", " ", " ", "1", "2", "3", "4"])
        self.assertEqual(october[2][3].callback_data, "pick:-:2026-10-01")
        self.assertIn("·15·", [key.text for week in october[2:] for key in week])

        # once the first day is chosen it rides along; December leads on into the next year
        december = picker.keyboard(date(2026, 12, 1), date(2026, 12, 3), DAY).inline_keyboard
        self.assertEqual(december[0][2].callback_data, "cal:2026-12-03:2027-01")
        self.assertEqual(december[2][3].text, "[3]")
        self.assertEqual(december[2][4].callback_data, "pick:2026-12-03:2026-12-04")

    def test_parse_answer(self):
        found = parse_answer('{"customer": " Aliyev  Vali ", "order_number": "№ S01234"}')
        self.assertEqual((found.customer, found.order_number), ("Aliyev Vali", "S01234"))
        empty = parse_answer('{"customer": "null", "order_number": null}')
        self.assertEqual((empty.customer, empty.order_number), (None, None))
        # a label copied along with the value is dropped; digits after a name are part of the customer
        labelled = parse_answer('{"customer": "XARIDOR: Aliyev Vali 0208 (1/1)", "order_number": "S1"}')
        self.assertEqual(labelled.customer, "Aliyev Vali 0208 (1/1)")
        self.assertIsNone(parse_answer('{"customer": "Mijoz: -", "order_number": "S1"}').customer)
        with self.assertRaises(ExtractError):
            parse_answer("sorry, I cannot read it")


class ReaderTest(unittest.IsolatedAsyncioTestCase):
    """The OpenAI call itself is replaced; what is tested is what happens around it."""

    ANSWER = {
        "model": "gpt-test", "status": "completed", "usage": {"input_tokens": 900, "output_tokens": 20},
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [
                {"type": "output_text", "text": '{"customer": "Aliyev Vali 0208", "order_number": "S01234"}'},
            ]},
        ],
    }

    def setUp(self):
        self.pause = AsyncMock()
        for patcher in (
            patch.object(extract, "settings", SimpleNamespace(openai_api_key="k", openai_model="gpt-test")),
            patch.object(extract.asyncio, "sleep", self.pause),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_request_and_answer(self):
        body = extract.request_body(b"\xff\xd8", "image/jpeg")
        self.assertEqual(body["model"], "gpt-test")
        self.assertEqual(body["input"][0]["content"][1]["image_url"], "data:image/jpeg;base64,/9g=")
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertIn("Aliyev Vali 0208", extract.output_text(self.ANSWER))
        with self.assertRaises(ExtractError):
            extract.output_text({"status": "incomplete", "output": [{"type": "reasoning"}]})

    async def test_passing_failure_is_tried_again(self):
        failure = extract._Passing("OpenAI HTTP 503")
        with patch.object(extract, "ask", AsyncMock(side_effect=[failure, self.ANSWER])) as ask:
            found = await extract.read_photo(b"jpeg")
        self.assertEqual((found.customer, found.order_number), ("Aliyev Vali 0208", "S01234"))
        self.assertEqual((ask.await_count, self.pause.await_count), (2, 1))

    async def test_gives_up_after_three_passing_failures_and_at_once_on_a_rejected_key(self):
        with patch.object(extract, "ask", AsyncMock(side_effect=extract._Passing("timeout"))) as ask:
            with self.assertRaises(ExtractError):
                await extract.read_photo(b"jpeg")
        self.assertEqual(ask.await_count, extract.ATTEMPTS)

        with patch.object(extract, "ask", AsyncMock(side_effect=ExtractError("OpenAI HTTP 401"))) as ask:
            with self.assertRaises(ExtractError):
                await extract.read_photo(b"jpeg")
        self.assertEqual(ask.await_count, 1)

    @staticmethod
    def _answer(rotation: int, customer: str = "Aliyev Vali 0208") -> dict:
        text = json.dumps({"customer": customer, "order_number": "S01234", "rotation": rotation})
        message = {"type": "message", "content": [{"type": "output_text", "text": text}]}
        return {"model": "gpt-test", "output": [message]}

    @staticmethod
    def _sideways() -> bytes:
        """A page that is red on its left and blue on its right, photographed turned 90 degrees clockwise."""
        page = Image.new("RGB", (40, 20), "red")
        page.paste("blue", (20, 0, 40, 20))
        out = io.BytesIO()
        page.transpose(Image.Transpose.ROTATE_270).save(out, "JPEG", quality=95)
        return out.getvalue()

    @staticmethod
    def _sent(call) -> Image.Image:
        """The picture inside a request that went to OpenAI."""
        url = call.args[1]["input"][0]["content"][1]["image_url"]
        return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB")

    async def test_sideways_photo_is_turned_upright_and_read_again(self):
        answers = [self._answer(90, "ZAFAR AGA"), self._answer(0, "ZAFAR AKA 0660")]
        with patch.object(extract, "ask", AsyncMock(side_effect=answers)) as ask:
            found = await extract.read_photo(self._sideways())
        self.assertEqual(found.customer, "ZAFAR AKA 0660")
        first, second = (self._sent(call) for call in ask.await_args_list)
        self.assertEqual((first.size, second.size), ((20, 40), (40, 20)))
        # upright again: red on the left, blue on the right
        left, right = second.getpixel((5, 10)), second.getpixel((35, 10))
        self.assertTrue(left[0] > 200 > left[2] and right[2] > 200 > right[0], (left, right))

    async def test_turning_stops_after_three_reads_and_leaves_alone_what_is_not_a_picture(self):
        with patch.object(extract, "ask", AsyncMock(return_value=self._answer(90))) as ask:
            await extract.read_photo(self._sideways())
        self.assertEqual(ask.await_count, extract.MAX_READS)

        with patch.object(extract, "ask", AsyncMock(return_value=self._answer(90))) as ask:
            found = await extract.read_photo(b"not a picture")
        self.assertEqual((ask.await_count, found.customer), (1, "Aliyev Vali 0208"))


class ExcelTest(unittest.TestCase):
    def test_workbook_is_laid_out_like_the_accountants_sheet(self):
        data = excel.build([("S01234", "Aliyev Vali", "Nodir", DAY), (None, "=1+1", None, DAY + timedelta(days=3))])
        sheet = load_workbook(io.BytesIO(data)).active
        self.assertEqual(sheet.title, "Hujjat")
        self.assertEqual(
            [cell.value for cell in sheet[1]], ["Hujjat", "Sana", "Hafta kuni", "Mijoz", "Summa", "To‘langan", "Hodim"]
        )
        self.assertEqual(
            [cell.value for cell in sheet[2]],
            ["S01234", datetime.combine(DAY, time()), "Payshanba", "Aliyev Vali", None, "✕", "Nodir"],
        )
        self.assertEqual(sheet["C3"].value, "Yakshanba")
        # the sum is theirs to type in; its total stands ready underneath
        total = sheet["E4"]
        self.assertEqual((sheet["D4"].value, total.value, total.number_format), ("Jami:", "=SUM(E2:E3)", "#,##0.00"))
        self.assertTrue(sheet["A1"].font.bold and sheet["D4"].font.bold and not sheet["A2"].font.bold)
        self.assertEqual(sheet["D2"].alignment.horizontal, "center")
        # text that looks like a formula is kept as text
        self.assertEqual((sheet["D3"].value, sheet["D3"].data_type), ("=1+1", "s"))


if __name__ == "__main__":
    unittest.main()
