"""Reading QR codes and barcodes (and the text layer of PDFs) from an uploaded file. CPU bound: run in a thread.

Phone photos arrive compressed to 1280 px, so the QR on a printed page is small. Each image is tried
as it is, then with stretched contrast, then upscaled 2x and sharpened; the last step alone turned
most "almost readable" simulated photos into successes. Images the decoder cannot use go to the AI.

Photos are often sideways or upside down. The QR code tells how it is turned (``Barcode.orientation``),
so the scan also reports ``rotate_cw``: the clockwise turn (0/90/180/270) that makes the page upright.
``upright`` produces that turned copy, which is what gets attached to Odoo.
"""

import io
from dataclasses import dataclass, field

import pypdfium2 as pdfium
import zxingcpp
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

from docbot.config import CodesSettings

# What our printed forms carry (QR on orders/receipts, Code 128 on waybills) plus close relatives.
FORMATS = (
    zxingcpp.BarcodeFormat.QRCode,
    zxingcpp.BarcodeFormat.MicroQRCode,
    zxingcpp.BarcodeFormat.DataMatrix,
    zxingcpp.BarcodeFormat.Code128,
    zxingcpp.BarcodeFormat.Code39,
)
_UPSCALE_MAX_SIDE = 2600  # larger images are detailed enough; upscaling them only costs time and memory


class UnreadableFile(Exception):
    """The file is neither an image Pillow can open nor a PDF."""


@dataclass(frozen=True)
class ScanResult:
    codes: list[str] = field(default_factory=list)  # decoded texts, unique, in reading order
    text: str = ""  # PDF text layer ("" for pictures)
    rotate_cw: int = 0  # clockwise turn that makes the page upright, from the first code found


def is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def sniff_mime(data: bytes, declared: str | None = None) -> str:
    """The real type from the first bytes (Telegram's mime_type is the sender's claim)."""
    if is_pdf(data):
        return "application/pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypmsf1"):
        return "image/heic"
    return declared or "application/octet-stream"


def _open_image(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)  # phone photos sent as files carry their rotation in EXIF
        return image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UnreadableFile(f"rasm ochilmadi: {exc}") from None


def _pdf_pages(data: bytes, *, pages: int, scale: float) -> tuple[list[Image.Image], str]:
    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise UnreadableFile(f"PDF ochilmadi: {exc}") from None
    try:
        images, texts = [], []
        for index in range(min(pages, len(pdf))):
            page = pdf[index]
            images.append(page.render(scale=scale).to_pil().convert("RGB"))
            texts.append(page.get_textpage().get_text_range())
        return images, "\n".join(texts)
    finally:
        pdf.close()


def turn_to_upright(orientation: int) -> int:
    """zxing's orientation of a code (counter-clockwise turns are negative) -> clockwise turn that undoes it."""
    return (-round(orientation / 90) * 90) % 360


def _decode(image: Image.Image) -> tuple[list[str], int]:
    attempts = [lambda: image, lambda: ImageOps.autocontrast(image.convert("L"))]
    if max(image.size) <= _UPSCALE_MAX_SIDE:
        attempts.append(
            lambda: image.resize((image.width * 2, image.height * 2), Image.LANCZOS).filter(ImageFilter.UnsharpMask(2, 80, 2))
        )
    for attempt in attempts:
        found = [r for r in zxingcpp.read_barcodes(attempt(), formats=FORMATS) if r.valid and r.text]
        if found:
            return [r.text for r in found], turn_to_upright(found[0].orientation)
    return [], 0


def scan(data: bytes, settings: CodesSettings) -> ScanResult:
    """All code texts in the file (every PDF page up to ``pdf_pages``) and, for a PDF, its text."""
    if is_pdf(data):
        images, text = _pdf_pages(data, pages=settings.pdf_pages, scale=settings.pdf_scale)
    else:
        images, text = [_open_image(data)], ""
    codes: list[str] = []
    rotate_cw: int | None = None
    for image in images:
        found, turn = _decode(image)
        if found and rotate_cw is None:
            rotate_cw = turn
        for value in found:
            if value not in codes:
                codes.append(value)
    return ScanResult(codes=codes, text=text, rotate_cw=rotate_cw or 0)


def _turned(image: Image.Image, rotate_cw: int) -> Image.Image:
    return image.rotate(-rotate_cw, expand=True) if rotate_cw % 360 else image  # PIL turns counter-clockwise


def needs_upright(data: bytes, rotate_cw: int) -> bool:
    """A picture that must be turned (by the scan/AI, or by its own EXIF tag) before people look at it."""
    if is_pdf(data):
        return False
    if rotate_cw % 360:
        return True
    try:
        return Image.open(io.BytesIO(data)).getexif().get(0x0112, 1) not in (0, 1)  # EXIF Orientation
    except (UnidentifiedImageError, OSError, ValueError):
        return False


def upright(data: bytes, rotate_cw: int) -> bytes:
    """The picture turned so the document reads normally, as JPEG (what is attached to Odoo)."""
    out = io.BytesIO()
    _turned(_open_image(data), rotate_cw).save(out, "JPEG", quality=92)
    return out.getvalue()


def image_for_ai(data: bytes, *, max_px: int, pdf_scale: float, rotate_cw: int = 0) -> bytes:
    """The picture to send to the AI: first PDF page or the photo, upright, longer side <= max_px, as JPEG."""
    if is_pdf(data):
        images, _ = _pdf_pages(data, pages=1, scale=pdf_scale)
        image = images[0]
    else:
        image = _turned(_open_image(data), rotate_cw)
    if max(image.size) > max_px:
        image.thumbnail((max_px, max_px), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=88)
    return out.getvalue()
