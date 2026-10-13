"""The whole flow with the real queue, workers and file handling; Odoo, the AI and Telegram are fakes."""

import asyncio
import io
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
import zxingcpp
from helpers import wait_for_status
from images import jpeg, page_with_code, text_pdf
from PIL import Image

from docbot.ai import AiResult, Extraction
from docbot.config import AiSettings, AppSettings, DocumentsConfig, NotifySettings, PagesSettings, QueueSettings
from docbot.db.repositories.audit import AuditRepository
from docbot.db.repositories.jobs import JobRepository, NewJob
from docbot.documents import Documents
from docbot.errors import TransientError
from docbot.odoo import OdooRecord
from docbot.pipeline.files import FileStore
from docbot.pipeline.finder import RecordFinder
from docbot.pipeline.processor import AI_EVENT, Processor, missing_parts
from docbot.pipeline.review import ReviewService
from docbot.pipeline.states import Method, Status
from docbot.pipeline.worker import WorkerPool
from docbot.telegram.notifier import Notifier

PROJECT_ROOT = Path(__file__).resolve().parents[1]
USER, ADMIN, CHAT = 8001, 9001, 8001  # a private chat: its id is the user's id
GROUP = -1001234567890
UNCLEAR = "📷 Hujjat aniqlanmadi. Rasmni boshqatdan tiniqroq qilib tashlang (QR kod aniq ko'rinsin)."
TOKEN = "9d0ac27f-486e-46a6-a391-36fec7eeb6a0"
PORTAL = f"https://db.example.com/my/orders/89452?access_token={TOKEN}"

FAST = QueueSettings(
    workers=2, poll_interval_seconds=0.01, lease_seconds=5, max_attempts=5,
    backoff_base_seconds=0.01, backoff_max_seconds=0.05, backoff_jitter=0,
)

# What the fake AI "reads"
DOCUMENT = dict(similarity=92, branded=True, doc_number="S82409", total=404000)  # a readable photo of S82409
BLURRED = dict(similarity=85, branded=True, doc_number=None)  # clearly our form, number unreadable
FOREIGN = dict(similarity=80, branded=False, doc_number="0001234")  # another company's waybill: similar layout
SELFIE = dict(similarity=3, doc_number=None)  # anything else people post


class FakeOdoo:
    def __init__(self):
        self.records = {
            ("sale.order", 89452): {
                "name": "S82409", "access_token": TOKEN, "amount_total": 404000.0,
                "partner_id": [81983, "MUSLIMBEK 0774"], "user_id": [196, "NABIXO‘JAYEV SAIDAZIM SAIDA'LO O‘G‘LI"],
            },
            ("stock.picking", 252495): {"name": "OD/INT/01514", "partner_id": False},
        }
        self.partners = {81983: {"name": "MUSLIMBEK 0774", "phone": "+998906000774", "mobile": False}}
        self.chatter: list[dict] = []
        self.outages = 0  # the next N attach calls fail like a network problem

    def _record(self, key) -> OdooRecord:
        return OdooRecord(key[0], key[1], self.records[key]["name"], {"id": key[1], **self.records[key]})

    async def read_by_id(self, model, record_id, fields):
        return self._record((model, record_id)) if (model, record_id) in self.records else None

    async def find_by_name(self, model, name, fields):
        return [self._record(key) for key, row in self.records.items() if key[0] == model and row["name"] == name]

    async def partner(self, partner_id):
        return self.partners.get(partner_id)

    async def attach_to_chatter(self, model, res_id, *, filename, data, mimetype, body, marker):
        if self.outages:
            self.outages -= 1
            raise TransientError("Odoo bilan aloqa yo'q: ConnectionResetError")
        for item in self.chatter:
            if (item["model"], item["res_id"], item["marker"]) == (model, res_id, marker):
                return item["id"], item["message_id"]
        item = {"id": len(self.chatter) + 1, "message_id": 700 + len(self.chatter), "model": model, "res_id": res_id,
                "marker": marker, "filename": filename, "mimetype": mimetype, "body": body, "data": data}
        self.chatter.append(item)
        return item["id"], item["message_id"]


class FakeAI:
    model = "fake-vision"

    def __init__(self, by_size: dict | None = None, **fields):
        self.rotations = list(fields.pop("rotations", []))  # what each look says about the turn, in order
        self.by_size = by_size or {}  # multi-page tests: each page has its own size, and its own answer
        self.fields = fields
        self.calls = 0

    async def read(self, jpeg_bytes: bytes) -> AiResult:
        self.calls += 1
        fields = self.by_size.get(Image.open(io.BytesIO(jpeg_bytes)).size, self.fields)
        rotation = self.rotations.pop(0) if self.rotations else fields.get("rotation", 0)
        return AiResult(Extraction(**{**fields, "rotation": rotation}), self.model, 900, 60, 0.1)


class FakeSource:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    async def download(self, file_id: str, destination: Path) -> None:
        destination.write_bytes(self.files[file_id])


class FakeBot:
    def __init__(self):
        self.sent: list[SimpleNamespace] = []
        self.reactions: list[tuple[int, int, str]] = []
        self.deleted: list[tuple[int, int]] = []

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))

    async def set_message_reaction(self, chat_id, message_id, reaction):
        self.reactions.append((chat_id, message_id, reaction[0].emoji))

    async def send_message(self, chat_id, text, message_thread_id=None, reply_parameters=None, reply_markup=None):
        message = SimpleNamespace(
            message_id=5000 + len(self.sent), chat_id=chat_id, text=text, markup=reply_markup, thread=message_thread_id,
            reply_to=reply_parameters.message_id if reply_parameters else None,
        )
        self.sent.append(message)
        return message


class Harness:
    def __init__(
        self, session_factory, tmp_path: Path, *, ai: FakeAI | None, daily_limit: int = 1000, max_bytes: int = 20_000_000,
        workers: int = 2, messages: bool = False, unclear_text: str = UNCLEAR,
    ):
        raw = yaml.safe_load((PROJECT_ROOT / "config" / "documents.yaml").read_text(encoding="utf-8"))
        self.documents = Documents(DocumentsConfig.model_validate(raw))
        self.settings = AppSettings(
            queue=FAST.model_copy(update={"workers": workers}),
            ai=AiSettings(daily_limit=daily_limit),
            pages=PagesSettings(retry_seconds=0.05, album_grace_seconds=0.3),
        )
        self.jobs = JobRepository(session_factory, FAST)
        self.audit = AuditRepository(session_factory)
        self.odoo = FakeOdoo()
        self.ai = ai
        self.source = FakeSource()
        self.bot = FakeBot()
        self.images = tmp_path / "images"
        finder = RecordFinder(self.odoo, self.documents)
        self.notifier = Notifier(
            self.bot, self.jobs, self.documents, admin_ids=[ADMIN], messages=messages, unclear_text=unclear_text,
            missing_pages_text=self.settings.notify.missing_pages_text,
        )
        self.processor = Processor(
            settings=self.settings, documents=self.documents, finder=finder, odoo=self.odoo, ai=ai,
            files=FileStore(self.images, self.source, max_bytes=max_bytes), audit=self.audit,
            on_change=self.notifier.wake,
        )
        self.review = ReviewService(self.jobs, finder, self.documents, [ADMIN])
        self._messages = 0

    async def send(
        self, data: bytes, *, mime: str = "image/jpeg", file_name: str | None = None, chat: int = CHAT,
        thread: int | None = None, album: str | None = None,
    ):
        self._messages += 1
        file_id = f"file-{self._messages}"
        self.source.files[file_id] = data
        new = NewJob(
            chat_id=chat, message_id=self._messages, message_thread_id=thread, telegram_user_id=USER, sender_name="Ali Valiyev (@ali)",
            file_id=file_id, file_unique_id=file_id, mime_type=mime, file_name=file_name, media_group_id=album,
        )
        return (await self.jobs.enqueue(new)).job

    @asynccontextmanager
    async def running(self):
        stop = asyncio.Event()
        task = asyncio.create_task(WorkerPool(self.jobs, self.processor, self.settings.queue).run(stop))
        try:
            yield
        finally:
            stop.set()
            await asyncio.wait_for(task, timeout=10)

    async def process(self, *jobs, until=(Status.ATTACHED, Status.NEEDS_REVIEW, Status.IGNORED, Status.FAILED)):
        async with self.running():
            for job in jobs:
                await wait_for_status(self.jobs, job.id, *until)
        await self.notifier.flush()


def make(session_factory, tmp_path, fields: dict | None = None, **kwargs) -> Harness:
    return Harness(session_factory, tmp_path, ai=FakeAI(**fields) if fields is not None else None, **kwargs)


@pytest.fixture
def harness(session_factory, tmp_path):
    return make(session_factory, tmp_path, DOCUMENT)


# ----------------------------------------------------------------------------- our codes


async def test_a_qr_photo_goes_into_the_chatter_with_only_a_like(harness):
    job = await harness.send(jpeg(page_with_code(PORTAL)), chat=GROUP)
    await harness.process(job)
    done = await harness.jobs.get(job.id)
    assert (done.status, done.method, done.odoo_model, done.odoo_record_id) == (Status.ATTACHED, Method.QR, "sale.order", 89452)
    [item] = harness.odoo.chatter
    assert (item["mimetype"], item["body"], item["marker"]) == ("image/jpeg", "Telegram: Ali Valiyev (@ali)", f"telegram-docbot job {job.id}")
    assert item["filename"].startswith("S82409_") and item["filename"].endswith(".jpg")
    assert harness.ai.calls == 0  # the QR was enough
    assert harness.bot.reactions == [(GROUP, job.message_id, "👍")]
    assert harness.bot.sent == []  # no reply for a document that was placed


async def test_a_waybill_barcode_finds_the_warehouse_document_by_name(harness):
    job = await harness.send(jpeg(page_with_code("OD/INT/01514", zxingcpp.BarcodeFormat.Code128, code_px=420)))
    await harness.process(job)
    assert (harness.odoo.chatter[0]["model"], harness.odoo.chatter[0]["res_id"]) == ("stock.picking", 252495)


async def test_a_pdf_without_a_qr_is_found_by_the_number_in_its_text(harness):
    job = await harness.send(
        text_pdf("XARIDORDAN BUYURTMA No S82409", "Xaridor: MUSLIMBEK 0774"),
        mime="application/pdf", file_name="Xaridordan_buyurtma_S82409.pdf",
    )
    await harness.process(job)
    assert (await harness.jobs.get(job.id)).method is Method.TEXT
    assert (harness.odoo.chatter[0]["filename"], harness.odoo.chatter[0]["mimetype"]) == ("Xaridordan_buyurtma_S82409.pdf", "application/pdf")


async def test_the_same_document_sent_again_is_attached_again(harness):
    data = jpeg(page_with_code(PORTAL))
    first, second = await harness.send(data), await harness.send(data)
    await harness.process(first, second)
    assert [(await harness.jobs.get(j.id)).status for j in (first, second)] == [Status.ATTACHED, Status.ATTACHED]
    assert sorted(item["marker"] for item in harness.odoo.chatter) == sorted(
        f"telegram-docbot job {j.id}" for j in (first, second)
    )
    assert sorted(r[2] for r in harness.bot.reactions) == ["👍", "👍"]


async def test_an_odoo_outage_is_waited_out_without_a_second_copy(harness):
    harness.odoo.outages = 3
    job = await harness.send(jpeg(page_with_code(PORTAL)))
    await harness.process(job)
    assert (await harness.jobs.get(job.id)).status is Status.ATTACHED
    assert len(harness.odoo.chatter) == 1


# ----------------------------------------------------------------------------- pictures without our code (AI)


async def test_any_other_picture_is_ignored_silently_and_not_kept(session_factory, tmp_path):
    h = make(session_factory, tmp_path, SELFIE)
    job = await h.send(jpeg(page_with_code(None, title="birthday party")), chat=GROUP)
    await h.process(job)
    ignored = await h.jobs.get(job.id)
    assert ignored.status is Status.IGNORED and ignored.path is None
    assert not any(p.is_file() for p in h.images.rglob("*"))  # the photo was deleted at once
    assert (h.bot.sent, h.bot.reactions, h.odoo.chatter) == ([], [], [])


async def test_a_readable_photo_of_our_document_is_attached_by_the_ai(harness):
    job = await harness.send(jpeg(page_with_code(None)))
    await harness.process(job)
    done = await harness.jobs.get(job.id)
    assert (done.status, done.method, done.odoo_record_name) == (Status.ATTACHED, Method.AI, "S82409")
    assert done.extracted_data["check"]["matched"] == ["total"]
    assert (await harness.audit.count_since(AI_EVENT, done.created_at)) == 1


async def test_a_blurred_photo_of_our_document_gets_one_reply_asking_for_a_clearer_one(session_factory, tmp_path):
    h = make(session_factory, tmp_path, BLURRED)
    job = await h.send(jpeg(page_with_code(None)), chat=GROUP, thread=77)  # posted in forum topic 77
    await h.process(job)
    assert (await h.jobs.get(job.id)).status is Status.NEEDS_REVIEW
    [reply] = h.bot.sent
    assert (reply.chat_id, reply.thread, reply.reply_to, reply.text, reply.markup) == (GROUP, 77, job.message_id, UNCLEAR, None)
    assert h.bot.reactions == []
    assert await h.notifier.flush() == 0  # asked once


async def test_another_companys_waybill_is_ignored_even_if_it_looks_similar(session_factory, tmp_path):
    h = make(session_factory, tmp_path, FOREIGN)
    job = await h.send(jpeg(page_with_code(None)), chat=GROUP)
    await h.process(job)
    assert (await h.jobs.get(job.id)).status is Status.IGNORED
    assert (h.bot.sent, h.bot.reactions) == ([], [])


async def test_a_blurred_photo_without_our_logo_visible_is_ignored(session_factory, tmp_path):
    h = make(session_factory, tmp_path, dict(similarity=85, branded=False, doc_number=None))
    job = await h.send(jpeg(page_with_code(None)), chat=GROUP)
    await h.process(job)
    assert (await h.jobs.get(job.id)).status is Status.IGNORED
    assert h.bot.sent == []


async def test_a_clear_photo_is_attached_when_the_customer_agrees_though_the_total_changed(session_factory, tmp_path):
    h = make(session_factory, tmp_path, dict(similarity=100, branded=True, doc_number="S82409", partner="MUSLIMBEK 0774", total=555000))
    job = await h.send(jpeg(page_with_code(None)))
    await h.process(job)
    assert (await h.jobs.get(job.id)).status is Status.ATTACHED


async def test_a_clearly_read_number_alone_is_trusted(session_factory, tmp_path):
    h = make(session_factory, tmp_path, dict(similarity=95, branded=True, doc_number="S82409"))
    job = await h.send(jpeg(page_with_code(None)))
    await h.process(job)
    assert (await h.jobs.get(job.id)).status is Status.ATTACHED


async def test_a_barely_read_number_alone_is_not_trusted(session_factory, tmp_path):
    h = make(session_factory, tmp_path, dict(similarity=75, branded=True, doc_number="S82409"))
    job = await h.send(jpeg(page_with_code(None)))
    await h.process(job)
    assert (await h.jobs.get(job.id)).status is Status.NEEDS_REVIEW


async def test_after_a_requeue_the_file_is_attached_and_the_old_reply_removed(session_factory, tmp_path):
    h = make(session_factory, tmp_path, dict(similarity=75, branded=True, doc_number="S82409"))
    job = await h.send(jpeg(page_with_code(None)), chat=GROUP)
    await h.process(job)
    [reply] = h.bot.sent
    h.ai.fields = dict(DOCUMENT)  # e.g. the rules were fixed; an admin runs `docbot requeue --review`
    await h.jobs.transition(job.id, Status.RECEIVED, worker_id=None, actor="admin:cli", event="requeued")
    await h.process(job, until=(Status.ATTACHED,))
    assert h.bot.deleted == [(GROUP, reply.message_id)]
    assert h.bot.reactions == [(GROUP, job.message_id, "👍")]


async def test_a_requeued_file_that_is_still_unclear_is_not_asked_twice(session_factory, tmp_path):
    h = make(session_factory, tmp_path, BLURRED)
    job = await h.send(jpeg(page_with_code(None)), chat=GROUP)
    await h.process(job)
    await h.jobs.transition(job.id, Status.RECEIVED, worker_id=None, actor="admin:cli", event="requeued")
    await h.process(job, until=(Status.NEEDS_REVIEW,))
    assert len(h.bot.sent) == 1


async def test_a_document_whose_details_disagree_is_not_attached_but_asked_again(session_factory, tmp_path):
    h = make(session_factory, tmp_path, dict(similarity=80, doc_number="S82409", total=999000))
    job = await h.send(jpeg(page_with_code(None)))
    await h.process(job)
    waiting = await h.jobs.get(job.id)
    assert (waiting.status, waiting.error) == (Status.NEEDS_REVIEW, "S82409: mos kelmadi: summa")
    assert h.odoo.chatter == [] and [m.text for m in h.bot.sent] == [UNCLEAR]


async def test_a_number_that_is_not_in_odoo_on_an_unlike_picture_is_ignored(session_factory, tmp_path):
    h = make(session_factory, tmp_path, dict(similarity=20, branded=True, doc_number="S11111"))
    job = await h.send(jpeg(page_with_code(None)))
    await h.process(job)
    assert (await h.jobs.get(job.id)).status is Status.IGNORED
    assert h.bot.sent == []


async def test_our_qr_from_another_database_is_read_by_the_ai(harness):
    job = await harness.send(jpeg(page_with_code(PORTAL.replace(TOKEN, "00000000-0000-0000-0000-000000000000"))))
    await harness.process(job)
    assert (await harness.jobs.get(job.id)).method is Method.AI
    assert harness.ai.calls == 1


async def test_our_qr_that_leads_nowhere_asks_for_a_clearer_photo_even_if_the_ai_is_unsure(session_factory, tmp_path):
    h = make(session_factory, tmp_path, SELFIE)  # the AI sees nothing, but the QR proves it is our form
    job = await h.send(jpeg(page_with_code(PORTAL.replace("89452", "1"))))
    await h.process(job)
    assert (await h.jobs.get(job.id)).status is Status.NEEDS_REVIEW
    assert [m.text for m in h.bot.sent] == [UNCLEAR]


async def test_without_ai_a_picture_without_our_code_is_ignored(session_factory, tmp_path):
    h = make(session_factory, tmp_path, None)
    job = await h.send(jpeg(page_with_code(None)))
    await h.process(job)
    assert (await h.jobs.get(job.id)).status is Status.IGNORED


async def test_the_ai_answer_is_not_paid_for_twice_when_odoo_fails_after_it(harness):
    harness.odoo.outages = 2
    job = await harness.send(jpeg(page_with_code(None)))
    await harness.process(job)
    assert harness.ai.calls == 1


async def test_after_the_daily_ai_limit_pictures_without_a_code_are_ignored(session_factory, tmp_path):
    # One worker: the limit is checked before the call, so parallel workers may overshoot it by a few calls.
    h = make(session_factory, tmp_path, DOCUMENT, daily_limit=1, workers=1)
    first, second = await h.send(jpeg(page_with_code(None, title="A"))), await h.send(jpeg(page_with_code(None, title="B")))
    await h.process(first, second)
    assert (await h.jobs.get(first.id)).status is Status.ATTACHED
    assert ((await h.jobs.get(second.id)).status, (await h.jobs.get(second.id)).error) == (Status.IGNORED, "AI kunlik limiti tugadi")
    assert h.ai.calls == 1


# ----------------------------------------------------------------------------- failures and the optional text mode


async def test_nothing_is_written_into_a_paused_group(session_factory, tmp_path):
    from docbot.telegram.chats import ChatRegistry

    h = make(session_factory, tmp_path, None)
    chats = ChatRegistry(tmp_path / "chats.json", [])
    chats.add(GROUP, title="Ombor", added_by=ADMIN)
    h.notifier._chats = chats
    job = await h.send(jpeg(page_with_code(PORTAL)), chat=GROUP)
    chats.pause_all()
    await h.process(job)
    assert (await h.jobs.get(job.id)).status is Status.ATTACHED
    assert h.bot.reactions == []  # held back while paused
    chats.go_live()
    await h.notifier.flush()
    assert h.bot.reactions == [(GROUP, job.message_id, "👍")]


async def test_a_sideways_photo_is_read_and_attached_upright(harness):
    page = page_with_code(PORTAL)  # portrait: 1080 x 1280
    job = await harness.send(jpeg(page.rotate(90, expand=True)))  # the phone was held sideways
    await harness.process(job)
    done = await harness.jobs.get(job.id)
    assert (done.status, done.extracted_data["rotate_cw"]) == (Status.ATTACHED, 90)
    attached = Image.open(io.BytesIO(harness.odoo.chatter[0]["data"]))
    assert attached.size == (1080, 1280)  # upright again, as people want to read it


async def test_the_ai_reads_a_sideways_photo_again_the_right_way_up(session_factory, tmp_path):
    h = make(session_factory, tmp_path, dict(DOCUMENT, rotations=[270, 0]))
    job = await h.send(jpeg(page_with_code(None).rotate(-90, expand=True)))
    await h.process(job)
    done = await h.jobs.get(job.id)
    assert (done.status, done.extracted_data["rotate_cw"]) == (Status.ATTACHED, 270)
    assert h.ai.calls == 2  # first look, then the upright read
    assert Image.open(io.BytesIO(h.odoo.chatter[0]["data"])).size == (1080, 1280)


async def test_a_mixed_up_turn_is_corrected_by_the_next_look(session_factory, tmp_path):
    # Turned 270 but first called 90: after that turn the page is upside down, the next look says 180.
    h = make(session_factory, tmp_path, dict(DOCUMENT, rotations=[90, 180, 0]))
    job = await h.send(jpeg(page_with_code(None).rotate(-90, expand=True)))
    await h.process(job)
    done = await h.jobs.get(job.id)
    assert (done.extracted_data["rotate_cw"], h.ai.calls) == (270, 3)
    assert Image.open(io.BytesIO(h.odoo.chatter[0]["data"])).size == (1080, 1280)


async def test_a_failed_file_gets_a_dislike_and_the_admins_a_message(session_factory, tmp_path):
    h = make(session_factory, tmp_path, None, max_bytes=10)
    job = await h.send(jpeg(page_with_code(PORTAL)))
    await h.process(job)
    assert (await h.jobs.get(job.id)).status is Status.FAILED
    assert h.bot.reactions == [(CHAT, job.message_id, "👎")]
    assert [(m.chat_id, m.text.split(":")[0]) for m in h.bot.sent] == [(ADMIN, f"⚠️ Job {job.id} (Ali Valiyev (@ali)) failed")]
    assert await h.notifier.flush() == 0  # told once


async def test_text_mode_asks_with_buttons_and_yes_attaches(session_factory, tmp_path):
    h = make(session_factory, tmp_path, dict(similarity=80, doc_number="S82409", total=999000), messages=True)
    job = await h.send(jpeg(page_with_code(None)))
    await h.process(job)
    [question] = h.bot.sent
    assert question.reply_to == job.message_id and "S82409" in question.text
    buttons = [b.callback_data for row in question.markup.inline_keyboard for b in row]
    assert buttons == [f"rv:{job.id}:yes", f"rv:{job.id}:skip"]
    assert (await h.jobs.get(job.id)).review_message_id == question.message_id
    answer = await h.review.press(USER, job.id, "yes")
    assert answer.done and "S82409" in answer.text
    await h.process(job, until=(Status.ATTACHED,))
    assert (await h.jobs.get(job.id)).method is Method.HUMAN


async def test_text_mode_answers_by_number_and_only_the_sender_or_an_admin_decides(session_factory, tmp_path):
    h = make(session_factory, tmp_path, BLURRED, messages=True)
    first, second = await h.send(jpeg(page_with_code(None, title="A"))), await h.send(jpeg(page_with_code(None, title="B")))
    await h.process(first, second)
    many = await h.review.answer_text(USER, "S82409", reply_to=None, chat_id=CHAT)
    assert not many.ok and "2 ta fayl" in many.text
    assert await h.review.answer_text(4242, "S82409", reply_to=None) is None  # someone with nothing waiting
    assert not (await h.review.answer_text(USER, "salom", reply_to=first.message_id, chat_id=CHAT)).is_number
    assert (await h.review.answer_text(USER, "s82409", reply_to=first.message_id, chat_id=CHAT)).ok
    stranger = await h.review.press(4242, second.id, "skip")
    assert not stranger.done
    assert (await h.review.press(ADMIN, second.id, "skip")).done
    await h.process(first, until=(Status.ATTACHED,))
    assert (await h.jobs.get(second.id)).status is Status.REJECTED


# ----------------------------------------------------------------------------- documents of several pages


def page(n: int, code: str | None = None) -> bytes:
    """Page n of a document: each page has its own height, so the fake AI knows which one it looks at."""
    return jpeg(page_with_code(code, size=(1080, 1280 + n)))


def size(n: int) -> tuple[int, int]:
    return (1080, 1280 + n)


MIDDLE = dict(similarity=85, doc_number=None)  # only goods rows: no title, no customer, corner number unreadable


def pages_of(h: Harness) -> list[tuple[str, int]]:
    return sorted((item["model"], item["res_id"]) for item in h.odoo.chatter)


async def test_every_page_of_an_album_is_attached(session_factory, tmp_path):
    h = make(session_factory, tmp_path, SELFIE)
    h.ai.by_size = {size(2): dict(MIDDLE, first_item=35, last_item=68), size(3): dict(MIDDLE, first_item=69, last_item=80)}
    jobs = [await h.send(page(1, PORTAL), chat=GROUP, album="A1"), await h.send(page(2), chat=GROUP, album="A1"),
            await h.send(page(3), chat=GROUP, album="A1")]
    await h.process(*jobs)
    assert [(await h.jobs.get(j.id)).status for j in jobs] == [Status.ATTACHED] * 3
    assert pages_of(h) == [("sale.order", 89452)] * 3
    assert sorted(r[2] for r in h.bot.reactions) == ["👍"] * 3 and h.bot.sent == []


async def test_pages_sent_one_after_another_are_joined_by_the_row_numbers(session_factory, tmp_path):
    h = make(session_factory, tmp_path, SELFIE, workers=1)
    h.ai.by_size = {size(1): dict(DOCUMENT, first_item=1, last_item=34), size(2): dict(MIDDLE, first_item=35, last_item=60)}
    first, second = await h.send(page(1)), await h.send(page(2))
    await h.process(first, second)
    assert (await h.jobs.get(second.id)).extracted_data["linked_page_of_job"] == first.id
    assert pages_of(h) == [("sale.order", 89452)] * 2


async def test_the_small_corner_number_places_a_middle_page(session_factory, tmp_path):
    h = make(session_factory, tmp_path, SELFIE, workers=1)
    h.ai.by_size = {size(1): dict(DOCUMENT, first_item=1, last_item=34),
                    size(2): dict(similarity=84, doc_number="S82409", first_item=None)}  # rows unreadable, corner readable
    first, second = await h.send(page(1)), await h.send(page(2))
    await h.process(first, second)
    assert pages_of(h) == [("sale.order", 89452)] * 2


async def test_a_new_document_is_not_glued_to_the_previous_one(session_factory, tmp_path):
    h = make(session_factory, tmp_path, SELFIE, workers=1)
    h.ai.by_size = {size(1): dict(DOCUMENT, first_item=1, last_item=34), size(2): dict(MIDDLE, first_item=1, last_item=20)}
    first, second = await h.send(page(1)), await h.send(page(2))
    await h.process(first, second)
    assert pages_of(h) == [("sale.order", 89452)]  # the second photo starts at row 1: another document
    assert (await h.jobs.get(second.id)).status is not Status.ATTACHED


async def test_a_middle_page_that_cannot_be_placed_gets_no_reply(session_factory, tmp_path):
    h = make(session_factory, tmp_path, dict(MIDDLE, first_item=35, last_item=60))
    job = await h.send(page(1), chat=GROUP)
    await h.process(job)
    assert (await h.jobs.get(job.id)).status is Status.NEEDS_REVIEW
    assert (h.bot.sent, h.bot.reactions) == ([], [])


async def test_an_album_that_cannot_be_read_gets_one_reply(session_factory, tmp_path):
    h = make(session_factory, tmp_path, BLURRED)
    jobs = [await h.send(page(n), chat=GROUP, album="A2") for n in (1, 2, 3)]
    await h.process(*jobs)
    assert [(await h.jobs.get(j.id)).status for j in jobs] == [Status.NEEDS_REVIEW] * 3
    assert [m.text for m in h.bot.sent] == [UNCLEAR]


async def test_a_selfie_in_an_album_is_not_attached_with_the_document(session_factory, tmp_path):
    h = make(session_factory, tmp_path, SELFIE)
    doc, selfie = await h.send(page(1, PORTAL), chat=GROUP, album="A3"), await h.send(page(2), chat=GROUP, album="A3")
    await h.process(doc, selfie)
    assert (await h.jobs.get(selfie.id)).status is Status.IGNORED
    assert pages_of(h) == [("sale.order", 89452)]


async def test_a_page_waits_for_the_page_still_being_read(session_factory, tmp_path):
    h = make(session_factory, tmp_path, SELFIE, workers=1)
    h.ai.by_size = {size(1): dict(DOCUMENT, first_item=1, last_item=34), size(2): dict(MIDDLE, first_item=35, last_item=60)}
    second = await h.send(page(2))  # the continuation arrives (and is read) first
    first = await h.send(page(1))
    await h.process(second, first)
    assert pages_of(h) == [("sale.order", 89452)] * 2


async def test_middle_pages_are_placed_by_their_page_numbers(session_factory, tmp_path):
    # Page 1 by its title number, pages 2 and 3 only by the page number at the bottom (rows unreadable).
    h = make(session_factory, tmp_path, SELFIE, workers=1)
    h.ai.by_size = {size(1): dict(DOCUMENT, page_number=1), size(2): dict(MIDDLE, page_number=2),
                    size(3): dict(MIDDLE, page_number=3)}
    jobs = [await h.send(page(n)) for n in (1, 2, 3)]
    await h.process(*jobs)
    assert pages_of(h) == [("sale.order", 89452)] * 3
    names = sorted(item["filename"].split("_")[1] for item in h.odoo.chatter)
    assert names == ["1-bet", "2-bet", "3-bet"]


async def test_the_qr_on_the_last_page_anchors_the_pages_before_it(session_factory, tmp_path):
    # Page 1 unreadable; page 2 has only its page number; page 3 carries the QR code.
    h = make(session_factory, tmp_path, SELFIE, workers=1)
    h.ai.by_size = {size(1): dict(similarity=85, doc_number=None, page_number=1),
                    size(2): dict(MIDDLE, page_number=2), size(3): dict(MIDDLE, page_number=3)}
    jobs = [await h.send(page(1)), await h.send(page(2)), await h.send(page(3, PORTAL))]
    await h.process(*jobs)
    assert (await h.jobs.get(jobs[1].id)).status is Status.ATTACHED  # page 2 chained to the QR page 3
    assert ("sale.order", 89452) in pages_of(h) and len(pages_of(h)) >= 2


async def test_an_unreadable_page_1_does_not_chain_on_to_the_next_document(session_factory, tmp_path):
    # A1 (unreadable page 1 of document A), then B1 and B2: A1 must not ride on B2's "page 2".
    h = make(session_factory, tmp_path, SELFIE, workers=1)
    h.ai.by_size = {size(1): dict(similarity=85, doc_number=None, page_number=1),
                    size(2): dict(DOCUMENT, page_number=1), size(3): dict(MIDDLE, page_number=2)}
    a1, b1, b2 = await h.send(page(1)), await h.send(page(2)), await h.send(page(3))
    await h.process(a1, b1, b2)
    assert (await h.jobs.get(a1.id)).status is not Status.ATTACHED
    assert pages_of(h) == [("sale.order", 89452)] * 2  # B1 and B2


async def test_a_page_1_is_never_chained_to_the_previous_document(session_factory, tmp_path):
    h = make(session_factory, tmp_path, SELFIE, workers=1)
    h.ai.by_size = {size(1): dict(DOCUMENT, page_number=1), size(2): dict(MIDDLE, page_number=1)}
    first, second = await h.send(page(1)), await h.send(page(2))
    await h.process(first, second)
    assert pages_of(h) == [("sale.order", 89452)]


# Rows 1-20, 21-40, 61-80 and 81-100 ending with the QR code: the page with rows 41-60 was not sent.
ROWS_WITHOUT_41_60 = {size(1): dict(DOCUMENT, first_item=1, last_item=20), size(2): dict(MIDDLE, first_item=21, last_item=40),
                      size(3): dict(MIDDLE, first_item=61, last_item=80), size(4): dict(MIDDLE, first_item=81, last_item=100)}
MISSING_41_60 = "⚠️ S82409 hujjatida sahifa yetishmayapti: 41–60-qatorlar. Shu sahifani ham tiniq qilib tashlang."


async def test_a_page_missing_in_the_middle_gets_a_reply_on_the_qr_page(session_factory, tmp_path):
    h = make(session_factory, tmp_path, SELFIE, workers=1)
    h.ai.by_size = ROWS_WITHOUT_41_60
    jobs = [await h.send(page(1), chat=GROUP), await h.send(page(2), chat=GROUP), await h.send(page(3), chat=GROUP),
            await h.send(page(4, PORTAL), chat=GROUP)]
    await h.process(*jobs)
    assert pages_of(h) == [("sale.order", 89452)] * 4  # every page sent is attached all the same
    assert [(m.text, m.reply_to) for m in h.bot.sent] == [(MISSING_41_60, jobs[3].message_id)]
    assert sorted(r[2] for r in h.bot.reactions) == ["👍"] * 4


async def test_a_page_missing_from_an_album_gets_one_reply(session_factory, tmp_path):
    h = make(session_factory, tmp_path, SELFIE)
    h.ai.by_size = ROWS_WITHOUT_41_60
    jobs = [await h.send(page(n, PORTAL if n == 4 else None), chat=GROUP, album="A5") for n in (1, 2, 3, 4)]
    await h.process(*jobs)
    assert [m.text for m in h.bot.sent] == [MISSING_41_60]


async def test_the_missing_page_is_named_by_its_page_number(session_factory, tmp_path):
    h = make(session_factory, tmp_path, SELFIE, workers=1)
    h.ai.by_size = {size(1): dict(DOCUMENT, page_number=1, first_item=1, last_item=20),
                    size(2): dict(MIDDLE, page_number=2, first_item=21, last_item=40),
                    size(4): dict(MIDDLE, page_number=4, first_item=61, last_item=80)}
    jobs = [await h.send(page(1)), await h.send(page(2)), await h.send(page(4, PORTAL))]
    await h.process(*jobs)
    assert [m.text for m in h.bot.sent] == [
        "⚠️ S82409 hujjatida sahifa yetishmayapti: 3-bet (41–60-qatorlar). Shu sahifani ham tiniq qilib tashlang."
    ]


async def test_a_whole_document_gets_no_reply(session_factory, tmp_path):
    h = make(session_factory, tmp_path, SELFIE, workers=1)
    h.ai.by_size = {size(1): dict(DOCUMENT, first_item=1, last_item=20), size(2): dict(MIDDLE, first_item=21, last_item=40),
                    size(3): dict(MIDDLE, first_item=41, last_item=55)}
    jobs = [await h.send(page(1)), await h.send(page(2)), await h.send(page(3, PORTAL))]
    await h.process(*jobs)
    assert pages_of(h) == [("sale.order", 89452)] * 3
    assert h.bot.sent == []


async def test_no_reply_about_missing_pages_when_it_is_switched_off(session_factory, tmp_path):
    h = make(session_factory, tmp_path, SELFIE, workers=1)
    h.processor._settings = h.settings.model_copy(update={"notify": NotifySettings(missing_pages_text="")})
    h.ai.by_size = ROWS_WITHOUT_41_60
    jobs = [await h.send(page(n, PORTAL if n == 4 else None)) for n in (1, 2, 3, 4)]
    await h.process(*jobs)
    assert pages_of(h) == [("sale.order", 89452)] * 4 and h.bot.sent == []


@pytest.mark.parametrize(
    ("pages", "missing"),
    [
        ([(None, 1, 20), (None, 21, 40), (None, 61, 80), (None, 81, 100)], "41–60-qatorlar"),
        ([(None, 1, 20), (None, 20, 40), (None, 60, 80), (None, 80, 100)], "41–59-qatorlar"),  # as the user put it
        ([(None, 41, 60), (None, 61, 80)], "1–40-qatorlar"),  # page 1 not sent
        ([(None, 1, 20), (None, 22, 30)], "21-qator"),
        ([(None, 1, 20), (None, 41, 60), (None, 81, 90)], "21–40-qatorlar, 61–80-qatorlar"),
        ([(None, 1, 20), (None, 21, 40), (None, 41, 50)], None),
        ([(None, 1, 20), (None, 1, 20), (None, 21, 30)], None),  # the same page twice
        ([(1, 1, 20), (2, 21, 40), (4, 61, 80)], "3-bet (41–60-qatorlar)"),
        ([(1, None, None), (4, None, None)], "2–3-betlar"),
        ([(2, 21, 40), (3, 41, 50)], "1-bet (1–20-qatorlar)"),
        ([(1, 1, 20), (2, 25, 40), (3, 41, 50)], None),  # page numbers follow: a misread row does not count
        ([(None, 1, 20), (None, None, None), (None, 61, 80)], None),  # an unplaced page may be the missing one
        ([(None, 61, 80)], None),  # a single page
    ],
)
def test_missing_parts(pages, missing):
    assert missing_parts(pages) == missing
