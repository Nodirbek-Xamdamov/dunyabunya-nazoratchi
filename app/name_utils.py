"""O'zbek ism-normallashtirish yordamchisi.

Ovozli/matn xabarlarda ismlar ko'pincha qo'shimchalar bilan keladi
("Nodirga", "Nodirbekka", "Muroddan"). Qidiruvdan oldin shu qo'shimchalarni
olib tashlab, ismning SOF O'ZAGI (nominativ shakli) olinadi — shunda Odoo/DB
qidiruvi aniq ishlaydi.
"""

from __future__ import annotations

# Kelishik / egalik / ko'plik qo'shimchalari (uzunidan qisqasiga)
_INFLECTION_SUFFIXES = (
    "laridagi", "larining", "laridan",
    "lardagi", "larning", "lariga", "larida", "larini",
    "larga", "lardan", "lari", "lar",
    "ning", "dan", "ni", "ga", "ka", "qa", "da",
)

# Ismga yopishgan hurmat/erkalash qo'shimchalari ("Nodirbek", "Murodjon", "Rustamxon")
_ATTACHED_NAME_SUFFIXES = ("bek", "jon", "xon")

# -da/-dan faqat UNDOSHdan keyin kelganda qo'shimcha hisoblanadi ("Nodirdan").
# Vowel bilan tugagan haqiqiy ismlar buzilmasligi uchun: "Farida" -> o'zgarishsiz qoladi.
_VOWELS = set("aeiou" + "o'" + "ʻo" + "’ʻ‘`" + "'")


def strip_uzbek_suffixes(name: str) -> str:
    """Ismdan barcha o'zbekcha qo'shimchalarni olib tashlab, sof o'zakni qaytaradi.

    Misollar: "Nodirga"->"Nodir", "Nodirbekka"->"Nodir", "Muroddan"->"Murod",
    "Azizning"->"Aziz", "Dilnozalarni"->"Dilnoza", "Alisherbek"->"Alisher",
    "Rustamxon"->"Rustam".
    O'zak 3 ta harfdan kichik bo'lib qolishiga yo'l qo'yilmaydi ("Bek" o'zgarishsiz qoladi).
    Vowel bilan tugaydigan ayol ismlariga xos "-da" olib tashlanmaydi ("Farida").
    """
    if not name:
        return name
    n = name.strip()

    # 1-qatlam: kelishik/egalik/ko'plik qo'shimchalari
    changed = True
    while changed and len(n) >= 3:
        changed = False
        for suffix in _INFLECTION_SUFFIXES:
            if len(suffix) >= len(n):
                continue
            if not n.lower().endswith(suffix):
                continue
            if len(n) - len(suffix) < 3:
                continue
            # -da/-dan: faqat oldingi harf undosh bo'lsa qo'shimcha deb hisoblanadi
            if suffix in ("da", "dan"):
                prev_idx = len(n) - len(suffix) - 1
                prev = n.lower()[prev_idx] if prev_idx >= 0 else ""
                if prev in _VOWELS:
                    continue
            n = n[: -len(suffix)]
            changed = True
            break

    # 2-qatlam: hurmat qo'shimchalari (-bek, -jon, -xon) — o'zak kamida 3 harf bo'lishi shart
    changed = True
    while changed and len(n) >= 3:
        changed = False
        for suffix in _ATTACHED_NAME_SUFFIXES:
            if len(suffix) >= len(n):
                continue
            if n.lower().endswith(suffix) and len(n) - len(suffix) >= 3:
                n = n[: -len(suffix)]
                changed = True
                break

    return n


def normalize_name_for_search(name: str) -> str:
    """Qidiruv uchun to'liq normallashtirish: HAR BIR so'zdan qo'shimchalarni olib
    tashlaydi ("Nodirbek Xamdamovdan"->"Nodir Xamdamov"), patronimik qismlarni
    ("o'g'li"/"ogli") va tinish belgilarini tozalaydi."""
    if not name:
        return ""
    words = []
    for w in name.split():
        n = strip_uzbek_suffixes(w)
        for ch in ["\u2018", "\u2019", "'", "`", '"']:
            n = n.replace(ch, "")
        n = n.replace("o g li", "").replace("ogli", "").replace("o'g'li", "")
        if n.strip():
            words.append(n.strip())
    return " ".join(words)
