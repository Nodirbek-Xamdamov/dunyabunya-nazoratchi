# app/aliases.py
# Mijoz so'zi (imlo xato, boshqa til/alifbo) -> katalogdagi haqiqiy so'z
# ko'prigi. Manba: app/data/product_aliases.xml. Bir marta import vaqtida
# o'qiladi (fayl kichik, ishga tushishda parslash arzon).
from pathlib import Path
from xml.etree import ElementTree

_ALIASES_PATH = Path(__file__).parent / "data" / "product_aliases.xml"


def _load_aliases() -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not _ALIASES_PATH.exists():
        return mapping

    root = ElementTree.parse(_ALIASES_PATH).getroot()
    for alias_el in root.findall("alias"):
        terms_el = alias_el.find("terms")
        canonical_el = alias_el.find("canonical")
        if terms_el is None or canonical_el is None:
            continue
        canonical = (canonical_el.text or "").strip()
        if not canonical:
            continue
        for term in (terms_el.text or "").split(","):
            term = term.strip().lower()
            if term:
                mapping[term] = canonical
    return mapping


# {"siment": "цемент", "sement": "цемент", ...}
ALIASES: dict[str, str] = _load_aliases()


def expand_word(word: str) -> list[str]:
    """Berilgan so'z uchun qidiruvda ishlatiladigan variantlar: o'zi + (bor bo'lsa) canonical alias."""
    variants = [word]
    canonical = ALIASES.get(word.lower())
    if canonical and canonical.lower() != word.lower():
        variants.append(canonical)
    return variants
