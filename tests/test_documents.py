from pathlib import Path

import pytest
import yaml

from docbot.config import DocumentsConfig
from docbot.documents import Documents, RecordRef, normalize_number

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def docs() -> Documents:
    """The shipped config/documents.yaml: these tests also guard the real patterns."""
    raw = yaml.safe_load((PROJECT_ROOT / "config" / "documents.yaml").read_text(encoding="utf-8"))
    return Documents(DocumentsConfig.model_validate(raw))


# ----------------------------------------------------------------------------- codes


def test_sales_order_qr_gives_the_record_id_and_its_token(docs):
    ref = docs.from_code("https://db.example.com/my/orders/89452?access_token=9d0ac27f-486e-46a6-a391-36fec7eeb6a0")
    assert ref == RecordRef(
        "sale", "sale.order", record_id=89452, token="9d0ac27f-486e-46a6-a391-36fec7eeb6a0", source=ref.source
    )


def test_purchase_qr_is_told_apart_from_a_sales_qr(docs):
    ref = docs.from_code("https://db.example.com/my/purchase/36269?access_token=17fb8b39-b41e-49ee-98d9-64a2c7775031")
    assert (ref.doc, ref.model, ref.record_id) == ("purchase", "purchase.order", 36269)


@pytest.mark.parametrize(
    ("text", "kind", "name"),
    [
        ("S82419", "sale", "S82419"),  # Yo'llanma barcode
        ("P32200", "purchase", "P32200"),
        ("OD/INT/01514", "picking", "OD/INT/01514"),  # Ombor cheki QR
        ("SHF/POS/00456", "picking", "SHF/POS/00456"),
        (" VWH/OUT/12345 \n", "picking", "VWH/OUT/12345"),
    ],
)
def test_name_codes_find_the_record_by_name(docs, text, kind, name):
    ref = docs.from_code(text)
    assert (ref.doc, ref.name, ref.record_id) == (kind, name, None)


@pytest.mark.parametrize(
    "text",
    [
        "https://taplink.cc/dunyabunya",  # the old company QR on some forms
        "https://www.dunyabunya.uz/terms-conditions",
        "4780000123456",
        "S123",  # too short to be an order
        "xS82419",
    ],
)
def test_foreign_codes_are_ignored(docs, text):
    assert docs.from_code(text) is None


# ----------------------------------------------------------------------------- numbers


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("s82409", "S82409"),
        ("№ S82409", "S82409"),
        ("S 82409", "S82409"),
        ("Р32200", "P32200"),  # Cyrillic Р typed on a Russian layout
        ("ОС/in/00060", "OC/IN/00060"),  # Cyrillic О and С
    ],
)
def test_typed_numbers_are_normalised(typed, expected):
    assert normalize_number(typed) == expected


def test_a_number_is_routed_by_its_shape(docs):
    assert [(r.doc, r.name) for r in docs.from_number("s82409")] == [("sale", "S82409")]
    assert [(r.doc, r.name) for r in docs.from_number("p32200")] == [("purchase", "P32200")]
    assert [(r.doc, r.name) for r in docs.from_number("OD/OUT/01234")] == [("picking", "OD/OUT/01234")]
    assert docs.from_number("INV/2026/78243") == []
    assert docs.from_number("") == []


def test_numbers_are_found_in_a_pdf_text_layer_but_not_inside_other_codes(docs):
    text = (
        "XARIDORDAN BUYURTMA № S82411\r\n17.09.2026 yil\r\nXaridor: SHIRIBOM\r\n"
        "1 [0925-28908] Shpaklevka 20kg dona 3 44 000\r\nS82411\r\nINV/2026/78243 XS99999"
    )
    assert [(r.doc, r.name) for r in docs.find_numbers(text)] == [("sale", "S82411")]


def test_refs_survive_a_json_round_trip():
    ref = RecordRef("sale", "sale.order", record_id=5, token="t", source="qr")
    assert RecordRef.from_dict(ref.as_dict()) == ref


def test_titles_and_examples_for_messages(docs):
    assert docs.title("stock.picking") == "Ombor hujjati"
    assert docs.title("unknown.model") == "unknown.model"
    assert docs.examples() == "S82409, P32200 yoki OD/OUT/01234"
