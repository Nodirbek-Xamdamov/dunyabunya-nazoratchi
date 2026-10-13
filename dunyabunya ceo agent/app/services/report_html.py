"""HTML hisobot sahifalari uchun umumiy qurilish bloklari (dizayn tizimi).

Sahifa to'liq mustaqil: CSS va kichik skript ichida, tashqi kutubxona/shrift so'ralmaydi —
Telegram'dan yuklab olingan fayl internetsiz ham, telefonda ham to'liq ochiladi.

Tamoyillar:
- grafiklar HTML/CSS bilan chiziladi (skriptsiz ham ko'rinadi); skript faqat qulaylik
  qo'shadi — ustiga borganda qiymat, jadvalda qidiruv/saralash, yorug'/qorong'i mavzu;
- rang ma'lumotni bildiradi, bezak emas: bitta ko'rsatkich — bitta rang, ikki qatorli
  grafikda ikki rang + izoh (legend). Ranglar yorug' va qorong'i fon uchun alohida
  tanlangan va rang ajrata olmaydiganlar uchun tekshirilgan (ko'k/to'q sariq juftligi);
- har bir grafikdagi son jadvalda ham bor; matn hech qachon grafik rangida yozilmaydi;
- Odoo'dan kelgan barcha matn ekranlanadi ("A&B <Savdo>" sahifani buzmaydi).
"""

from __future__ import annotations

import html
import json
import math
import re
from datetime import datetime

from app.services import list_layout as ll

# ── Qiymatlarni yozish ──────────────────────────────────────────────────────


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def slug(title) -> str:
    """Sarlavhadan fayl nomi uchun xavfsiz bo'lak: "Xodimlar ro'yxati" -> "xodimlar_royxati"
    (Telegram'da fayl "Xodimlar Royxati" bo'lib ko'rinadi)."""
    text = re.sub(r"[^a-z0-9Ѐ-ӿ]+", "_", str(title).lower().replace("'", "").replace("‘", "")
                  .replace("’", "").replace("ʻ", ""))
    return re.sub(r"_+", "_", text).strip("_")[:40] or "hisobot"


def is_number(value) -> bool:
    """bool Python'da int hisoblanadi — Odoo bo'sh maydonni False qaytargani uchun
    u son sifatida qaralmasligi kerak."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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


def fmt_full(n) -> str:
    """Qisqartirilmagan to'liq qiymat (ixcham yozilgan son ustiga borganda ko'rinadi)."""
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return str(n)
    return (f"{int(n):,}" if n == int(n) else f"{n:,.2f}").replace(",", " ")


def fmt_pct(part, whole) -> str:
    if not whole:
        return "0%"
    pct = part / whole * 100
    return f"{pct:.0f}%" if abs(pct - round(pct)) < 0.05 or pct >= 99.95 else f"{pct:.1f}%"


# ── Stil ────────────────────────────────────────────────────────────────────

_LIGHT = ("color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink-2:#52514e;"
          "--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);"
          "--wash:rgba(11,11,11,.04);--series-1:#2a78d6;--series-2:#eb6834;--track:#cde2fb;")
_DARK = ("color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#ffffff;--ink-2:#c3c2b7;"
         "--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);"
         "--wash:rgba(255,255,255,.05);--series-1:#3987e5;--series-2:#d95926;--track:#0d366b;")

_CSS = (
    ":root{" + _LIGHT + "}"
    "@media (prefers-color-scheme:dark){:root:where(:not([data-theme=light])){" + _DARK + "}}"
    ":root[data-theme=dark]{" + _DARK + "}"
    # Bosmada har doim yorug' mavzu (qora fon siyohni behuda sarflaydi)
    "@media print{:root:root:root{" + _LIGHT + "}}"
    """
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;background:var(--page);color:var(--ink);padding:28px 16px 44px}
.wrap{max-width:1080px;margin:0 auto}
.wrap.wide{max-width:1360px}
.top{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:22px}
.brand{display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--ink-2);margin-bottom:8px}
.brand i{width:10px;height:10px;border-radius:3px;background:var(--series-1)}
h1{font-size:25px;line-height:1.2;font-weight:650;letter-spacing:-.01em}
.sub{color:var(--ink-2);font-size:13px;margin-top:6px}
.theme{flex:none;width:36px;height:36px;border-radius:10px;border:1px solid var(--border);background:var(--surface);color:var(--ink-2);font-size:16px;line-height:1;cursor:pointer}
.theme:hover{background:var(--wash)}
.tiles{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:16px}
.tile{flex:1 1 150px;background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px;min-width:0}
.tile.hero{flex:2 1 320px}
.tile .lbl{font-size:13px;color:var(--ink-2)}
.tile .val{font-size:26px;font-weight:600;line-height:1.15;margin-top:6px;letter-spacing:-.01em;overflow-wrap:anywhere}
.tile.hero .val{font-size:clamp(40px,12vw,48px);line-height:1.05}
.tile .nt{font-size:12px;color:var(--muted);margin-top:6px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;margin-bottom:16px;min-width:0}
.card h2{font-size:15px;font-weight:600;margin-bottom:14px}
.card h2.hs{margin-bottom:2px}
.card .cs{font-size:12px;color:var(--muted);margin-bottom:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-bottom:16px}
.grid>.card{margin-bottom:0}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12px;color:var(--ink-2);margin-bottom:12px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.sw{width:10px;height:10px;border-radius:2px;background:var(--series-1);flex:none}
.sw.s2{background:var(--series-2)}.sw.pt{background:var(--track)}
.cols{margin-top:4px}
.cols-plot{position:relative;height:220px;margin:18px 0 0 58px}
.cols-tick{position:absolute;left:0;right:0;height:0;border-top:1px solid var(--grid)}
.cols-tick.base{border-top-color:var(--axis)}
.cols-tick span{position:absolute;right:calc(100% + 8px);top:-8px;font-size:11px;line-height:16px;color:var(--muted);white-space:nowrap;font-variant-numeric:tabular-nums}
.cols-bars{position:absolute;inset:0;display:flex;align-items:flex-end;gap:2px;padding:0 6px}
.col{flex:1 1 0;min-width:0;height:100%;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;outline:none}
.col-val{font-size:11px;line-height:14px;color:var(--ink-2);white-space:nowrap;margin-bottom:4px}
.col-stack{flex:none;width:100%;max-width:24px;min-height:2px;display:flex;flex-direction:column-reverse;gap:2px;border-radius:4px 4px 0 0;overflow:hidden}
.seg{background:var(--series-1);min-height:0}
.seg.s2{background:var(--series-2)}.seg.pt{background:var(--track)}
.col:hover .seg,.col:focus-visible .seg{filter:brightness(1.12)}
.col:focus-visible .col-stack{outline:2px solid var(--ink);outline-offset:2px}
.cols-x{display:flex;gap:2px;padding:6px 6px 0;margin-left:58px}
.cols-x span{flex:1 1 0;min-width:0;text-align:center;font-size:11px;line-height:14px;color:var(--muted);white-space:nowrap;overflow:hidden}
.cols-x small{display:block;font-size:10px}
.cols+.tbl,.cols+.tools{margin-top:22px}
.hbars{display:grid;grid-template-columns:fit-content(38%) 1fr;gap:10px 14px;align-items:center}
.hb-l{font-size:13px;color:var(--ink-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hb{display:flex;align-items:center;gap:8px;min-width:0;outline:none}
.hb i{flex:none;height:14px;min-width:2px;border-radius:0 4px 4px 0;background:var(--series-1)}
.hb:hover i,.hb:focus-visible i{filter:brightness(1.12)}
.hb span{font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap}
.meter-pct{font-size:32px;font-weight:600;line-height:1.1;letter-spacing:-.01em}
.meter-cap{font-size:13px;color:var(--ink-2);margin:2px 0 14px}
.meter-track{height:12px;border-radius:6px;background:var(--track);overflow:hidden}
.meter-fill{height:100%;min-width:2px;background:var(--series-1);border-radius:0 4px 4px 0}
.meter-legend{display:flex;flex-wrap:wrap;gap:8px 20px;margin-top:14px;font-size:13px;color:var(--ink-2)}
.meter-legend span{display:inline-flex;align-items:center;gap:6px}
.meter-legend b{color:var(--ink);font-weight:600}
.tools{display:flex;align-items:center;gap:12px;margin-bottom:12px}
.tools input{flex:1;min-width:0;max-width:320px;font:inherit;font-size:14px;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--page);color:var(--ink)}
.tools input:focus{outline:2px solid var(--series-1);outline-offset:1px}
.tools .cnt{font-size:12px;color:var(--muted);white-space:nowrap}
.tbl{overflow-x:auto;-webkit-overflow-scrolling:touch}
.tbl.tall{max-height:72vh;overflow:auto}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{position:sticky;top:0;z-index:1;background:var(--surface);text-align:left;font-weight:600;font-size:12px;color:var(--ink-2);padding:10px 12px;white-space:nowrap;box-shadow:inset 0 -1px 0 var(--axis)}
th[data-s]{cursor:pointer;user-select:none}
th[aria-sort=ascending]::after{content:" \\25B2";font-size:9px}
th[aria-sort=descending]::after{content:" \\25BC";font-size:9px}
td{padding:9px 12px;border-bottom:1px solid var(--grid);vertical-align:top}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:var(--wash)}
.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.s{white-space:nowrap}
.w{min-width:210px}
.i{color:var(--muted);text-align:right;font-variant-numeric:tabular-nums;width:1%;white-space:nowrap}
tbody.auto{counter-reset:r}tbody.auto tr:not([hidden]){counter-increment:r}tbody.auto td.i::before{content:counter(r)}
tr[hidden]{display:none}
.rb{height:6px;min-width:2px;border-radius:0 3px 3px 0;background:var(--series-1);margin-top:6px}
.empty{color:var(--muted);font-size:13px}
a.lnk{color:inherit;text-decoration:underline;text-decoration-color:var(--axis);text-underline-offset:3px}
a.lnk:hover,a.lnk:focus-visible{text-decoration-color:var(--series-1)}
.pp{display:block;font-size:11.5px;line-height:1.35;color:var(--muted);margin-top:2px}
.pw{min-width:150px}
.nw{white-space:nowrap}
table.fx{table-layout:fixed}
table.fx td,table.fx th{overflow-wrap:anywhere}
table.fx .s{white-space:normal}
table.fx .w,table.fx .pw{min-width:0}
col.ci{width:44px}
.tools.all{margin:4px 0 14px}
.tools.all input{max-width:420px;background:var(--surface)}
.grp{background:var(--surface);border:1px solid var(--border);border-radius:12px;margin-bottom:12px;min-width:0}
.grp>summary{display:flex;align-items:baseline;gap:6px 12px;flex-wrap:wrap;padding:14px 20px;cursor:pointer;list-style:none;border-radius:12px}
.grp>summary::-webkit-details-marker{display:none}
.grp>summary::before{content:"";flex:none;align-self:center;width:6px;height:6px;margin:0 4px 2px 2px;border-right:1.5px solid var(--muted);border-bottom:1.5px solid var(--muted);transform:rotate(-45deg);transition:transform .15s}
.grp[open]>summary::before{transform:rotate(45deg)}
.grp>summary:hover{background:var(--wash)}
.grp>summary:focus-visible{outline:2px solid var(--series-1);outline-offset:-2px}
.gt{font-size:15px;font-weight:600}
.gc{flex:1 1 120px;min-width:0;font-size:12px;color:var(--muted)}
.gs{font-size:12px;color:var(--ink-2);white-space:nowrap;font-variant-numeric:tabular-nums}
.gn{font-size:12px;color:var(--ink-2);white-space:nowrap;font-variant-numeric:tabular-nums;background:var(--wash);border-radius:999px;padding:2px 10px}
.grp>.tbl{padding:0 20px 10px}
.grp th{position:static}
.hb-l a{color:inherit;text-decoration:none}
.hb-l a:hover{text-decoration:underline;text-underline-offset:3px}
.note{color:var(--muted);font-size:12px;margin-top:12px}
footer{color:var(--muted);font-size:12px;text-align:center;margin-top:24px}
.tip{position:fixed;z-index:9;pointer-events:none;background:var(--ink);color:var(--surface);border-radius:8px;padding:8px 10px;font-size:12px;line-height:1.45;box-shadow:0 4px 16px rgba(0,0,0,.2);max-width:260px}
.tip .tt{opacity:.75;margin-bottom:2px}
.tip .tr{display:flex;align-items:center;gap:6px;white-space:nowrap}
.tip .tr b{font-size:13px}
.tip .tr span{opacity:.75}
.tip .k{width:10px;height:2px;border-radius:1px;background:var(--series-1);flex:none}
.tip .k.s2{background:var(--series-2)}.tip .k.pt{background:var(--track)}
@media (max-width:600px){body{padding:18px 12px 32px}h1{font-size:21px}.card{padding:16px}.cols-plot,.cols-x{margin-left:50px}td,th{padding-left:10px;padding-right:10px}}
@media (max-width:640px){
.grp>summary{padding:12px 14px}.grp>.tbl{padding:0 14px 6px}
table.stack thead{display:none}
table.stack,table.stack tbody,table.stack tr,table.stack td{display:block}
table.stack colgroup{display:none}
table.stack tr{padding:10px 0;border-bottom:1px solid var(--grid)}
table.stack tbody tr:last-child{border-bottom:0}
table.stack tr[hidden]{display:none}
table.stack td{border:0;padding:1px 0;min-width:0;white-space:normal;text-align:left;overflow-wrap:anywhere}
table.stack td.i,table.stack td:empty{display:none}
table.stack td.t{font-size:14.5px;font-weight:600;padding-bottom:3px}
table.stack td[data-l]:not(.t)::before{content:attr(data-l) ": ";font-size:12px;color:var(--muted)}
table.stack .pp{display:inline;margin:0 0 0 6px}
table.stack tbody tr:hover td{background:none}
}
@media print{body{padding:0;background:#fff}.theme,.tools{display:none!important}.card,.tile{break-inside:avoid}.grp>summary::before{display:none}.grp tr{break-inside:avoid}.tbl,.tbl.tall{max-height:none;overflow:visible}th{position:static}thead{display:table-header-group}.seg,.rb,.hb i,.meter-fill,.meter-track,.sw,.brand i{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""
)

# Skript faqat qulaylik qo'shadi — usiz ham sahifa to'liq o'qiladi. Ma'lumot DOM'ga faqat
# textContent orqali qo'yiladi (Odoo'dagi nomlar HTML sifatida bajarilmaydi).
_JS = """
(function(){
var d=document,root=d.documentElement;
var fmtN=function(n){return String(n).replace(/\\B(?=(\\d{3})+(?!\\d))/g,' ');};
var btn=d.querySelector('.theme');
if(btn){btn.hidden=false;btn.addEventListener('click',function(){
var dark=root.dataset.theme?root.dataset.theme==='dark':matchMedia('(prefers-color-scheme: dark)').matches;
root.dataset.theme=dark?'light':'dark';});}
var tip=null;
function hide(){if(tip)tip.hidden=true;}
function show(el,x,y){var data;try{data=JSON.parse(el.getAttribute('data-tip'));}catch(e){return;}
if(!tip){tip=d.createElement('div');tip.className='tip';d.body.appendChild(tip);}
tip.textContent='';
if(data.t){var t=d.createElement('div');t.className='tt';t.textContent=data.t;tip.appendChild(t);}
(data.r||[]).forEach(function(r){var row=d.createElement('div');row.className='tr';
var k=d.createElement('i');k.className='k '+(r[2]||'');row.appendChild(k);
var b=d.createElement('b');b.textContent=r[1];row.appendChild(b);
if(r[0]){var s=d.createElement('span');s.textContent=r[0];row.appendChild(s);}
tip.appendChild(row);});
tip.hidden=false;
var w=tip.offsetWidth,h=tip.offsetHeight;
var left=Math.min(Math.max(8,x+14),innerWidth-w-8),top=y-h-12;if(top<8)top=y+18;
tip.style.left=left+'px';tip.style.top=top+'px';}
d.querySelectorAll('[data-tip]').forEach(function(el){el.removeAttribute('title');
el.addEventListener('pointermove',function(e){show(el,e.clientX,e.clientY);});
el.addEventListener('pointerleave',hide);
el.addEventListener('focus',function(){var r=el.getBoundingClientRect();show(el,r.left+r.width/2,r.top);});
el.addEventListener('blur',hide);});
d.addEventListener('scroll',hide,true);
d.querySelectorAll('table[data-x]').forEach(function(tb){
var body=tb.tBodies[0];if(!body)return;
var rows=Array.prototype.slice.call(body.rows);
var tools=tb.closest('.card')?tb.closest('.card').querySelector('.tools'):null;
if(tools){tools.hidden=false;var inp=tools.querySelector('input'),cnt=tools.querySelector('.cnt');
var fmt=function(n){return String(n).replace(/\\B(?=(\\d{3})+(?!\\d))/g,' ');};
var upd=function(){var q=inp.value.trim().toLowerCase(),n=0;
rows.forEach(function(r){var ok=!q||r.textContent.toLowerCase().indexOf(q)>=0;r.hidden=!ok;if(ok)n++;});
cnt.textContent=q?(fmt(n)+' / '+fmt(rows.length)+' ta'):(fmt(rows.length)+' ta');};
inp.addEventListener('input',upd);upd();}
Array.prototype.forEach.call(tb.tHead.rows[0].cells,function(th,ci){
if(!th.hasAttribute('data-s'))return;
th.addEventListener('click',function(){
var asc=th.getAttribute('aria-sort')!=='ascending';
Array.prototype.forEach.call(tb.tHead.rows[0].cells,function(o){o.removeAttribute('aria-sort');});
th.setAttribute('aria-sort',asc?'ascending':'descending');
var key=function(r){var c=r.cells[ci];if(!c)return '';var v=c.getAttribute('data-v');
return v!==null?parseFloat(v):c.textContent.trim().toLowerCase();};
rows.sort(function(a,b){var x=key(a),y=key(b);
if(typeof x==='number'&&typeof y==='number')return asc?x-y:y-x;
x=String(x);y=String(y);if(x===''&&y!=='')return 1;if(y===''&&x!=='')return -1;
return asc?x.localeCompare(y,undefined,{numeric:true}):y.localeCompare(x,undefined,{numeric:true});});
rows.forEach(function(r){body.appendChild(r);});});});});
d.querySelectorAll('.tools[data-all]').forEach(function(tools){
var rows=Array.prototype.slice.call(d.querySelectorAll('table[data-g] tbody tr'));
if(!rows.length)return;
var groups=Array.prototype.slice.call(d.querySelectorAll('.grp'));
var inp=tools.querySelector('input'),cnt=tools.querySelector('.cnt');
tools.hidden=false;
var upd=function(){var q=inp.value.trim().toLowerCase(),n=0;
rows.forEach(function(r){var ok=!q||r.textContent.toLowerCase().indexOf(q)>=0;r.hidden=!ok;if(ok)n++;});
groups.forEach(function(g){var vis=g.querySelectorAll('tbody tr:not([hidden])').length;
g.hidden=!vis;if(q&&vis)g.open=true;
var b=g.querySelector('.gn');if(b)b.textContent=(q?fmtN(vis)+' / ':'')+b.getAttribute('data-n')+' ta';});
cnt.textContent=q?(fmtN(n)+' / '+fmtN(rows.length)+' ta'):(fmtN(rows.length)+' ta');};
inp.addEventListener('input',upd);upd();});
addEventListener('beforeprint',function(){d.querySelectorAll('details').forEach(function(x){x.open=true;});});
})();
"""


def _tip(title, rows) -> str:
    """Belgi (ustun/chiziq) uchun maslahat: `data-tip` (skript ko'rsatadi) va `title`
    (skriptsiz brauzerning o'zi ko'rsatadi). rows: [(nom, qiymat, rang-klassi)]."""
    payload = json.dumps({"t": str(title), "r": [[str(n), str(v), str(k)] for n, v, k in rows]},
                         ensure_ascii=False)
    plain = "; ".join(f"{n}: {v}" if n else str(v) for n, v, _k in rows)
    if title:
        plain = f"{title} — {plain}"
    return f'data-tip="{esc(payload)}" title="{esc(plain)}"'


# ── Bloklar ─────────────────────────────────────────────────────────────────

def tile(label, value, note: str = "", *, hero: bool = False) -> str:
    """Ko'rsatkich kartochkasi: nom, katta son va ixtiyoriy izoh. `hero` — sahifaning
    bosh raqami (bittadan ortiq bo'lmasin)."""
    extra = f'<div class="nt">{esc(note)}</div>' if note else ""
    return (f'<div class="tile{" hero" if hero else ""}"><div class="lbl">{esc(label)}</div>'
            f'<div class="val">{esc(value)}</div>{extra}</div>')


def tiles(*items: str) -> str:
    items = [i for i in items if i]
    return f'<section class="tiles">{"".join(items)}</section>' if items else ""


def card(title, *blocks: str, sub: str = "") -> str:
    head = (f'<h2 class="hs">{esc(title)}</h2><div class="cs">{esc(sub)}</div>' if sub
            else f"<h2>{esc(title)}</h2>")
    return f'<section class="card">{head}{"".join(b for b in blocks if b)}</section>'


def grid(*cards: str) -> str:
    return f'<div class="grid">{"".join(c for c in cards if c)}</div>'


def note(text) -> str:
    return f'<p class="note">{esc(text)}</p>' if text else ""


def empty(text: str = "Ma'lumot yo'q") -> str:
    return f'<p class="empty">{esc(text)}</p>'


def _nice_ticks(top: float, count: int = 4) -> list[float]:
    """O'q belgilari: 0 dan boshlab "chiroyli" qadam bilan (1, 2, 2.5, 5 x 10^n)."""
    if top <= 0:
        return [0.0, 1.0]
    raw = top / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if raw <= m * magnitude)
    return [i * step for i in range(math.ceil(top / step - 1e-9) + 1)]


def columns(labels, series, *, titles=None, sublabels=None, partial_last: bool = False) -> str:
    """Ustunli grafik (vaqt bo'yicha ko'rsatkich). `series`: [(nom, qiymatlar)] — bitta
    qator yoki ustma-ust ikki qator. `titles` — maslahatdagi to'liq nomlar ("Mart 2026"),
    `sublabels` — o'q yozuvi ostidagi ikkinchi qator (yil). `partial_last` — oxirgi davr
    hali tugamagan: ustuni ochroq rangda chiziladi, toki "tushib ketgan"dek ko'rinmasin."""
    labels = [str(lb) for lb in labels]
    series = [(str(name), [v if is_number(v) and v > 0 else 0 for v in values]) for name, values in series][:2]
    if not labels or not series:
        return empty()
    count = len(labels)
    titles = list(titles or labels)
    sublabels = list(sublabels or [""] * count)
    totals = [sum(values[i] for _name, values in series) for i in range(count)]
    if not any(totals):
        return empty()

    ticks = _nice_ticks(max(totals))
    top = ticks[-1]
    grid_html = "".join(
        f'<div class="cols-tick{" base" if t == 0 else ""}" style="bottom:{t / top * 100:.2f}%">'
        f'<span>{esc(fmt_num(t))}</span></div>'
        for t in ticks
    )

    # Qiymat har ustunga yozilmaydi (o'qib bo'lmaydigan shovqin) — faqat eng balandiga va
    # oxirgisiga; qolganlari o'qdan, maslahatdan va jadvaldan o'qiladi.
    peak = max(range(count), key=lambda i: totals[i])
    labelled = {peak}
    if count <= 6 or abs(count - 1 - peak) >= 2:
        labelled.add(count - 1)

    classes = ["", "s2"]
    bars = []
    for i in range(count):
        partial = partial_last and i == count - 1 and len(series) == 1
        segments, tip_rows = [], []
        for si, (name, values) in enumerate(series):
            cls = "pt" if partial else classes[si]
            if values[i]:
                share = values[i] / totals[i] * 100
                segments.append(f'<div class="seg {cls}" style="flex:{share:.3f} 1 0"></div>')
            tip_rows.append((name if len(series) > 1 else "", fmt_num(values[i], 2), cls))
        if len(series) > 1:
            tip_rows.append(("Jami", fmt_num(totals[i], 2), ""))
        value_label = f'<div class="col-val">{esc(fmt_num(totals[i]))}</div>' if i in labelled and totals[i] else ""
        stack = (f'<div class="col-stack" style="height:{totals[i] / top * 100:.2f}%">{"".join(segments)}</div>'
                 if totals[i] else "")
        tip_title = titles[i] + (" (oy tugamagan)" if partial else "")
        bars.append(f'<div class="col" tabindex="0" {_tip(tip_title, tip_rows)}>{value_label}{stack}</div>')

    axis = "".join(
        f"<span>{esc(lb)}{f'<small>{esc(sub)}</small>' if sub else ''}</span>"
        for lb, sub in zip(labels, sublabels)
    )
    legend = ""
    if len(series) > 1:
        legend = '<div class="legend">' + "".join(
            f'<span><i class="sw {classes[si]}"></i>{esc(name)}</span>' for si, (name, _v) in enumerate(series)
        ) + "</div>"
    elif partial_last:
        legend = ('<div class="legend"><span><i class="sw"></i>to\'liq oy</span>'
                  '<span><i class="sw pt"></i>joriy oy (hali tugamagan)</span></div>')
    return (f'{legend}<div class="cols"><div class="cols-plot">{grid_html}'
            f'<div class="cols-bars">{"".join(bars)}</div></div>'
            f'<div class="cols-x">{axis}</div></div>')


def hbars(labels, values, links=None) -> str:
    """Gorizontal chiziqlar — nomli toifalarni taqqoslash (qiymat chiziq uchida).
    `links` — har bir yorliq uchun sahifa ichidagi havola ("#g3"): bosilganda o'sha guruhga o'tadi."""
    pairs = [(str(lb), v if is_number(v) else 0) for lb, v in zip(labels, values)]
    links = list(links or [])
    if not pairs or not any(v for _lb, v in pairs):
        return empty()
    if len(pairs) == 1:
        # Bitta chiziqli "grafik" hech narsani taqqoslamaydi — sonning o'zi yoziladi
        label, value = pairs[0]
        return f'<div class="meter-pct">{esc(fmt_num(value))}</div><div class="meter-cap">{esc(label)}</div>'
    top = max(abs(v) for _lb, v in pairs) or 1
    cells = []
    for index, (label, value) in enumerate(pairs):
        share = abs(value) / top
        name = esc(label)
        if index < len(links) and links[index]:
            name = f'<a href="{esc(links[index])}">{name}</a>'
        cells.append(
            f'<div class="hb-l" title="{esc(label)}">{name}</div>'
            f'<div class="hb" tabindex="0" {_tip(label, [("", fmt_full(value), "")])}>'
            f'<i style="width:calc((100% - 76px) * {share:.4f})"></i><span>{esc(fmt_num(value))}</span></div>'
        )
    return f'<div class="hbars">{"".join(cells)}</div>'


def meter(filled, total, filled_label: str, rest_label: str) -> str:
    """Ulush o'lchagichi: `filled` — `total`ning qancha qismi (masalan to'langan fakturalar)."""
    filled = filled if is_number(filled) else 0
    total = total if is_number(total) else 0
    if total <= 0:
        return empty()
    share = max(0.0, min(1.0, filled / total))
    rest = total - filled
    return (
        f'<div class="meter-pct">{esc(fmt_pct(filled, total))}</div>'
        f'<div class="meter-cap">{esc(filled_label.lower())}</div>'
        f'<div class="meter-track" role="img" aria-label="{esc(filled_label)}: {esc(fmt_pct(filled, total))}">'
        f'<div class="meter-fill" style="width:{share * 100:.2f}%"></div></div>'
        f'<div class="meter-legend">'
        f'<span><i class="sw"></i>{esc(filled_label)} <b>{esc(fmt_num(filled, 2))}</b></span>'
        f'<span><i class="sw pt"></i>{esc(rest_label)} <b>{esc(fmt_num(rest, 2))}</b> '
        f'({esc(fmt_pct(rest, total))})</span></div>'
    )


def _number_cell(value, attrs: str = "") -> str:
    if not value:
        return f'<td class="n"{attrs}></td>'  # 0 qiymatlar bo'sh qoldiriladi
    short = fmt_num(value, 2)
    full = fmt_full(value)
    hint = f' title="{esc(full)}"' if short != full else ""
    return f'<td class="n" data-v="{value:.10g}"{hint}{attrs}>{esc(short)}</td>'


def _smart_cell(cell, kind) -> tuple[str, str, str] | None:
    """Turi ma'lum ustun katagi: (ichki HTML, klass, qo'shimcha atributlar). Telefon va email —
    bosiladigan havola, sana — kun.oy.yil (saralash kaliti bilan), pog'onali nom — oxirgi bo'g'in
    va undan oldingilari kichik yozuvda. Oddiy matn uchun None."""
    value = "" if cell in (None, False) else str(cell)
    if not value:
        return None
    if kind == "phone":
        parts = ll.phone_parts(value)
        if parts:
            links = ", ".join(f'<a class="lnk" href="tel:{esc(tel)}">{esc(shown)}</a>' for shown, tel in parts)
            return links, "nw", ""
    elif kind == "email" and "@" in value and " " not in value.strip():
        return f'<a class="lnk" href="mailto:{esc(value.strip())}">{esc(value.strip())}</a>', "", ""
    elif kind in ("date", "datetime"):
        key = ll.date_sort_key(value)
        if key:
            return esc(ll.date_text(value)), "nw", f' data-v="{key}"'
    elif kind == "path":
        leaf, parents = ll.split_path(value)
        if parents:
            return f'{esc(leaf)}<span class="pp">{esc(parents)}</span>', "pw", ""
    return None


def table(headers, rows, *, numbered: bool = False, interactive: bool = False,
          bar_col: int | None = None, label_col: int = 0,
          kinds: list | None = None, stack: bool = False, tools: bool = True, group: bool = False,
          widths: list | None = None) -> str:
    """Jadval. Son kataklari o'ngga tekislanadi va ixcham yoziladi (to'liq qiymat — ustiga
    borganda), 0 qiymatlar bo'sh qoldiriladi.

    `interactive` — ustun sarlavhasini bosib saralash va (`tools`) qidiruv maydoni.
    `bar_col` — shu ustun qiymatiga mutanosib ingichka chiziq `label_col` katagi ostida
    chiziladi (reyting jadvali: "top mijozlar").
    `kinds` — ustun turlari (phone, email, date, datetime, path): kataklar shunga mos chiziladi.
    `stack` — yozuvlar ro'yxati: tor ekranda har qator "sarlavha + nom: qiymat" kartochkasiga
    aylanadi (ko'p ustunli jadval telefonda sig'maydi). `group` — sahifadagi umumiy qidiruv
    shu jadval qatorlarini ham filtrlaydi. `widths` — ustunlarning nisbiy kengligi: berilsa
    jadval shu nisbatda bo'linadi (bir necha guruh jadvalida ustunlar bir chiziqda turishi uchun)."""
    rows = [list(r) for r in rows]
    if not rows:
        return empty()
    colgroup = ""
    if widths and len(widths) == len(headers) and sum(widths) > 0:
        colgroup = ("<colgroup>" + ('<col class="ci">' if numbered else "")
                    + "".join(f'<col style="width:{w / sum(widths) * 100:.2f}%">' for w in widths) + "</colgroup>")
    kinds = list(kinds or []) + [None] * len(headers)
    numeric = [any(is_number(r[c]) for r in rows if c < len(r)) for c in range(len(headers))]
    bar_top = max((abs(r[bar_col]) for r in rows if is_number(r[bar_col])), default=0) if bar_col is not None else 0
    title_col = next((c for c in range(len(headers)) if not numeric[c]), 0) if stack else None

    sort_attr = " data-s" if interactive else ""
    head = ('<th class="i">#</th>' if numbered else "") + "".join(
        f'<th class="n"{sort_attr}>{esc(h)}</th>' if numeric[c] else f"<th{sort_attr}>{esc(h)}</th>"
        for c, h in enumerate(headers)
    )
    body = []
    for n, r in enumerate(rows, start=1):
        cells = [f'<td class="i">{"" if interactive else n}</td>'] if numbered else []
        for c, cell in enumerate(r):
            label = f' data-l="{esc(headers[c])}"' if stack and c < len(headers) else ""
            if is_number(cell):
                cells.append(_number_cell(cell, label))
                continue
            title = " t" if c == title_col else ""
            smart = _smart_cell(cell, kinds[c])
            if smart:
                inner, cls, attrs = smart
                cells.append(f'<td class="{(cls + title).strip()}"{attrs}{label}>{inner}</td>')
                continue
            text = esc("" if cell is False else cell)
            if bar_top and c == label_col and is_number(r[bar_col]):
                text += f'<div class="rb" style="width:{abs(r[bar_col]) / bar_top * 100:.1f}%"></div>'
                cells.append(f"<td{label}>{text}</td>")
                continue
            # Qisqa qiymat (sana, oy nomi, raqamli kod) tor ekranda ikki qatorga bo'linib ketmasin;
            # uzun matnli ustun esa siqilib, har katagi besh-olti qatorga cho'zilib ketmasin
            # (jadval sig'masa — gorizontal aylantiriladi)
            length = len(str(cell or ""))
            cls = ("s" if length <= 18 else "w" if length >= 34 else "") + title
            cells.append(f'<td class="{cls.strip()}"{label}>{text}</td>' if cls.strip() else f"<td{label}>{text}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")

    tools_html = ('<div class="tools" hidden><input type="search" placeholder="Qidirish…" '
                  'aria-label="Jadvaldan qidirish"><span class="cnt"></span></div>') if interactive and tools else ""
    tall = " tall" if len(rows) > 15 and not group else ""
    classes = " ".join(c for c in ("stack" if stack else "", "fx" if colgroup else "") if c)
    attrs = (" data-x" if interactive else "") + (" data-g" if group else "") + (f' class="{classes}"' if classes else "")
    return (f'{tools_html}<div class="tbl{tall}"><table{attrs}>{colgroup}'
            f'<thead><tr>{head}</tr></thead>'
            f'<tbody{" class=auto" if interactive and numbered else ""}>{"".join(body)}</tbody></table></div>')


def search_bar() -> str:
    """Sahifadagi barcha guruh jadvallari uchun bitta qidiruv maydoni (skript yoqadi)."""
    return ('<div class="tools all" data-all hidden><input type="search" placeholder="Qidirish…" '
            'aria-label="Ro\'yxatdan qidirish"><span class="cnt"></span></div>')


def group(title, body: str, *, context: str = "", count: int = 0, extra: str = "", anchor: str = "") -> str:
    """Ro'yxatdagi bitta guruh (masalan bo'lim): sarlavha, nechta yozuv borligi va jadvali.
    Yig'ib-ochiladi (skriptsiz ham ishlaydi); bosmada hamma guruh ochiq chiqadi."""
    ident = f' id="{esc(anchor)}"' if anchor else ""
    more = f'<span class="gs">{esc(extra)}</span>' if extra else ""
    return (f'<details class="grp" open{ident}><summary><span class="gt">{esc(title)}</span>'
            f'<span class="gc">{esc(context)}</span>{more}'
            f'<span class="gn" data-n="{esc(fmt_int(count))}">{esc(fmt_int(count))} ta</span></summary>'
            f'{body}</details>')


def page(*, title: str, subtitle: str = "", tiles_html: str = "", body: str = "", wide: bool = False) -> str:
    """To'liq mustaqil HTML sahifa (tashqi so'rovsiz). `wide` — ko'p ustunli jadval uchun kengroq sahifa."""
    now = datetime.now()
    wrap_class = "wrap wide" if wide else "wrap"
    sub = " · ".join(p for p in (subtitle, f"tayyorlangan: {now.strftime('%Y-%m-%d %H:%M')}") if p)
    return f"""<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="{wrap_class}">
<header class="top">
<div><div class="brand"><i></i>dunyabunya</div><h1>{esc(title)}</h1><p class="sub">{esc(sub)}</p></div>
<button class="theme" type="button" hidden title="Yorug' / qorong'i mavzu" aria-label="Mavzuni almashtirish">&#9680;</button>
</header>
{tiles_html}
{body}
<footer>dunyabunya agent · {now.strftime('%Y-%m-%d')}</footer>
</div>
<script>{_JS}</script>
</body>
</html>"""
