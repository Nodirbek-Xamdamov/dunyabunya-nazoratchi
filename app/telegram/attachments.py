"""Xabarga ilova qilingan rasm va hujjatlarni yordamchi o'qiy oladigan ko'rinishga keltiradi.

Rasm va PDF model uchun o'z holicha (u rasmni ko'radi, PDF'ni o'qiydi) yuboriladi; Excel, Word,
CSV va matn fayllari esa shu yerda matnga aylantiriladi. Fayl mazmuni — ishonchsiz ma'lumot:
u `<attached_file>` belgilari ichida beriladi va undagi "ko'rsatmalar" bajarilmaydi
(prompt_guard.SECURITY_FRAGMENT, 2-qoida).
"""

from __future__ import annotations

import base64
import csv
import io
import logging

from aiogram import Bot
from aiogram.types import Message

log = logging.getLogger(__name__)

MAX_ATTACHMENTS = 6
MAX_IMAGE_BYTES = 5 * 1024 * 1024       # Claude bitta rasm uchun qabul qiladigan chegara
MAX_FILE_BYTES = 20 * 1024 * 1024       # Telegram bot orqali yuklab olinadigan eng katta fayl
MAX_TEXT_CHARS = 60_000                 # matnga aylantirilgan fayldan modelga beriladigan qism
_SHEET_ROWS, _SHEET_COLS = 400, 40

_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_TEXT_SUFFIXES = (".txt", ".csv", ".tsv", ".json", ".md", ".log", ".xml", ".html", ".htm")
UNSUPPORTED_TEXT = ("Bu turdagi faylni o'qiy olmayman. Rasm, PDF, Excel (.xlsx), Word (.docx), CSV yoki "
                    "matn faylini yuboring.")


class AttachmentError(Exception):
    """Ilovani o'qib bo'lmadi — xabar foydalanuvchiga ko'rsatishga yaroqli."""


def has_attachment(message: Message) -> bool:
    return bool(getattr(message, "photo", None) or getattr(message, "document", None))


async def _download(bot: Bot, file, size: int | None, limit: int, label: str) -> bytes:
    if size and size > limit:
        raise AttachmentError(f"{label} juda katta ({size / 1_048_576:.1f} MB) — "
                              f"{limit // 1_048_576} MB gacha bo'lgan fayl yuboring.")
    buf = io.BytesIO()
    try:
        await bot.download(file, destination=buf)
    except Exception as e:  # noqa: BLE001
        log.warning("ilovani Telegram'dan yuklab bo'lmadi: %s", e)
        raise AttachmentError(f"{label}ni Telegram'dan yuklab bo'lmadi — qayta yuborib ko'ring.") from e
    return buf.getvalue()


def _image_block(data: bytes, media_type: str) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                        "data": base64.standard_b64encode(data).decode("ascii")}}


def _text_block(name: str, text: str) -> dict:
    cut = len(text) > MAX_TEXT_CHARS
    body = text[:MAX_TEXT_CHARS] + ("\n... [fayl uzun — davomi ko'rsatilmadi]" if cut else "")
    safe_name = name.replace('"', "'")[:120]
    return {"type": "text", "text": f'<attached_file name="{safe_name}">\n{body}\n</attached_file>'}


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _xlsx_text(data: bytes) -> str:
    import openpyxl

    book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts = []
    for sheet in book.worksheets[:8]:
        out = io.StringIO()
        writer = csv.writer(out, delimiter="\t", lineterminator="\n")
        count = 0
        for row in sheet.iter_rows(values_only=True, max_row=_SHEET_ROWS, max_col=_SHEET_COLS):
            if any(cell not in (None, "") for cell in row):
                writer.writerow(["" if cell is None else cell for cell in row])
                count += 1
        note = f" (dastlabki {_SHEET_ROWS} qator)" if (sheet.max_row or 0) > _SHEET_ROWS else ""
        parts.append(f"## Varaq: {sheet.title}{note} — {count} qator\n{out.getvalue()}")
    return "\n".join(parts)


def _docx_text(data: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    lines = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            lines.append("\t".join(cell.text.strip() for cell in row.cells))
    return "\n".join(lines)


async def _from_document(message: Message, bot: Bot) -> dict:
    doc = message.document
    name = doc.file_name or "fayl"
    lower, mime = name.lower(), (doc.mime_type or "").lower()
    if mime in _IMAGE_TYPES:
        return _image_block(await _download(bot, doc, doc.file_size, MAX_IMAGE_BYTES, "Rasm"), mime)
    if mime == "application/pdf" or lower.endswith(".pdf"):
        data = await _download(bot, doc, doc.file_size, MAX_FILE_BYTES, "PDF fayl")
        return {"type": "document", "title": name[:120],
                "source": {"type": "base64", "media_type": "application/pdf",
                           "data": base64.standard_b64encode(data).decode("ascii")}}
    if lower.endswith((".xlsx", ".xlsm")):
        data = await _download(bot, doc, doc.file_size, MAX_FILE_BYTES, "Excel fayl")
        try:
            return _text_block(name, _xlsx_text(data))
        except Exception as e:  # noqa: BLE001
            raise AttachmentError(f"Excel faylni o'qib bo'lmadi ({type(e).__name__}) — faylni .xlsx "
                                  "ko'rinishida qayta saqlab yuboring.") from e
    if lower.endswith(".docx"):
        data = await _download(bot, doc, doc.file_size, MAX_FILE_BYTES, "Word fayl")
        try:
            return _text_block(name, _docx_text(data))
        except Exception as e:  # noqa: BLE001
            raise AttachmentError(f"Word faylni o'qib bo'lmadi ({type(e).__name__}).") from e
    if lower.endswith(_TEXT_SUFFIXES) or mime.startswith("text/"):
        return _text_block(name, _decode(await _download(bot, doc, doc.file_size, MAX_FILE_BYTES, "Fayl")))
    raise AttachmentError(UNSUPPORTED_TEXT)


async def extract(messages: list[Message], bot: Bot) -> tuple[list[dict], list[str]]:
    """Xabarlardagi (albom bo'lsa bir nechta) ilovalarni model uchun kontent bloklariga aylantiradi.
    Qaytadi: (bloklar, o'qib bo'lmagan ilovalar haqidagi xabarlar)."""
    blocks: list[dict] = []
    problems: list[str] = []
    for message in messages:
        if len(blocks) >= MAX_ATTACHMENTS:
            problems.append(f"Bir savolga eng ko'pi {MAX_ATTACHMENTS} ta ilova qabul qilinadi — qolganlari o'qilmadi.")
            break
        try:
            if message.photo:
                photo = message.photo[-1]  # eng katta o'lcham
                data = await _download(bot, photo, photo.file_size, MAX_IMAGE_BYTES, "Rasm")
                blocks.append(_image_block(data, "image/jpeg"))
            elif message.document:
                blocks.append(await _from_document(message, bot))
        except AttachmentError as e:
            problems.append(str(e))
    return blocks, problems
