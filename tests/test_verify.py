import pytest

from docbot.ai import Extraction
from docbot.config import DocumentType
from docbot.odoo import OdooRecord
from docbot.verify import normalize_name, phone_tail, similarity, verify

SALE = DocumentType(
    key="sale",
    title="Sotuv buyurtmasi",
    model="sale.order",
    number=r"S\d{5,}",
    partner_field="partner_id",
    total_field="amount_total",
    person_field="user_id",
)
RECORD = OdooRecord(
    "sale.order",
    88921,
    "S81878",
    {"partner_id": [83123, "Dilshod 6161"], "amount_total": 1780000.0, "user_id": [191, "MIRKAMILOV BOTIR BAXODIRXON O‘G‘LI"]},
)
PARTNER = {"name": "Dilshod 6161", "phone": "+998 88 150 61 61", "mobile": False}


def read(**fields) -> Extraction:
    return Extraction(similarity=90, doc_number="S81878", **fields)


def test_names_are_compared_without_case_and_apostrophe_variants():
    assert normalize_name("Mirkamilov Botir O‘G‘LI") == normalize_name("MIRKAMILOV BOTIR O'G'LI") == "MIRKAMILOV BOTIR OGLI"


def test_phone_numbers_are_compared_by_their_last_nine_digits():
    assert phone_tail("+998 88 150 61 61") == phone_tail("881506161") == "881506161"
    assert phone_tail("12-34") == ""  # too short to mean anything


def test_everything_matching_is_a_clear_yes():
    verdict = verify(
        read(partner="Dilshod 6161", phone="+998881506161", person="MIRKAMILOV BOTIR BAXODIRXON O'G'LI", total=1780000),
        RECORD, SALE, PARTNER,
    )
    assert verdict.ok
    assert sorted(verdict.matched) == ["partner", "person", "phone", "total"]


def test_misspelled_names_still_match(capsys):
    # What the AI really read from a small screenshot of S81878
    verdict = verify(read(partner="Dilorhod 6161", person="MIRKAMOLYOV BOTIR BAXODIROVICH O'G'LI", total=1780000), RECORD, SALE, PARTNER)
    assert verdict.ok
    assert verdict.details["partner"] >= 0.8 and verdict.details["person"] >= 0.8


def test_only_the_total_read_is_enough_when_nothing_contradicts():
    assert verify(read(total=1780000.0), RECORD, SALE, PARTNER).ok


def test_a_wrong_total_alone_blocks_automatic_attaching():
    verdict = verify(read(total=1790000), RECORD, SALE, PARTNER)
    assert not verdict.ok
    assert verdict.conflicts == ["total"]
    assert verdict.reason() == "mos kelmadi: summa"


def test_a_different_printed_phone_is_no_contradiction():
    # The paper often shows the delivery contact's number.
    verdict = verify(read(phone="+998991112233", total=1780000), RECORD, SALE, PARTNER)
    assert verdict.ok and verdict.conflicts == []


def test_the_right_customer_wins_over_a_changed_total_or_another_printed_phone():
    # Real production cases: the order was edited after printing; the paper shows a delivery phone.
    assert verify(read(partner="Dilshod 6161", phone="+998881506161", total=1790000), RECORD, SALE, PARTNER).ok
    assert verify(read(partner="Dilshod 6161", phone="+998991112233"), RECORD, SALE, PARTNER).ok


def test_the_manager_alone_does_not_prove_the_record():
    # One manager has hundreds of orders: a misread number may well land on another one of them.
    verdict = verify(read(person="MIRKAMILOV BOTIR BAXODIRXON O'G'LI", total=1790000), RECORD, SALE, PARTNER)
    assert not verdict.ok


def test_a_different_customer_blocks_automatic_attaching():
    verdict = verify(read(partner="Shiribom chakana", total=1780000), RECORD, SALE, PARTNER)
    assert verdict.conflicts == ["partner"]


def test_nothing_comparable_is_not_enough():
    verdict = verify(read(), RECORD, SALE, PARTNER)
    assert not verdict.ok
    assert verdict.reason() == "tekshirish uchun ma'lumot o'qilmadi"


@pytest.mark.parametrize(("a", "b"), [("MUSLIMBEK 0774", "MUSLIMBEK 0774"), ("abc", "ABC")])
def test_similarity_of_equal_names_is_one(a, b):
    assert similarity(a, b) == 1.0
