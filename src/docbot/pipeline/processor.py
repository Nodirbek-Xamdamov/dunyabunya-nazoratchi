"""The worker steps: how a job gets from "a file arrived" to "the file is in the Odoo record's chatter".

    received       read QR codes / barcodes, and the text layer of a PDF ......... qr_ok | ai_processing | ignored
    qr_ok          find the record in Odoo from those codes / numbers ............ ready | ai_processing | needs_review
    ai_processing  the AI reads the picture; Odoo lookup + cross-check ........... ready | needs_review | ignored
    ready          put the file into the record's chatter (idempotent per job) ... attached
    needs_review   one of our documents, but it could not be placed: the sender is asked for a clearer photo

Every picture in the chat comes through here (the user's rule: "the bot checks all messages"). A picture
that is not one of our documents is ``ignored``: no reaction, no reply, and its local copy is deleted at
once (people's private photos are not kept). "One of our documents" = one of our codes was read, or the AI
rates the picture at least ``ai.min_similarity`` % similar to our printed forms AND sees the dunyabunya
logo/name on it (a waybill of another company looks similar too; it must not get a "send it again" reply).
A picture whose number the AI reads, finds in Odoo and verifies is attached in any case.

The same file sent again is attached again (the user's rule); only a retry of the same job never adds a
second copy.

A document of several pages comes as an album or as photos one after another; every page is attached
(the user's rule). A page that does not settle itself is linked to another page of the same group (same
album, or the same person's photos within ``pages.window_seconds``), the way people tell pages apart:
the small document number in the top right corner of every page, or the goods rows numbered on (a page
starting at row 35 continues the page that ended at 34). A page waits a few seconds for pages still being
read. A middle page never gets a reply of its own, and an album gets one reply at most.

Page 1 is found by the number in its title and the last page by its QR code; the pages in between are
placed by their page numbers (printed at the bottom), which chain them to page 1 or to the QR page
(3 -> 2 -> 1). To let the QR page anchor that chain, a QR page sent together with other photos is also
read by the AI for its page number. Attachments are named with the page number ("S87295_2-bet_..."). The only thing ever written to Odoo is the attachment and its chatter note. Every step ends
with a transition (see ``WorkerPool``); network trouble raises ``TransientError`` and the step is retried.

The last page (the QR page) waits until the sender's other photos are read, then looks at the pages of its
document for a hole (the user's rule): rows 1-20, 21-40, 61-80 and 81-100 with the QR code means the page
with rows 41-60 was not sent, and the QR page gets a reply saying so (``notify.missing_pages_text``).

Only a document signed or written on with a pen is attached (the user's rule, ``notify.unsigned_text``): the AI
looks at every picture, also one placed by its QR code. An unsigned one goes to needs_review and gets the reply
"send the signed one". A signature usually stands on one page only, so an unsigned page is attached when another
page of the same document (same person, chat and record, within ``pages.window_seconds``) is signed; such a page
told "not signed" before the signed page came is let in once it comes. PDFs and a record chosen by a person are
not checked, nor a picture the AI could not look at.
"""

import asyncio
import hashlib
import logging
from collections.abc import Callable
from datetime import datetime, time, timezone
from typing import Any

from docbot import codes
from docbot.ai import AiRejected, AiResult, Extraction, OpenAIReader
from docbot.clock import Clock, utcnow
from docbot.config import AppSettings
from docbot.db.models import Job
from docbot.db.repositories.audit import AuditRepository
from docbot.documents import Documents, RecordRef
from docbot.errors import InvalidTransition, LeaseLost, PermanentError, TransientError
from docbot.odoo import OdooClient, OdooRecord
from docbot.pipeline.files import FileStore, extension
from docbot.pipeline.finder import RecordFinder
from docbot.pipeline.states import Method, Status
from docbot.pipeline.worker import JobContext
from docbot.verify import Verdict, verify

log = logging.getLogger(__name__)

AI_EVENT = "ai_read"  # audit event per AI call; counted for the daily limit
MAX_TURN_CORRECTIONS = 2  # an upright photo costs 1 AI call, a sideways one 2, a mixed-up direction 3


def record_fields(record: OdooRecord) -> dict[str, Any]:
    return {"odoo_model": record.model, "odoo_record_id": record.id, "odoo_record_name": record.name}


def candidate(record: OdooRecord) -> dict[str, Any]:
    return {"model": record.model, "id": record.id, "name": record.name}


def attachment_name(job: Job, mime: str) -> str:
    if job.file_name:
        stem, dot, suffix = job.file_name.rpartition(".")
        converted = mime == "image/jpeg" and suffix.lower() not in ("jpg", "jpeg")  # e.g. a PNG turned upright
        return f"{stem or job.file_name}.jpg" if dot and converted else job.file_name
    stem = (job.odoo_record_name or "hujjat").replace("/", "-")
    page = ((job.extracted_data or {}).get("ai") or {}).get("page_number")
    return f"{stem}{f'_{page}-bet' if page else ''}_{job.created_at:%Y%m%d_%H%M%S}{extension(mime)}"


def page_info(ai: dict[str, Any] | None) -> tuple[int | None, int | None, int | None]:
    """(page number, first goods row, last goods row) as the AI read them."""
    ai = ai or {}
    return ai.get("page_number"), ai.get("first_item"), ai.get("last_item")


def same_page(a: tuple, b: tuple) -> bool:
    """Two photos show the same page (e.g. a new photo of it), as far as their ``page_info`` tells; unknown = the same."""
    (a_page, a_first, a_last), (b_page, b_first, b_last) = a, b
    if a_page and b_page:
        return a_page == b_page
    if a_first and b_first:
        return (a_first, a_last) == (b_first, b_last)
    return True


def signed(job: Job) -> bool | None:
    """The AI saw pen marks on the picture (True), saw none (False), or did not look (None)."""
    return ((job.extracted_data or {}).get("ai") or {}).get("signed")


def comes_right_before(a: tuple, b: tuple) -> bool:
    """Page ``a`` is the page just before ``b``: the page numbers follow, or the goods rows go on."""
    (a_page, _, a_last), (b_page, b_first, _) = a, b
    if a_page and b_page:
        return a_page + 1 == b_page
    return bool(a_last and b_first and a_last + 1 == b_first)


def is_last_page(job: Job) -> bool:
    """The page a document ends with: the one carrying its QR code (or "4/4" printed at the bottom)."""
    number, count = (((job.extracted_data or {}).get("ai") or {}).get(key) for key in ("page_number", "page_count"))
    return job.method is Method.QR or bool(number and number == count)


def _span(low: int, high: int, unit: str) -> str:
    return f"{low}-{unit}" if low == high else f"{low}–{high}-{unit}lar"


def _runs(numbers: list[int]) -> list[tuple[int, int]]:
    """[3, 4, 7] -> [(3, 4), (7, 7)]"""
    runs: list[list[int]] = []
    for n in numbers:
        if runs and runs[-1][1] + 1 == n:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return [(low, high) for low, high in runs]


def missing_parts(pages: list[tuple]) -> str | None:
    """What is missing before the last page sent, from the pages' ``page_info``: "3-bet (41–60-qatorlar)" by the
    page numbers, or "41–60-qatorlar" by the goods rows. None when nothing is missing, or when some page cannot be
    placed (it might be the missing one: better silent than wrong)."""
    if len(pages) < 2:
        return None
    if all(number for number, _, _ in pages):
        rows = {number: (first, last) for number, first, last in pages}
        parts = []
        for low, high in _runs([n for n in range(1, max(rows)) if n not in rows]):
            text = _span(low, high, "bet")
            before, after = 0 if low == 1 else rows[low - 1][1], rows[high + 1][0]  # the pages around are there
            if before is not None and after is not None and before + 1 < after:
                text += f" ({_span(before + 1, after - 1, 'qator')})"
            parts.append(text)
        return ", ".join(parts) or None
    if all(first and last for _, first, last in pages):
        parts, covered = [], 0
        for _, first, last in sorted(pages, key=lambda page: page[1]):
            if first > covered + 1:
                parts.append(_span(covered + 1, first - 1, "qator"))
            covered = max(covered, last)
        return ", ".join(parts) or None
    return None


def chatter_note(job: Job) -> str:
    return f"Telegram: {job.sender_name or job.telegram_user_id}"


def job_marker(job: Job) -> str:
    """Stored as the attachment's description: a retry of this job finds its own upload."""
    return f"telegram-docbot job {job.id}"


class Processor:
    def __init__(
        self,
        *,
        settings: AppSettings,
        documents: Documents,
        finder: RecordFinder,
        odoo: OdooClient,
        ai: OpenAIReader | None,
        files: FileStore,
        audit: AuditRepository,
        on_change: Callable[[], None] = lambda: None,
        clock: Clock = utcnow,
    ):
        self._settings = settings
        self._documents = documents
        self._finder = finder
        self._odoo = odoo
        self._ai = ai
        self._files = files
        self._audit = audit
        self._on_change = on_change
        self._clock = clock

    async def __call__(self, ctx: JobContext) -> None:
        step = {
            Status.RECEIVED: self._read_codes,
            Status.QR_OK: self._find_by_codes,
            Status.AI_PROCESSING: self._read_with_ai,
            Status.READY: self._attach,
        }[ctx.job.status]
        try:
            await step(ctx)
        finally:
            self._on_change()

    # ------------------------------------------------------------------ received

    async def _read_codes(self, ctx: JobContext) -> None:
        data = await self._files.load(ctx)
        extracted: dict[str, Any] = {"sha1": hashlib.sha1(data).hexdigest()}
        try:
            scan = await asyncio.to_thread(codes.scan, data, self._settings.codes)
        except codes.UnreadableFile as exc:
            await self._ignore(ctx, f"fayl rasm yoki PDF sifatida ochilmadi ({exc})", extracted)
            return
        refs = [ref for ref in map(self._documents.from_code, scan.codes) if ref is not None]
        numbers = self._documents.find_numbers(scan.text) if scan.text else []
        extracted["codes"] = scan.codes
        extracted["rotate_cw"] = scan.rotate_cw
        qr_value = "\n".join(scan.codes) or None
        if refs or numbers:
            extracted["refs"] = [ref.as_dict() for ref in refs + numbers]
            extracted["our_code"] = True  # certainly one of our documents, whatever happens next
            method = Method.QR if refs else Method.TEXT
            await ctx.transition(Status.QR_OK, method=method, qr_value=qr_value, extracted_data=extracted)
            return
        reason = "rasmda QR/shtrix-kod o'qilmadi" if not scan.codes else "kod bizning hujjatnikiga o'xshamaydi"
        await self._to_ai(ctx, reason, extracted, qr_value=qr_value)

    # ------------------------------------------------------------------ qr_ok

    async def _find_by_codes(self, ctx: JobContext) -> None:
        extracted = dict(ctx.job.extracted_data or {})
        refs = [RecordRef.from_dict(item) for item in extracted.get("refs", [])]
        records = await self._finder.find_all(refs)
        if len(records) == 1:
            await self._read_qr_page(ctx, extracted)
            await self._ready(ctx, records[0], ctx.job.method or Method.QR, extracted)
        elif len(records) > 1:
            extracted["candidates"] = [candidate(r) for r in records]
            names = ", ".join(r.name for r in records)
            await self._ask(ctx, f"faylda bir nechta hujjat bor: {names}", extracted)
        else:
            labels = ", ".join(ref.label for ref in refs)
            await self._to_ai(ctx, f"{labels} Odoo'da topilmadi", extracted)

    async def _read_qr_page(self, ctx: JobContext, extracted: dict[str, Any]) -> None:
        """The AI also looks at a picture its QR code placed: is it signed (``notify.unsigned_text``), and its page
        number, which lets the pages of a document sent together with it chain to it.

        Without the AI's answer (switched off, daily limit, refused) the page is attached all the same; an OpenAI
        outage is waited out when the signature has to be checked.
        """
        if self._ai is None or "ai" in extracted:
            return
        check_signature = bool(self._settings.notify.unsigned_text)
        window = self._settings.pages.window_seconds
        if not check_signature and not await ctx.jobs.list_page_siblings(ctx.job, window_seconds=window):
            return
        if await self._ai_calls_today() >= self._settings.ai.daily_limit:
            log.warning("job %s: AI daily limit reached, the QR page is not looked at", ctx.job.id)
            return
        try:
            data = await self._files.load(ctx)
            if codes.is_pdf(data):
                return  # a PDF is attached whole: no pages to chain, no pen marks to check
            result = await self._ai_read(ctx, data, rotate_cw=int(extracted.get("rotate_cw") or 0))
        except (codes.UnreadableFile, AiRejected) as exc:
            log.info("job %s: the QR page not read by the AI: %s", ctx.job.id, exc)
            return
        except TransientError as exc:
            if check_signature:
                raise
            log.info("job %s: page number of the QR page not read: %s", ctx.job.id, exc)
            return
        extracted["ai"] = result.extraction.as_dict()

    # ------------------------------------------------------------------ ai_processing

    async def _read_with_ai(self, ctx: JobContext) -> None:
        extracted = dict(ctx.job.extracted_data or {})
        if "ai" not in extracted:  # a retry after an Odoo error reuses the answer instead of paying twice
            if self._ai is None:
                await self._unsure(ctx, "AI o'chirilgan", extracted)
                return
            if await self._ai_calls_today() >= self._settings.ai.daily_limit:
                log.warning("job %s: AI daily limit reached", ctx.job.id)
                await self._unsure(ctx, "AI kunlik limiti tugadi", extracted)
                return
            data = await self._files.load(ctx)
            try:
                # A sideways / upside-down photo is turned and read again. The AI sometimes mixes up left and
                # right (a real sample turned 270 was called 90): the next look then says 180, and the turns add up.
                turn = 0
                result = await self._ai_read(ctx, data, rotate_cw=0)
                for _ in range(MAX_TURN_CORRECTIONS):
                    if not result.extraction.rotation:
                        break
                    turn = (turn + result.extraction.rotation) % 360
                    result = await self._ai_read(ctx, data, rotate_cw=turn)
                extracted["rotate_cw"] = turn
            except codes.UnreadableFile as exc:
                await self._unsure(ctx, f"fayl rasm sifatida ochilmadi ({exc})", extracted)
                return
            except AiRejected as exc:
                await self._unsure(ctx, f"AI rasmni o'qiy olmadi: {exc}", extracted)
                return
            extracted["ai"] = result.extraction.as_dict()
            await ctx.update(extracted_data=extracted)

        read = Extraction.from_dict(extracted["ai"])
        ours = extracted.get("our_code") or (read.similarity >= self._settings.ai.min_similarity and read.branded)
        record, verdict = await self._record_by_number(read, extracted)
        if record is not None and verdict is not None:
            number_only = not verdict.matched and not verdict.conflicts
            trusted_number = number_only and read.similarity >= self._settings.ai.trust_number_from
            if verdict.ok or trusted_number:
                await self._ready(ctx, record, Method.AI, extracted)
                return

        # Not settled by the page itself: it may be one page of a multi-page document.
        linked = await self._link_page(ctx, read, record)
        if linked is not None:
            extracted["linked_page_of_job"] = linked[1]
            await self._ready(ctx, linked[0], Method.AI, extracted)
            return

        continuation = (read.page_number or 0) > 1 or (read.first_item or 0) > 1
        if continuation and (ours or read.similarity >= self._settings.ai.min_similarity):
            # A middle page whose document could not be found: never a reply of its own (kept for an admin).
            where = f"{read.page_number}-bet" if read.page_number else f"{read.first_item}-{read.last_item} qatorlar"
            await self._ask(ctx, f"davom sahifasi ({where}), hujjati topilmadi", extracted, quiet=True)
        elif record is None:
            reason = (
                f"AI o'qigan raqam {read.doc_number} Odoo'da topilmadi" if read.doc_number else "hujjat raqami o'qilmadi"
            ) + f" (o'xshashlik {read.similarity}%)"
            await (self._ask if ours else self._ignore)(ctx, reason, extracted)
        else:
            # The number points to a real document of ours but the rest does not agree: a blurred photo of it.
            extracted["candidate"] = candidate(record)
            await self._ask(ctx, f"{record.name}: {verdict.reason() if verdict else 'tekshirib bo‘lmadi'}", extracted)

    async def _record_by_number(self, read: Extraction, extracted: dict[str, Any]) -> tuple[OdooRecord | None, Verdict | None]:
        """The record the number on the page points to, and how the rest of the page agrees with it."""
        if not read.doc_number:
            return None, None
        refs = self._documents.from_number(read.doc_number)
        records = await self._finder.find_all(refs) if refs else []
        if not records:
            return None, None
        record = records[0]
        kind = self._documents.for_model(record.model)
        partner = await self._finder.partner_of(record, kind) if kind else None
        verdict = verify(read, record, kind, partner) if kind else None
        extracted["check"] = verdict.as_dict() if verdict else None
        return record, verdict

    async def _link_page(self, ctx: JobContext, read: Extraction, own: OdooRecord | None) -> tuple[OdooRecord, int] | None:
        """The document another page of the same group was attached to, if this page belongs to it.

        Raises a non-counting TransientError to look again shortly while other pages are still being read.
        """
        job, pages = ctx.job, self._settings.pages
        if own is None and not (read.first_item or read.page_number) and read.similarity < self._settings.ai.min_similarity:
            return None  # not a document page (a selfie in the same album must not ride along)
        siblings = await ctx.jobs.list_page_siblings(job, window_seconds=pages.window_seconds)
        placed = {s.id: s for s in siblings if s.odoo_record_id is not None and s.status in (Status.READY, Status.ATTACHED)}

        def record_of(page: Job) -> OdooRecord:
            return OdooRecord(page.odoo_model, page.odoo_record_id, page.odoo_record_name or str(page.odoo_record_id), {})

        if own is not None:  # the corner number, confirmed by another page of the group
            for page in placed.values():
                if (page.odoo_model, page.odoo_record_id) == (own.model, own.id):
                    return own, page.id
        reached = self._chain_to_placed(job, page_info(read.as_dict()), siblings, placed)
        if reached is not None:  # page numbers / goods rows lead, page by page, to a placed page
            return record_of(reached), reached.id
        if job.media_group_id and own is None:  # an album is one document when its placed pages agree
            album = {(p.odoo_model, p.odoo_record_id): p for p in placed.values() if p.media_group_id == job.media_group_id}
            if len(album) == 1:
                page = next(iter(album.values()))
                return record_of(page), page.id

        age = (self._clock() - job.created_at).total_seconds()
        being_read = [
            s for s in siblings
            if s.status in (Status.RECEIVED, Status.QR_OK)
            or (s.status is Status.AI_PROCESSING and "ai" not in (s.extracted_data or {}))  # not yet read (not waiting like us)
        ]
        album_arriving = bool(job.media_group_id) and age < pages.album_grace_seconds
        if (being_read or album_arriving) and age < pages.window_seconds:
            raise TransientError("boshqa sahifalar o'qilishi kutilmoqda", retry_after=pages.retry_seconds, counts=False)
        return None

    @staticmethod
    def _chain_to_placed(job: Job, mine: tuple, siblings: list[Job], placed: dict[int, Job]) -> Job | None:
        """Walk from this page to its neighbours (page just before / after), nearest message first, until a placed page.

        No other document's first page may have been sent between the two (in either order: an album can come
        shuffled): an unreadable page 1 of document A must not chain on to page 2 of the next document B.
        """
        info = {s.id: page_info((s.extracted_data or {}).get("ai")) for s in siblings if (s.extracted_data or {}).get("ai")}
        starts = [s.message_id for s in siblings if s.id in info and (info[s.id][0] == 1 or (info[s.id][0] is None and info[s.id][1] == 1))]

        def linked(a_msg: int, a: tuple, b_msg: int, b: tuple) -> bool:  # page a (sent as a_msg) is right before page b
            low, high = sorted((a_msg, b_msg))
            return comes_right_before(a, b) and not any(low < m < high for m in starts)

        queue, seen = [(job.message_id, mine)], set()
        while queue:
            message_id, current = queue.pop(0)
            neighbours = sorted(
                (s for s in siblings if s.id in info and s.id not in seen
                 and (linked(s.message_id, info[s.id], message_id, current) or linked(message_id, current, s.message_id, info[s.id]))),
                key=lambda s: abs(s.message_id - message_id),
            )
            for page in neighbours:
                if page.id in placed:
                    return page
                seen.add(page.id)
                queue.append((page.message_id, info[page.id]))
        return None

    async def _ai_read(self, ctx: JobContext, data: bytes, *, rotate_cw: int) -> AiResult:
        picture = await asyncio.to_thread(
            codes.image_for_ai, data, max_px=self._settings.ai.max_image_px,
            pdf_scale=self._settings.codes.pdf_scale, rotate_cw=rotate_cw,
        )
        result = await self._ai.read(picture)
        await self._audit.record(
            actor=ctx.actor,
            event=AI_EVENT,
            job_id=ctx.job.id,
            details={
                "model": result.model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "seconds": result.seconds,
                "doc_number": result.extraction.doc_number,
                "similarity": result.extraction.similarity,
                "rotation": result.extraction.rotation,
                "turned_before_reading": rotate_cw,
            },
        )
        return result

    async def _ai_calls_today(self) -> int:
        midnight = datetime.combine(self._clock().date(), time(0), tzinfo=timezone.utc)
        return await self._audit.count_since(AI_EVENT, midnight)

    # ------------------------------------------------------------------ ready

    async def _attach(self, ctx: JobContext) -> None:
        job = ctx.job
        if job.odoo_model is None or job.odoo_record_id is None:
            raise PermanentError("ready, lekin Odoo yozuvi belgilanmagan")
        if not await self._signed(ctx):
            extracted = {**(job.extracted_data or {}), "unsigned": True,
                         "candidate": {"model": job.odoo_model, "id": job.odoo_record_id, "name": job.odoo_record_name}}
            await self._ask(ctx, f"{job.odoo_record_name}: hujjatga qo'l qo'yilmagan", extracted)
            return
        await self._let_in_unsigned_pages(ctx)
        missing = await self._missing_pages(ctx)
        data = await self._files.load(ctx)
        rotate_cw = int((ctx.job.extracted_data or {}).get("rotate_cw") or 0)
        if await asyncio.to_thread(codes.needs_upright, data, rotate_cw):
            data = await asyncio.to_thread(codes.upright, data, rotate_cw)  # people should not read it sideways
        mime = codes.sniff_mime(data, ctx.job.mime_type)
        attachment_id, message_id = await self._odoo.attach_to_chatter(
            job.odoo_model,
            job.odoo_record_id,
            filename=attachment_name(ctx.job, mime),
            data=data,
            mimetype=mime,
            body=chatter_note(ctx.job),
            marker=job_marker(ctx.job),
        )
        extracted = {**(ctx.job.extracted_data or {}), "attachment_id": attachment_id, "message_id": message_id}
        if missing:
            extracted["missing_pages"] = missing  # the notifier replies to this last page
        log.info("job %s: attached to %s %s (attachment %s)", job.id, job.odoo_model, job.odoo_record_name, attachment_id)
        await ctx.transition(Status.ATTACHED, extracted_data=extracted)

    async def _signed(self, ctx: JobContext) -> bool:
        """May this file go into the chatter: it is signed, or another page of its document is (see the module doc).

        While the sender's other photos are still being read it raises a non-counting TransientError.
        """
        job, pages = ctx.job, self._settings.pages
        if not self._settings.notify.unsigned_text or job.method is Method.HUMAN or job.mime_type == "application/pdf":
            return True
        if signed(job) is not False:
            return True
        siblings = await ctx.jobs.list_page_siblings(job, window_seconds=pages.window_seconds)
        age = (self._clock() - job.created_at).total_seconds()
        being_read = [s for s in siblings if s.status in (Status.RECEIVED, Status.QR_OK, Status.AI_PROCESSING)]
        if (being_read or (siblings and age < pages.album_grace_seconds)) and age < pages.window_seconds:
            raise TransientError("hujjatning imzoli sahifasi kutilmoqda", retry_after=pages.retry_seconds, counts=False)
        mine = page_info((job.extracted_data or {}).get("ai"))
        document = await ctx.jobs.list_record_pages(job, window_seconds=pages.window_seconds)
        return any(
            page.id != job.id and signed(page) and not same_page(page_info((page.extracted_data or {}).get("ai")), mine)
            for page in document
        )

    async def _let_in_unsigned_pages(self, ctx: JobContext) -> None:
        """A signed page: the other pages of its document told "not signed" before it came are attached after all."""
        job = ctx.job
        if not self._settings.notify.unsigned_text or signed(job) is not True:
            return
        mine = page_info((job.extracted_data or {}).get("ai"))
        for page in await ctx.jobs.list_record_reviews(job, window_seconds=self._settings.pages.window_seconds):
            data = page.extracted_data or {}
            if not data.get("unsigned") or same_page(page_info(data.get("ai")), mine):
                continue
            try:
                await ctx.jobs.transition(page.id, Status.READY, worker_id=None, actor=ctx.actor, event="signed_page_came")
            except (InvalidTransition, LeaseLost):
                continue
            log.info("job %s: the signed page (job %s) came, attached after all", page.id, job.id)

    async def _missing_pages(self, ctx: JobContext) -> str | None:
        """The last page of a document sent in several photos: the pages before it that were not sent (``missing_parts``).

        Looks once the sender's other photos are read; until then it raises a non-counting TransientError.
        """
        job, pages = ctx.job, self._settings.pages
        if not self._settings.notify.missing_pages_text or job.mime_type == "application/pdf" or not is_last_page(job):
            return None
        siblings = await ctx.jobs.list_page_siblings(job, window_seconds=pages.window_seconds)
        if not siblings:
            return None  # sent alone: a one-page document
        age = (self._clock() - job.created_at).total_seconds()
        being_read = [s for s in siblings if s.status in (Status.RECEIVED, Status.QR_OK, Status.AI_PROCESSING)]
        if being_read or age < pages.album_grace_seconds:
            if age < pages.window_seconds:
                raise TransientError("hujjatning boshqa sahifalari kutilmoqda", retry_after=pages.retry_seconds, counts=False)
            log.info("job %s: other pages still being read, not checked for missing pages", job.id)
            return None
        document = await ctx.jobs.list_record_pages(job, window_seconds=pages.window_seconds)
        if any(page.message_id > job.message_id and is_last_page(page) for page in document):
            return None  # the document was sent again: its later last page tells
        missing = missing_parts([page_info((page.extracted_data or {}).get("ai")) for page in document])
        if missing:
            log.info("job %s: %s is missing %s", job.id, job.odoo_record_name, missing)
        return missing

    # ------------------------------------------------------------------ shared moves

    async def _ready(self, ctx: JobContext, record: OdooRecord, method: Method, extracted: dict[str, Any]) -> None:
        await ctx.transition(Status.READY, method=method, extracted_data=extracted, **record_fields(record))

    async def _to_ai(self, ctx: JobContext, reason: str, extracted: dict[str, Any], **fields: Any) -> None:
        extracted["no_code_reason"] = reason
        if self._ai is None:
            await self._unsure(ctx, f"{reason}; AI o'chirilgan", extracted, **fields)
            return
        await ctx.transition(Status.AI_PROCESSING, method=Method.AI, extracted_data=extracted, **fields)

    async def _unsure(self, ctx: JobContext, reason: str, extracted: dict[str, Any], **fields: Any) -> None:
        """The picture could not be read: ask for a clearer one only if it certainly is one of our documents."""
        await (self._ask if extracted.get("our_code") else self._ignore)(ctx, reason, extracted, **fields)

    async def _ask(self, ctx: JobContext, reason: str, extracted: dict[str, Any], *, quiet: bool = False, **fields: Any) -> None:
        """One of our documents that could not be placed: the notifier asks the sender for a clearer photo
        (``quiet``: no reply, e.g. a middle page of a document that was not found)."""
        if quiet:
            extracted["quiet"] = True
        log.info("job %s: needs review%s: %s", ctx.job.id, " (quiet)" if quiet else "", reason)
        await ctx.transition(Status.NEEDS_REVIEW, error=reason, extracted_data=extracted, **fields)

    async def _ignore(self, ctx: JobContext, reason: str, extracted: dict[str, Any], **fields: Any) -> None:
        """Not one of our documents: forget it silently and delete the local copy."""
        log.info("job %s: ignored: %s", ctx.job.id, reason)
        path = ctx.job.path
        await ctx.transition(Status.IGNORED, error=reason, extracted_data=extracted, **fields)
        if path:
            self._files.delete(path)
            await ctx.jobs.forget_file(ctx.job.id)
