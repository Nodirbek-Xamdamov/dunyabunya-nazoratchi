"""Text helpers: HTML escaping, safe LIKE patterns, simple word frequency for recurring topics."""

from __future__ import annotations

import html
import re
from collections import Counter

LIKE_ESCAPE = "\\"

_WORD_RE = re.compile(r"[0-9a-zA-Zа-яА-ЯёЁўЎқҚғҒҳҲʻʼ'’`]+")
_APOSTROPHES = str.maketrans({"ʻ": "'", "ʼ": "'", "’": "'", "`": "'"})

STOPWORDS = frozenset(
    """
    mijoz mijozlar mijozning mijozga va bilan uchun haqida bo'yicha yoki ham bu shu u ular edi emas bor yo'q
    so'radi so'rayapti so'ragan qildi qilyapti qilgan berdi bermoqchi bildirdi yozdi aytdi xabar xabari
    kerak kerakligini qanday qachon nima nimaga necha qancha qaysi kim kimga hali endi yana juda eng
    ekanini ekan esa lekin agar chunki keyin oldin hozir bugun ertaga kecha haqidagi to'g'risida ma'lumot
    so'rov so'rovi iltimos rahmat salom assalomu alaykum ok xo'p ha tushunarli boshqa qilish berish
    shikoyat norozi noroziligini kelgani kelganidan qolgani qolganidan bildirib aytib
    и в во на не что с со по для это как а но я мы вы он она они есть будет когда где там тут уже еще ещё
    же ли бы от до из за у о об при к ко ну да нет так вот можно нужно надо спасибо здравствуйте
    """.split()
)
_SUFFIXES = ("larining", "larning", "larini", "laridan", "lardan", "larga", "larni", "lari", "ning", "dagi",
             "ini", "dan", "lar", "ni", "ga", "da")


def esc(value) -> str:
    """Escape any user-provided text before it is put into HTML."""
    return html.escape("" if value is None else str(value), quote=True)


def like_pattern(text: str) -> str:
    """``%text%`` with LIKE wildcards escaped; use with ``ESCAPE '\\'``."""
    escaped = text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2).replace("%", LIKE_ESCAPE + "%").replace("_", LIKE_ESCAPE + "_")
    return f"%{escaped}%"


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def top_words(texts, limit: int = 20) -> list[tuple[str, int]]:
    """Most frequent meaningful words (stop words, numbers and very short words removed)."""
    counter: Counter[str] = Counter()
    for text in texts:
        if not text:
            continue
        seen = set()
        for raw in _WORD_RE.findall(str(text).lower().translate(_APOSTROPHES)):
            word = raw.strip("'")
            if len(word) < 3 or word.isdigit() or word in STOPWORDS:
                continue
            stem = _stem(word)
            if stem in STOPWORDS or stem in seen:
                continue
            seen.add(stem)
            counter[stem] += 1
    return counter.most_common(limit)
