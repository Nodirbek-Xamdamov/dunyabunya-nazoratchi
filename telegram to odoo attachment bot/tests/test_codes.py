import io

import pytest
import zxingcpp
from images import image_pdf, jpeg, page_with_code, text_pdf
from PIL import Image, ImageFilter

from docbot import codes
from docbot.config import CodesSettings

SETTINGS = CodesSettings()
PORTAL = "https://db.example.com/my/orders/89452?access_token=9d0ac27f-486e-46a6-a391-36fec7eeb6a0"


def test_a_qr_code_on_a_photo_is_read():
    assert codes.scan(jpeg(page_with_code(PORTAL)), SETTINGS).codes == [PORTAL]


@pytest.mark.parametrize(("turned_ccw", "rotate_cw"), [(0, 0), (90, 90), (180, 180), (270, 270)])
def test_the_scan_says_how_to_turn_a_sideways_photo_upright(turned_ccw, rotate_cw):
    page = page_with_code(PORTAL)
    result = codes.scan(jpeg(page.rotate(turned_ccw, expand=True)), SETTINGS)
    assert (result.codes, result.rotate_cw) == ([PORTAL], rotate_cw)
    upright = Image.open(io.BytesIO(codes.upright(jpeg(page.rotate(turned_ccw, expand=True)), rotate_cw)))
    assert upright.size == page.size


def test_an_upright_photo_is_attached_unchanged_but_a_pdf_never_turned():
    photo = jpeg(page_with_code(PORTAL))
    assert not codes.needs_upright(photo, 0)
    assert codes.needs_upright(photo, 90)
    assert not codes.needs_upright(text_pdf("S82411"), 90)


def test_a_code128_waybill_barcode_is_read():
    page = page_with_code("OD/INT/01514", zxingcpp.BarcodeFormat.Code128, code_px=420)
    assert codes.scan(jpeg(page), SETTINGS).codes == ["OD/INT/01514"]


def test_a_small_blurred_qr_is_still_read():
    # 70 px for a 41x41 code: under 2 px per module, like a QR on a photo of a whole A4 page.
    page = page_with_code(PORTAL, code_px=70).filter(ImageFilter.GaussianBlur(0.6))
    assert codes.scan(jpeg(page, quality=80), SETTINGS).codes == [PORTAL]


def test_decoding_falls_back_to_contrast_then_to_a_2x_upscale(monkeypatch):
    tried = []

    def fake_read(image, **kwargs):
        tried.append((image.mode, image.size))
        return []

    monkeypatch.setattr(codes.zxingcpp, "read_barcodes", fake_read)
    assert codes.scan(jpeg(page_with_code(None, size=(100, 200))), SETTINGS).codes == []
    assert tried == [("RGB", (100, 200)), ("L", (100, 200)), ("RGB", (200, 400))]


def test_a_page_without_codes_gives_nothing():
    result = codes.scan(jpeg(page_with_code(None)), SETTINGS)
    assert result.codes == [] and result.text == ""


def test_the_qr_inside_a_scanned_pdf_is_read():
    result = codes.scan(image_pdf(page_with_code(PORTAL, code_px=200)), SETTINGS)
    assert result.codes == [PORTAL]


def test_the_text_layer_of_a_pdf_is_returned():
    result = codes.scan(text_pdf("XARIDORDAN BUYURTMA No S82411", "Xaridor: TEST"), SETTINGS)
    assert result.codes == []
    assert "S82411" in result.text


def test_a_broken_file_is_reported_as_unreadable():
    with pytest.raises(codes.UnreadableFile):
        codes.scan(b"this is not a picture", SETTINGS)
    with pytest.raises(codes.UnreadableFile):
        codes.scan(b"%PDF-1.4 broken", SETTINGS)


@pytest.mark.parametrize(
    ("head", "mime"),
    [
        (b"%PDF-1.7\n", "application/pdf"),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n....", "image/png"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"\x00\x00\x00\x18ftypheic....", "image/heic"),
    ],
)
def test_the_real_type_is_sniffed_from_the_first_bytes(head, mime):
    assert codes.sniff_mime(head, "application/octet-stream") == mime


def test_unknown_bytes_keep_the_declared_type():
    assert codes.sniff_mime(b"????", "image/tiff") == "image/tiff"


def test_the_picture_for_the_ai_is_a_bounded_jpeg():
    big = page_with_code(PORTAL, size=(3000, 4000))
    out = codes.image_for_ai(jpeg(big), max_px=2048, pdf_scale=2.0)
    image = Image.open(io.BytesIO(out))
    assert image.format == "JPEG"
    assert max(image.size) == 2048


def test_the_picture_for_the_ai_of_a_pdf_is_its_first_page():
    out = codes.image_for_ai(text_pdf("S82411"), max_px=2048, pdf_scale=2.0)
    assert Image.open(io.BytesIO(out)).size == (1190, 1684)
