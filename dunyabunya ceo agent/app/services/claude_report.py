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


def _save(title: str, page: str) -> str:
    # O'qiladigan nom + taxmin qilib bo'lmaydigan qo'shimcha (ilgari fayl Telegram'da
    # "98b45bd5f7714bc9….html" kabi ma'nosiz nom bilan ko'rinardi)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{rh.slug(title)}_{uuid.uuid4().hex[:16]}.html"
    (REPORTS_DIR / filename).write_text(page, encoding="utf-8")
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/reports/{filename}"


# Guruhlar soni shundan oshsa taqsimot grafigi o'qib bo'lmaydigan bo'ladi — faqat ro'yxat qoladi
_DISTRIBUTION_MAX_GROUPS = 30


def _column_widths(layout, keys: list[str], headers: list[str]) -> list[float]:
    """Ustunlarning nisbiy kengligi — mazmun uzunligiga qarab (barcha guruhlar bo'yicha bitta
    o'lchov: har guruh jadvali o'zicha kengaysa, ustunlar bir chiziqda turmaydi)."""
    from app.services import list_layout as ll

    widths = []
    for key, header in zip(keys, headers):
        kind = layout.kinds.get(key) or "text"
        # Bo'sh kataklar hisobga olinmaydi (kam to'ldirilgan ustun ham o'z qiymatlariga sig'ishi kerak);
        # 90-foizlik uzunlik olinadi — bitta juda uzun qiymat ustunni kengaytirib yubormasin
        lengths = sorted(n for n in (len(ll.display(r.get(key), kind)) for r in layout.rows[:500]) if n)
        typical = lengths[int((len(lengths) - 1) * 0.9)] if lengths else 0
        if lengths and lengths[-1] <= 22:
            typical = lengths[-1]   # qisqa qiymatli ustun (holat, raqam) to'liq sig'sin — ikki qatorga bo'linmasin
        if kind == "number":
            typical = 11
        elif kind in ("phone", "date", "datetime"):
            typical = min(typical, 17) + 3   # bu kataklar bo'linmaydi — to'liq sig'ishi shart
        elif kind == "path":
            typical = min(typical, 26)
        widths.append(float(max(len(str(header)) + 2, 9, min(typical, 38))))
    return widths


def generate_list_report(*, title: str, description: str, layout, numeric: list[str] | None = None,
                         totals: dict | None = None, total_rows: int | None = None) -> str | None:
    """Yozuvlar ro'yxati (xodimlar, mijozlar, buyurtmalar) sahifasi: `layout`
    (list_layout.ListLayout) guruhlangan bo'lsa — tepada guruhlar bo'yicha taqsimot, pastda har
    bir guruh o'z sarlavhasi va jadvali bilan; bitta qidiruv maydoni hammasini filtrlaydi.
    Telefon va email bosiladi, tor ekranda har yozuv kartochka bo'lib ko'rinadi.

    `totals` / `total_rows` — ro'yxatga bazadagi yozuvlarning bir qismi sig'gan bo'lsa, BUTUN
    tanlov bo'yicha yig'indilar va soni (kartochkalar shulardan olinadi)."""
    rows = layout.rows
    if not rows:
        return None
    keys = [k for k, _ in layout.columns]
    labels = dict(layout.columns)
    numeric = [k for k in (numeric or []) if k in labels]
    partial = total_rows is not None and total_rows > len(rows)

    sums = []
    for key in numeric[:2]:
        if partial:
            if not rh.is_number((totals or {}).get(key)):
                continue  # butun tanlov bo'yicha yig'indi noma'lum — qisman yig'indi ko'rsatilmaydi
            value = totals[key]
        else:
            value = sum(r.get(key) for r in rows if rh.is_number(r.get(key)))
        sums.append(rh.tile(labels[key], rh.fmt_num(value), "barcha yozuvlar yig'indisi"))
    tiles = []
    # Yozuvlar soni sarlavha ostida ham yozilgan — yolg'iz o'zi uchun kartochka chiqarilmaydi
    many_groups = layout.grouped and len(layout.groups) > 1
    if many_groups or sums:
        tiles.append(rh.tile("Jami yozuvlar", rh.fmt_int(total_rows if partial else len(rows)),
                             f"ro'yxatda dastlabki {rh.fmt_int(len(rows))} tasi" if partial else ""))
    if many_groups:
        tiles.append(rh.tile(f"{layout.group_label} soni", rh.fmt_int(len(layout.groups))))
    tiles += sums

    headers = [labels[k] for k in keys]
    kinds = [layout.kinds.get(k) for k in keys]
    sortable = len(rows) >= _INTERACTIVE_MIN_ROWS
    widths = _column_widths(layout, keys, headers)

    def table_for(members: list[dict]) -> str:
        return rh.table(headers, [[r.get(k, "") for k in keys] for r in members], numbered=True,
                        interactive=sortable, tools=False, kinds=kinds, stack=True, group=True, widths=widths)

    blocks = []
    if layout.grouped:
        measure = numeric[0] if numeric else None
        if 2 <= len(layout.groups) <= _DISTRIBUTION_MAX_GROUPS:
            # Taqqoslanadigan qiymat: summa ustuni bo'lsa — uning yig'indisi, bo'lmasa yozuvlar soni.
            # Chiziqlar qiymati bo'yicha kamayish tartibida (guruhlarning o'zi pastda nomi bo'yicha turadi).
            bars = []
            for index, item in enumerate(layout.groups, start=1):
                value = (sum(r.get(measure) for r in item.rows if rh.is_number(r.get(measure))) if measure
                         else len(item.rows))
                name = item.title if not item.context else f"{item.title} ({item.context})"
                bars.append((value, name, f"#g{index}"))
            bars.sort(key=lambda b: -b[0])
            blocks.append(rh.card(
                f"{layout.group_label} bo'yicha taqsimot",
                rh.hbars([b[1] for b in bars], [b[0] for b in bars], links=[b[2] for b in bars]),
                sub=(f"{labels[measure]} yig'indisi" if measure else "yozuvlar soni") + " · nom bosilsa o'sha guruhga o'tadi",
            ))
        if sortable:
            blocks.append(rh.search_bar())
        for index, item in enumerate(layout.groups, start=1):
            extra = ""
            if measure:
                subtotal = sum(r.get(measure) for r in item.rows if rh.is_number(r.get(measure)))
                extra = f"{labels[measure]}: {rh.fmt_num(subtotal, 2)}" if subtotal else ""
            blocks.append(rh.group(item.title, table_for(item.rows), context=item.context,
                                   count=len(item.rows), extra=extra, anchor=f"g{index}"))
    else:
        search = rh.search_bar() if sortable else ""
        blocks.append(f'<section class="card">{search}{table_for(rows)}</section>')

    page = rh.page(title=title, subtitle=description, tiles_html=rh.tiles(*tiles), body="\n".join(blocks),
                   wide=len(keys) >= 6)
    return _save(title, page)


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
    return _save(title, page)
