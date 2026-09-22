"""HTML hisobot generatsiya qilish — Python bilan avtomatik, tashqi API kerak emas."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from app.config import settings

REPORTS_DIR = Path(settings.STORAGE_DIR) / "reports"


def generate_report_link(*, title: str, description: str, rows: list[dict]) -> str | None:
    """Berilgan ma'lumotlar asosida mustaqil HTML sahifa yasaydi va havolani qaytaradi."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # Kategoriyalarni aniqlash (birinchi satrning kalitlari)
    if not rows:
        return None

    # Raqamli ustunlarni aniqlash
    numeric_cols = []
    string_cols = []
    sample = rows[0]
    for key in sample:
        val = sample[key]
        if isinstance(val, (int, float)):
            numeric_cols.append(key)
        else:
            string_cols.append(key)

    # Jadvallar HTML
    table_html = _build_table(rows, string_cols, numeric_cols)

    # Grafik ma'lumotlar (birinchi 2 ta raqamli ustun)
    chart_data = {}
    label_col = string_cols[0] if string_cols else None
    for col in numeric_cols[:3]:
        chart_data[col] = [(r.get(label_col, str(i)), r.get(col, 0)) for i, r in enumerate(rows)]

    chart_html = _build_charts(chart_data, title)

    # Jamlovchi ko'rsatkichlar
    summary_html = _build_summary(rows, numeric_cols, label_col)

    filename = f"{uuid.uuid4().hex}.html"
    html = f"""<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0f172a 100%); color: #e2e8f0; padding: 20px; min-height: 100vh; }}
.container {{ max-width: 900px; margin: 0 auto; }}
h1 {{ color: #ffffff; margin-bottom: 8px; font-size: 1.6em; font-weight: 800; background: linear-gradient(90deg, #a5b4fc, #e9d5ff); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
.subtitle {{ color: #94a3b8; margin-bottom: 24px; font-size: 0.95em; }}
.summary {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
.card {{ background: rgba(30,41,59,0.75); border: 1px solid rgba(148,163,184,0.12); border-radius: 16px; padding: 20px; flex: 1; min-width: 180px; box-shadow: 0 8px 24px rgba(0,0,0,0.25); }}
.card .label {{ font-size: 0.85em; color: #94a3b8; margin-bottom: 4px; }}
.card .value {{ font-size: 1.8em; font-weight: 700; color: #c7d2fe; }}
.chart-section {{ background: rgba(30,41,59,0.75); border: 1px solid rgba(148,163,184,0.12); border-radius: 16px; padding: 24px; margin-bottom: 24px; box-shadow: 0 8px 24px rgba(0,0,0,0.25); }}
.chart-title {{ font-size: 1.1em; font-weight: 600; margin-bottom: 16px; color: #f1f5f9; }}
.bar-chart {{ display: flex; flex-direction: column; gap: 8px; }}
.bar-row {{ display: flex; align-items: center; gap: 12px; }}
.bar-label {{ min-width: 120px; font-size: 0.9em; text-align: right; color: #cbd5e1; }}
.bar-container {{ flex: 1; background: rgba(148,163,184,0.14); border-radius: 6px; height: 28px; overflow: hidden; }}
.bar {{ height: 100%; border-radius: 6px; background: linear-gradient(90deg, #6366f1, #8b5cf6); transition: width 0.5s; }}
.bar-value {{ min-width: 100px; font-size: 0.85em; color: #e2e8f0; font-weight: 500; }}
.kpi-row {{ display: flex; gap: 12px; flex-wrap: wrap; }}
.kpi-chip {{ background: rgba(99,102,241,0.12); border: 1px solid rgba(148,163,184,0.15); border-radius: 12px; padding: 14px 18px; min-width: 130px; text-align: center; }}
.kpi-chip .kpi-value {{ font-size: 1.5em; font-weight: 700; color: #c7d2fe; }}
.kpi-chip .kpi-label {{ font-size: 0.8em; color: #94a3b8; margin-top: 2px; }}
table {{ width: 100%; border-collapse: collapse; border-radius: 12px; overflow: hidden; background: rgba(30,41,59,0.5); border: 1px solid rgba(148,163,184,0.12); }}
th {{ background: rgba(99,102,241,0.2); color: #e0e7ff; padding: 12px 16px; text-align: left; font-size: 0.9em; border-bottom: 1px solid rgba(148,163,184,0.15); }}
td {{ padding: 10px 16px; border-bottom: 1px solid rgba(148,163,184,0.1); font-size: 0.9em; color: #e2e8f0; }}
tr:last-child td {{ border-bottom: none; }}
tr:hover td {{ background: rgba(99,102,241,0.08); }}
.number {{ text-align: right; font-variant-numeric: tabular-nums; }}
.footer {{ text-align: center; color: #64748b; font-size: 0.8em; margin-top: 24px; }}
</style>
</head>
<body>
<div class="container">
<h1>{title}</h1>
<p class="subtitle">{description} — {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
{summary_html}
{chart_html}
{table_html}
<p class="footer">dunyabunya agent tomonidan generatsiya qilindi</p>
</div>
</body>
</html>"""

    out_path = REPORTS_DIR / filename
    out_path.write_text(html, encoding="utf-8")

    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return f"{base}/reports/{filename}"


def _format_number(n) -> str:
    """Katta sonlarni ixcham formatlash: 120 mlrd, 20 mlrd, 500 mln."""
    if isinstance(n, (int, float)):
        n = float(n)
        if n >= 1_000_000_000:
            return f"{n / 1_000_000_000:.1f} mlrd".replace(".0", "")
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f} mln".replace(".0", "")
        if n >= 1_000:
            return f"{n / 1_000:.1f} ming".replace(".0", "")
        if n == int(n):
            return f"{int(n):,}".replace(",", " ")
        return f"{n:,.2f}".replace(",", " ")
    return str(n)


def _build_summary(rows: list[dict], numeric_cols: list[str], label_col: str | None) -> str:
    cards = []
    for col in numeric_cols[:4]:
        total = sum(r.get(col, 0) for r in rows if isinstance(r.get(col), (int, float)))
        cards.append(f'<div class="card"><div class="label">{col}</div><div class="value">{_format_number(total)}</div></div>')
    if len(rows) > 1 and label_col:
        cards.append(f'<div class="card"><div class="label">Jami qatorlar</div><div class="value">{len(rows)}</div></div>')
    return f'<div class="summary">{"".join(cards)}</div>' if cards else ""


def _chart_worthy(items: list) -> bool:
    """Grafik chizish qoidasi: kamida 2 ta haqiqiy (nol bo'lmagan) qiymat bo'lishi shart.
    Bo'sh, yagona qiymatli yoki barchasi 0 bo'lgan ko'rsatkichlar uchun grafik chizilmaydi."""
    if not items or len(items) < 2:
        return False
    non_zero = sum(1 for _, v in items if isinstance(v, (int, float)) and v != 0)
    return non_zero >= 2


def _build_kpi_chips(items: list) -> str:
    """Grafik o'rniga ixcham KPI metrika kartochkalari (katta son + ostida nom)."""
    chips = []
    for label, value in items:
        if isinstance(value, (int, float)) and value == 0:
            continue
        chips.append(
            f'<div class="kpi-chip"><div class="kpi-value">{_format_number(value)}</div>'
            f'<div class="kpi-label">{str(label)[:30]}</div></div>'
        )
    return f'<div class="kpi-row">{"".join(chips)}</div>' if chips else ""


def _build_charts(chart_data: dict, title: str) -> str:
    sections = []
    for col, items in chart_data.items():
        if not items:
            continue
        if not _chart_worthy(items):
            # Yetarli ma'lumot yo'q — bo'sh grafik gridlari o'rniga KPI kartochkalari
            kpi = _build_kpi_chips(items)
            if kpi:
                sections.append(
                    f'<div class="chart-section">'
                    f'<div class="chart-title">{col}</div>'
                    f'{kpi}'
                    f'</div>'
                )
            continue
        max_val = max(v for _, v in items if isinstance(v, (int, float))) or 1
        bars = []
        for label, value in items:
            pct = (value / max_val) * 100
            display_label = str(label)[:20]
            bars.append(
                f'<div class="bar-row">'
                f'<div class="bar-label">{display_label}</div>'
                f'<div class="bar-container"><div class="bar" style="width:{pct:.0f}%"></div></div>'
                f'<div class="bar-value">{_format_number(value)}</div>'
                f'</div>'
            )
        sections.append(
            f'<div class="chart-section">'
            f'<div class="chart-title">{col}</div>'
            f'<div class="bar-chart">{"".join(bars)}</div>'
            f'</div>'
        )
    return "\n".join(sections)


def _build_table(rows: list[dict], string_cols: list[str], numeric_cols: list[str]) -> str:
    all_cols = string_cols + numeric_cols
    if not all_cols:
        return ""

    headers = "".join(f"<th>{col}</th>" for col in all_cols)
    body_rows = []
    for row in rows:
        cells = []
        for col in all_cols:
            val = row.get(col, "")
            cls = ' class="number"' if col in numeric_cols else ""
            if isinstance(val, (int, float)) and val == 0:
                # 0 qiymatlar bo'sh qoldiriladi
                cells.append(f"<td{cls}></td>")
            else:
                cells.append(f"<td{cls}>{_format_number(val) if isinstance(val, (int, float)) else val}</td>")
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    return f'<table><thead><tr>{headers}</tr></thead><tbody>{"".join(body_rows)}</tbody></table>'
