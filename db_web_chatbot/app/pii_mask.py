# app/pii_mask.py
#
# Mijoz xabarida uchrashi mumkin bo'lgan shaxsiy ma'lumotlarni OpenAI'ga
# yuborilishidan va logga yozilishidan oldin maskalash. Bu tekshiruv
# system promptdagi qoidadan (bot o'zi ma'lumot so'ramaydi/takrorlamaydi)
# mustaqil, kod darajasidagi qo'shimcha himoya qatlami.
import re

# Pasport: 2 lotin harf + 7 raqam (masalan AA1234567), orada bo'sh joy bilan ham.
_PASSPORT_RE = re.compile(r"\b[A-Za-z]{2}\s?\d{7}\b")

# JSHSHIR: 14 ta ketma-ket raqam.
_JSHSHIR_RE = re.compile(r"\b\d{14}\b")

# Bank karta raqami: 16 ta raqam, bo'sh joy yoki chiziqcha bilan 4talik guruhlangan
# yoki guruhlanmagan holda.
_CARD_RE = re.compile(r"\b(?:\d[ -]?){15}\d\b")

_MASKS = (
    (_CARD_RE, "[KARTA RAQAMI OLIB TASHLANDI]"),
    (_JSHSHIR_RE, "[JSHSHIR OLIB TASHLANDI]"),
    (_PASSPORT_RE, "[PASPORT OLIB TASHLANDI]"),
)


def mask_pii(text: str) -> str:
    """Matndagi pasport, JSHSHIR va karta raqamlarini placeholder bilan almashtiradi."""
    if not text:
        return text
    for pattern, placeholder in _MASKS:
        text = pattern.sub(placeholder, text)
    return text
