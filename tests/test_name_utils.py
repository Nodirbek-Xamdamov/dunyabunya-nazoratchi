"""O'zbek ism normallashtirish (qo'shimchalarni olib tashlash) testlari."""

import pytest

from app.name_utils import strip_uzbek_suffixes, normalize_name_for_search


@pytest.mark.parametrize(
    "source,expected",
    [
        # Kelishik/egalik qo'shimchalari
        ("Nodirga", "Nodir"),
        ("Muroddan", "Murod"),
        ("Azizning", "Aziz"),
        ("Dilnozalarni", "Dilnoza"),
        ("Faridaga", "Farida"),
        # Hurmat/erkalash qo'shimchalari (-bek, -jon, -xon)
        ("Nodirbekka", "Nodir"),
        ("Murodjon", "Murod"),
        ("Azizbekdan", "Aziz"),
        ("Boburjonni", "Bobur"),
        ("Alisherbek", "Alisher"),
        ("Rustamxon", "Rustam"),
        # Ism + familiya (har bo'lakdan qo'shimcha tushadi)
        ("Nodir Xamdamovga", "Nodir Xamdamov"),
        ("Nodirbek Xamdamovdan", "Nodir Xamdamov"),
        # Sof nominativ ismlar o'zgarmasligi kerak
        ("Farida", "Farida"),
        ("Aziza Karimova", "Aziza Karimova"),
        ("Ali", "Ali"),
        ("Gulnora", "Gulnora"),
        ("Bekzod", "Bekzod"),
        ("Doston", "Doston"),
        ("Otabek", "Ota"),
        ("Ravshan", "Ravshan"),
    ],
)
def test_normalize_name_for_search(source: str, expected: str) -> None:
    got = normalize_name_for_search(source)
    assert got.casefold() == expected.casefold()


def test_suffix_strip_leaves_bare_short_names_untouched() -> None:
    # Qisqa o'zakni buzib yubormaslik kerak
    assert strip_uzbek_suffixes("Bek") == "Bek"
    assert strip_uzbek_suffixes("Jon") == "Jon"
    assert strip_uzbek_suffixes("Ali") == "Ali"
