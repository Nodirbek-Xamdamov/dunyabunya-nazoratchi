"""Umumiy jadval-hisobot (HTML) — Python bilan generatsiya qilinadi, tashqi API kerak emas."""

from __future__ import annotations

import uuid
from pathlib import Path

from app.config import settings
from app.services import report_html as rh

REPORTS_DIR = Path(settings.STORAGE_DIR) / "reports"

# Shundan ko'p qatorli ro'yxatda har qatorga ustun chizish ma'nosiz — faqat jadval qoladi
_CHART_MAX_ROWS = 40


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
                         totals: dict | None = None, total_rows: int | None = None) -> str | None:
    """Berilgan qatorlardan mustaqil HTML sahifa yasaydi va havolasini qaytaradi.

    `columns`: [(kalit, sarlavha)] — berilmasa qatorlardagi kalitlar o'z nomi bilan.
    `numeric`: jamlanadigan (son) ustunlar kalitlari — berilmasa qiymatlardan aniqlanadi.
    `totals`/`total_rows`: jadvalga bazadagi yozuvlarning faqat bir qismi sig'gan bo'lsa —
    BUTUN tanlov bo'yicha yig'indilar va yozuvlar soni (kartochkalar shulardan olinadi;
    aks holda "Jami" kartochkasi faqat ko'ringan qatorlar yig'indisini ko'rsatib chalg'itardi).
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
    cards = []
    for k in numeric[:4]:
        if partial:
            if not rh.is_number((totals or {}).get(k)):
                continue  # butun tanlov bo'yicha yig'indi noma'lum — qisman yig'indi ko'rsatilmaydi
            value = totals[k]
        else:
            value = sum(r.get(k) for r in rows if rh.is_number(r.get(k)))
        cards.append(rh.card(labels[k], rh.fmt_num(value)))
    cards.append(rh.card("Jami yozuvlar", rh.fmt_int(total_rows if partial else len(rows))))

    charts = rh.Charts()
    sections = []
    if label_key and 2 <= len(rows) <= _CHART_MAX_ROWS:
        row_labels = [str(r.get(label_key) or "—")[:40] for r in rows]
        for k in numeric[:2]:
            sections.append(rh.section(labels[k], charts.add(row_labels, [r.get(k) for r in rows],
                                                             horizontal=True, label=labels[k])))

    sections.append(rh.section(
        "Jadval",
        rh.table([labels[k] for k in keys], [[r.get(k, "") for k in keys] for r in rows], numbered=True),
    ))

    page = rh.page(title=title, subtitle=description, cards=cards, body="\n".join(sections), charts=charts)
    filename = f"{uuid.uuid4().hex}.html"
    (REPORTS_DIR / filename).write_text(page, encoding="utf-8")
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/reports/{filename}"
