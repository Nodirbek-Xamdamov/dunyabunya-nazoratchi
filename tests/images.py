"""Synthetic test files: pages with a QR code / barcode, and PDFs (no real customer data in tests)."""

import io

import zxingcpp
from PIL import Image, ImageDraw


def barcode_image(text: str, fmt=zxingcpp.BarcodeFormat.QRCode, *, scale: int = 4) -> Image.Image:
    view = memoryview(zxingcpp.create_barcode(text, fmt).to_image(scale=scale))
    height, width = view.shape
    return Image.frombuffer("L", (width, height), bytes(view), "raw", "L", 0, 1).convert("RGB")


def page_with_code(
    text: str | None,
    fmt=zxingcpp.BarcodeFormat.QRCode,
    *,
    code_px: int = 160,
    size: tuple[int, int] = (1080, 1280),
    title: str = "XARIDORDAN BUYURTMA",
) -> Image.Image:
    """A white "document" with a title line and (optionally) a code of ``code_px`` width near the bottom left."""
    page = Image.new("RGB", size, "white")
    ImageDraw.Draw(page).text((60, 60), title, fill="black")
    if text is not None:
        code = barcode_image(text, fmt)
        code = code.resize((code_px, max(1, round(code.height * code_px / code.width))), Image.NEAREST)
        page.paste(code, (60, size[1] - code.height - 120))
    return page


def jpeg(image: Image.Image, quality: int = 90) -> bytes:
    out = io.BytesIO()
    image.save(out, "JPEG", quality=quality)
    return out.getvalue()


def image_pdf(image: Image.Image) -> bytes:
    """A PDF that only holds a picture (no text layer), like a scanned page."""
    out = io.BytesIO()
    image.save(out, "PDF", resolution=100)
    return out.getvalue()


def text_pdf(*lines: str) -> bytes:
    """A minimal one-page PDF with a real text layer (like the ones Odoo prints)."""
    content = "BT /F1 14 Tf 50 780 Td 18 TL " + " ".join(f"({line}) Tj T*" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
