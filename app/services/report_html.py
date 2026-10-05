"""HTML hisobot sahifalari uchun umumiy qurilish bloklari.

Barcha matn (sarlavha, yozuv nomlari, jadval kataklari) HTML uchun ekranlanadi —
Odoo'dagi "A&B <Savdo>" kabi nomlar sahifani buzmaydi. Grafiklar Chart.js bilan
chiziladi, lekin har birining ostida oddiy CSS-ustunli ko'rinishi ham bor: Chart.js
yuklanmasa (internet yo'q, Telegram'ning ichki ko'rish oynasi skriptni
ishlatmaydi) hisobot baribir to'liq o'qiladi.
"""

from __future__ import annotations

import html
import json
from datetime import datetime

_PALETTE = ["#6366f1", "#a855f7", "#ec4899", "#22d3ee", "#34d399", "#fbbf24", "#f43f5e", "#f472b6"]


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def js(value) -> str:
    """Qiymatni <script> ichiga xavfsiz joylash (JSON; "</script>" yorib chiqa olmaydi)."""
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def fmt_num(n, precision: int = 1) -> str:
    """Katta sonlar ixcham yoziladi (120 mlrd, 20.5 mln); million ostidagilar to'liq va
    aniq (8 269, 850 000) — buyurtmalar soni kabi sanoqlar yaxlitlanib ketmasligi uchun.
    `precision` — mln/mlrd uchun kasr xonalari (jadvalda 2: "1.53 mln")."""
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return str(n)
    sign, a = ("-" if n < 0 else ""), abs(n)
    for limit, unit in ((1_000_000_000, "mlrd"), (1_000_000, "mln")):
        if a >= limit:
            short = f"{a / limit:.{precision}f}"
            if "." in short:
                short = short.rstrip("0").rstrip(".")
            return f"{sign}{short} {unit}"
    if a == int(a):
        return f"{sign}{int(a):,}".replace(",", " ")
    return f"{sign}{a:,.2f}".replace(",", " ")


def fmt_int(n) -> str:
    try:
        return f"{int(n or 0):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(n)


def is_number(value) -> bool:
    """bool Python'da int hisoblanadi — Odoo bo'sh maydonni False qaytargani uchun
    u son sifatida qaralmasligi kerak."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def chart_ok(values) -> bool:
    """Grafik faqat kamida 2 ta nol bo'lmagan qiymat bo'lsa chiziladi."""
    return sum(1 for v in values if is_number(v) and v) >= 2


def bars(labels, values, color: str = "#6366f1") -> str:
    """Gorizontal ustunli grafik — faqat HTML/CSS (skriptsiz ham ko'rinadi)."""
    nums = [v if is_number(v) else 0 for v in values]
    top = max((abs(v) for v in nums), default=0) or 1
    rows = []
    for label, value in zip(labels, nums):
        pct = abs(value) / top * 100
        rows.append(
            f'<div class="bar-row"><div class="bar-label">{esc(label)}</div>'
            f'<div class="bar-container"><div class="bar" style="width:{pct:.0f}%;background:{color}"></div></div>'
            f'<div class="bar-value">{esc(fmt_num(value))}</div></div>'
        )
    return f'<div class="bar-chart">{"".join(rows)}</div>' if rows else ""


def kpi_chips(labels, values) -> str:
    """Grafik o'rniga ixcham kartochkalar (katta son + ostida nom); nol qiymatlar chiqmaydi."""
    chips = "".join(
        f'<div class="kpi-chip"><div class="kpi-value">{esc(fmt_num(v))}</div>'
        f'<div class="kpi-label">{esc(lb)}</div></div>'
        for lb, v in zip(labels, values) if is_number(v) and v
    )
    return f'<div class="kpi-row">{chips}</div>' if chips else '<p class="empty">Ma\'lumot yo\'q</p>'


def table(headers, rows, numbered: bool = False) -> str:
    """Jadval. Son kataklari o'ngga tekislanadi, 0 qiymatlar bo'sh qoldiriladi."""
    if not rows:
        return '<p class="empty">Ma\'lumot yo\'q</p>'
    head = ("<th>#</th>" if numbered else "") + "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = []
    for n, row in enumerate(rows, start=1):
        cells = [f'<td class="number">{n}</td>'] if numbered else []
        for cell in row:
            if is_number(cell):
                cells.append(f'<td class="number">{esc(fmt_num(cell, 2)) if cell else ""}</td>')
            else:
                cells.append(f"<td>{esc(cell)}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    return (f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


class Charts:
    """Sahifadagi grafiklar: `add()` joylashtiriladigan HTML'ni qaytaradi va Chart.js
    konfiguratsiyasini yig'adi, `script()` esa hammasini chizuvchi <script> beradi."""

    def __init__(self) -> None:
        self._configs: list[tuple[str, dict]] = []

    def add(self, labels, values, *, kind: str = "bar", horizontal: bool = False,
            color: str = "#6366f1", label: str = "") -> str:
        labels = [str(lb) for lb in labels]
        values = [v if is_number(v) else 0 for v in values]
        if not chart_ok(values):
            return kpi_chips(labels, values)
        cid = f"c{len(self._configs) + 1}"
        colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(values))]
        dataset: dict = {"label": label, "data": values,
                         "backgroundColor": colors if kind == "doughnut" else color}
        if kind != "doughnut":
            dataset["borderRadius"] = 6
        self._configs.append((cid, {"kind": kind, "horizontal": horizontal,
                                    "labels": labels, "dataset": dataset}))
        return (f'<div class="chart-wrap" id="w_{cid}" hidden><canvas id="{cid}"></canvas></div>'
                f'<div id="f_{cid}">{bars(labels, values, color)}</div>')

    def script(self) -> str:
        if not self._configs:
            return ""
        return (
            '<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>\n<script>\n'
            "(function(){\n"
            "if(!window.Chart)return;\n"
            "Chart.defaults.color='#cbd5e1';Chart.defaults.borderColor='rgba(148,163,184,0.08)';\n"
            "var short=function(v){var a=Math.abs(v);return a>=1e9?(v/1e9).toFixed(1)+' mlrd':"
            "a>=1e6?(v/1e6).toFixed(1)+' mln':a>=1e3?(v/1e3).toFixed(0)+' ming':v;};\n"
            f"var charts={js(self._configs)};\n"
            "charts.forEach(function(item){try{\n"
            "var id=item[0],c=item[1],round=c.kind==='doughnut';\n"
            "var opts={responsive:true,maintainAspectRatio:false,"
            "plugins:{legend:{display:round,position:'right',labels:{color:'#cbd5e1'}}}};\n"
            "if(!round){var axis={beginAtZero:true,grid:{color:'rgba(148,163,184,0.08)'},ticks:{callback:short}};"
            "opts.scales=c.horizontal?{x:axis}:{y:axis};if(c.horizontal)opts.indexAxis='y';}\n"
            "document.getElementById('w_'+id).hidden=false;"
            "document.getElementById('f_'+id).style.display='none';\n"
            "new Chart(document.getElementById(id).getContext('2d'),"
            "{type:c.kind,data:{labels:c.labels,datasets:[c.dataset]},options:opts});\n"
            "}catch(e){}});\n"
            "})();\n</script>"
        )


_CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif; background:linear-gradient(135deg,#0f172a 0%,#1e293b 50%,#0f172a 100%); color:#e2e8f0; padding:20px; min-height:100vh; }
.container { max-width:1000px; margin:0 auto; }
h1 { font-size:1.5em; margin-bottom:4px; font-weight:800; color:#fff; }
.sub { color:#94a3b8; font-size:.9em; margin-bottom:20px; }
.cards { display:flex; gap:14px; margin-bottom:20px; flex-wrap:wrap; }
.card { background:rgba(30,41,59,.75); border:1px solid rgba(148,163,184,.12); border-radius:16px; padding:18px 20px; flex:1; min-width:140px; box-shadow:0 8px 24px rgba(0,0,0,.25); text-align:center; }
.card .lbl { font-size:.8em; color:#94a3b8; margin-bottom:2px; }
.card .val { font-size:1.5em; font-weight:700; color:#c7d2fe; }
.section { background:rgba(30,41,59,.75); border:1px solid rgba(148,163,184,.12); border-radius:16px; padding:20px; margin-bottom:16px; box-shadow:0 8px 24px rgba(0,0,0,.25); }
.section h2 { font-size:1.1em; margin-bottom:14px; color:#f1f5f9; }
.row { display:flex; gap:16px; flex-wrap:wrap; }
.row > .section { flex:1; min-width:300px; }
.chart-wrap { height:270px; position:relative; margin-bottom:12px; }
.bar-chart { display:flex; flex-direction:column; gap:8px; margin-bottom:12px; }
.bar-row { display:flex; align-items:center; gap:12px; }
.bar-label { width:34%; max-width:220px; font-size:.85em; text-align:right; color:#cbd5e1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.bar-container { flex:1; background:rgba(148,163,184,.14); border-radius:6px; height:24px; overflow:hidden; }
.bar { height:100%; border-radius:6px; }
.bar-value { min-width:84px; font-size:.85em; color:#e2e8f0; font-weight:500; }
.table-wrap { overflow-x:auto; }
table { width:100%; border-collapse:collapse; font-size:.88em; background:rgba(30,41,59,.45); border:1px solid rgba(148,163,184,.12); }
th { background:rgba(99,102,241,.2); color:#e0e7ff; padding:9px 12px; text-align:left; border-bottom:1px solid rgba(148,163,184,.15); white-space:nowrap; }
td { padding:8px 12px; border-bottom:1px solid rgba(148,163,184,.1); color:#e2e8f0; vertical-align:top; }
tr:hover td { background:rgba(99,102,241,.08); }
.number { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
.kpi-row { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:8px; }
.kpi-chip { background:rgba(99,102,241,.12); border:1px solid rgba(148,163,184,.15); border-radius:12px; padding:12px 16px; min-width:110px; text-align:center; }
.kpi-chip .kpi-value { font-size:1.4em; font-weight:700; color:#c7d2fe; }
.kpi-chip .kpi-label { font-size:.78em; color:#94a3b8; margin-top:2px; }
.note { color:#94a3b8; font-size:.82em; margin-top:8px; }
.empty { color:#64748b; font-size:.9em; }
.footer { text-align:center; color:#64748b; font-size:.78em; margin-top:20px; }
"""


def card(label, value) -> str:
    return f'<div class="card"><div class="lbl">{esc(label)}</div><div class="val">{esc(value)}</div></div>'


def section(title, *blocks: str) -> str:
    return f'<div class="section"><h2>{esc(title)}</h2>{"".join(b for b in blocks if b)}</div>'


def row(*sections: str) -> str:
    return f'<div class="row">{"".join(sections)}</div>'


def note(text) -> str:
    return f'<p class="note">{esc(text)}</p>'


def page(*, title: str, subtitle: str = "", cards: list[str] | None = None,
         body: str = "", charts: Charts | None = None) -> str:
    """To'liq mustaqil HTML sahifa (CSS ichida; tashqi so'rov — faqat ixtiyoriy Chart.js)."""
    now = datetime.now()
    sub = f"{subtitle} | " if subtitle else ""
    cards_html = f'<div class="cards">{"".join(cards)}</div>' if cards else ""
    return f"""<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="container">
<h1>{esc(title)}</h1>
<p class="sub">{esc(sub)}{now.strftime('%Y-%m-%d %H:%M')}</p>
{cards_html}
{body}
<p class="footer">dunyabunya agent — {now.strftime('%Y-%m-%d')}</p>
</div>
{charts.script() if charts else ""}
</body>
</html>"""
