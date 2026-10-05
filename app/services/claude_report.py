"""Umumiy jadval-hisobot (HTML) — Python bilan generatsiya qilinadi, tashqi API kerak emas."""

from __future__ import annotations

import uuid
from pathlib import Path

from app.config import settings
from app.services import report_html as rh

REPORTS_DIR = Path(settings.STORAGE_DIR) / "reports"

# Shundan ko'p qatorli ro'yxatda har qatorga chiziq chizish ma'nosiz — faqat jadval qoladi
_CHART_MAX_ROWS = 40
# Shundan uzun jadvalda qidiruv va saralash foydali
_INTERACTIVE_MIN_ROWS = 8


def _numeric_columns(rows: list[dict], keys: list[str]) -> list[str]:
    """Ustun son hisoblanadi, agar undagi BARCHA to'ldirilgan qiymatlar son bo'lsa.

    Ilgari faqat birinchi qatorga qaralardi va bool ham son deb olinardi — birinchi
    xodimning telefoni bo'sh (False) bo'lsa, "telefon" ustuni son deb topilib, hisobot
    tepasida "mobile_phone: 0" kartochkasi va telefon raqamli grafiklar chiqardi."""
    numeric = []
    for key in keys:
        values = [r.get(key) for r in rows if r.get(key) not in (None, "", False)]
        if values and all(rh.is_number(v) for v in values):
            numeric.append(key)
    return numeric


def generate_report_link(*, title: str, description: str, rows: list[dict],
                         columns: list[tuple[str, str]] | None = None,
                         numeric: list[str] | None = None,
                         totals: dict | None = None, total_rows: int | None = None,
                         count_rows: bool = True) -> str | None:
    """Berilgan qatorlardan mustaqil HTML sahifa yasaydi va havolasini qaytaradi.

    `columns`: [(kalit, sarlavha)] — berilmasa qatorlardagi kalitlar o'z nomi bilan.
    `numeric`: jamlanadigan (son) ustunlar kalitlari — berilmasa qiymatlardan aniqlanadi.
    `totals`/`total_rows`: jadvalga bazadagi yozuvlarning faqat bir qismi sig'gan bo'lsa —
    BUTUN tanlov bo'yicha yig'indilar va yozuvlar soni (kartochkalar shulardan olinadi;
    aks holda "Jami" kartochkasi faqat ko'ringan qatorlar yig'indisini ko'rsatib chalg'itardi).
    `count_rows=False` — qatorlar yozuv emas, guruh (masalan holatlar) bo'lsa: "Jami yozuvlar"
    kartochkasi chiqarilmaydi.
    """
    if not rows:
        return None
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if columns is None:
        keys: list[str] = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        columns = [(k, k) for k in keys]
    keys = [k for k, _ in columns]
    labels = dict(columns)
    numeric = [k for k in (numeric if numeric is not None else _numeric_columns(rows, keys)) if k in labels]
    label_key = next((k for k in keys if k not in numeric), None)

    partial = total_rows is not None and total_rows > len(rows)
    sums = []
    for k in numeric[:3]:
        if partial:
            if not rh.is_number((totals or {}).get(k)):
                continue  # butun tanlov bo'yicha yig'indi noma'lum — qisman yig'indi ko'rsatilmaydi
            value = totals[k]
        else:
            value = sum(r.get(k) for r in rows if rh.is_number(r.get(k)))
        sums.append(rh.tile(labels[k], rh.fmt_num(value), "barcha yozuvlar yig'indisi" if count_rows else "jami"))
    tiles = []
    # Yozuvlar soni sarlavha ostida ham yozilgan — yolg'iz o'zi uchun kartochka chiqarilmaydi
    if count_rows and sums:
        tiles.append(rh.tile("Jami yozuvlar", rh.fmt_int(total_rows if partial else len(rows)),
                             f"jadvalda dastlabki {rh.fmt_int(len(rows))} tasi" if partial else ""))
    tiles += sums

    cards = []
    if label_key and numeric and 2 <= len(rows) <= _CHART_MAX_ROWS:
        measure = numeric[0]
        cards.append(rh.card(
            labels[measure],
            rh.hbars([str(r.get(label_key) or "—") for r in rows], [r.get(measure) for r in rows]),
            sub=f"{labels[label_key]} bo'yicha",
        ))
    cards.append(rh.card(
        "Ro'yxat" if not numeric else "Jadval",
        rh.table([labels[k] for k in keys], [[r.get(k, "") for k in keys] for r in rows],
                 numbered=True, interactive=len(rows) >= _INTERACTIVE_MIN_ROWS),
    ))

    page = rh.page(title=title, subtitle=description, tiles_html=rh.tiles(*tiles), body="\n".join(cards),
                   wide=len(keys) >= 6)
    # O'qiladigan nom + taxmin qilib bo'lmaydigan qo'shimcha (ilgari fayl Telegram'da
    # "98b45bd5f7714bc9….html" kabi ma'nosiz nom bilan ko'rinardi)
    filename = f"{rh.slug(title)}_{uuid.uuid4().hex[:12]}.html"
    (REPORTS_DIR / filename).write_text(page, encoding="utf-8")
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/reports/{filename}"
