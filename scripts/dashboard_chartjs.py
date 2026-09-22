#!/usr/bin/env python3
"""
Generate a modern dashboard HTML using ONLY Chart.js graphs.
NO tables, NO plain text data — ONLY interactive charts.
"""
import os, sys, json, uuid, xmlrpc.client
from datetime import datetime
from collections import defaultdict

# Read from .env file in parent directory
_env = os.path.join(os.path.dirname(__file__), '..', '.env')
def _load_env():
    kv = {}
    if os.path.exists(_env):
        for line in open(_env):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                kv[k.strip()] = v.strip()
    return kv
_envd = _load_env()

URL = os.getenv("ODOO_URL", _envd.get("ODOO_URL", "http://localhost:8069"))
DB = os.getenv("ODOO_DB", _envd.get("ODOO_DB", "menga"))
LOGIN = os.getenv("ODOO_LOGIN", _envd.get("ODOO_LOGIN", "admin"))
PASSWORD = os.getenv("ODOO_PASSWORD", _envd.get("ODOO_API_KEY", "admin"))

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "storage", "reports")
os.makedirs(OUT_DIR, exist_ok=True)


def connect():
    uid = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common").authenticate(DB, LOGIN, PASSWORD, {})
    models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object")
    return uid, models


def search_read(models, uid, model, domain, fields, kw=None):
    return models.execute_kw(DB, uid, PASSWORD, model, "search_read", [domain], {"fields": fields, **(kw or {})})


def fmt(n):
    if n >= 1e12: return f"{n/1e12:.1f}T"
    if n >= 1e9: return f"{n/1e9:.1f}B"
    if n >= 1e6: return f"{n/1e6:.1f}M"
    if n >= 1e3: return f"{n/1e3:.1f}K"
    return f"{n:,.0f}"


def collect():
    uid, M = connect()

    invoices = search_read(M, uid, "account.move",
        [["move_type", "=", "out_invoice"], ["state", "=", "posted"]],
        ["invoice_date", "amount_total", "invoice_user_id"])
    inv_by_month = defaultdict(float)
    inv_by_manager = defaultdict(float)
    total_revenue = 0
    for inv in invoices:
        m = inv["invoice_date"][:7] if inv.get("invoice_date") else "N/A"
        inv_by_month[m] += inv["amount_total"]
        total_revenue += inv["amount_total"]
        nm = inv.get("invoice_user_id")
        if nm:
            nm = nm[1] if isinstance(nm, (list, tuple)) else str(nm)
            inv_by_manager[nm] += inv["amount_total"]

    bills = search_read(M, uid, "account.move",
        [["move_type", "=", "in_invoice"], ["state", "=", "posted"]],
        ["invoice_date", "amount_total"])
    bills_by_month = defaultdict(float)
    total_expense = 0
    for b in bills:
        m = b["invoice_date"][:7] if b.get("invoice_date") else "N/A"
        bills_by_month[m] += b["amount_total"]
        total_expense += b["amount_total"]

    orders = search_read(M, uid, "sale.order",
        [["state", "in", ["sale", "done"]]],
        ["date_order", "amount_total", "user_id"])
    orders_by_month = defaultdict(float)
    orders_count_by_month = defaultdict(int)
    orders_by_manager = defaultdict(float)
    total_sales = 0
    for o in orders:
        m = o["date_order"][:7] if o.get("date_order") else "N/A"
        orders_by_month[m] += o["amount_total"]
        orders_count_by_month[m] += 1
        total_sales += o["amount_total"]
        nm = o.get("user_id")
        if nm:
            nm = nm[1] if isinstance(nm, (list, tuple)) else str(nm)
            orders_by_manager[nm] += o["amount_total"]

    moves = search_read(M, uid, "stock.move", [["state", "=", "done"]],
        ["date", "product_id", "product_uom_qty"])
    stock_by_month = defaultdict(float)
    product_qty = defaultdict(float)
    for mv in moves:
        m = mv["date"][:7] if mv.get("date") else "N/A"
        stock_by_month[m] += mv["product_uom_qty"]
        pname = mv.get("product_id")
        if pname:
            pn = pname[1] if isinstance(pname, (list, tuple)) else str(pname)
            product_qty[pn] += mv["product_uom_qty"]

    leads = search_read(M, uid, "crm.lead", [],
        ["create_date", "stage_id", "user_id"])
    leads_by_month = defaultdict(int)
    leads_by_stage = defaultdict(int)
    leads_revenue = defaultdict(float)
    for l in leads:
        m = l["create_date"][:7] if l.get("create_date") else "N/A"
        leads_by_month[m] += 1
        stg = l.get("stage_id")
        if stg:
            sn = stg[1] if isinstance(stg, (list, tuple)) else str(stg)
            leads_by_stage[sn] += 1
        leads_revenue[m] += 0  # planned_revenue not available

    employees = search_read(M, uid, "hr.employee", [], ["name"])

    sorted_months = sorted(set(
        list(inv_by_month.keys()) + list(bills_by_month.keys()) +
        list(orders_by_month.keys())
    ))[-12:]

    return {
        "sorted_months": sorted_months,
        "month_labels": [datetime.strptime(m, "%Y-%m").strftime("%b %Y") for m in sorted_months],
        "inv_vals": [round(inv_by_month.get(m, 0) / 1e9, 2) for m in sorted_months],
        "bill_vals": [round(bills_by_month.get(m, 0) / 1e9, 2) for m in sorted_months],
        "order_vals": [round(orders_by_month.get(m, 0) / 1e9, 2) for m in sorted_months],
        "order_counts": [orders_count_by_month.get(m, 0) for m in sorted_months],
        "stock_vals": [round(stock_by_month.get(m, 0), 0) for m in sorted_months],
        "lead_vals": [leads_by_month.get(m, 0) for m in sorted_months],
        "lead_rev": [round(leads_revenue.get(m, 0) / 1e6, 2) for m in sorted_months],
        "top_managers": list(sorted(orders_by_manager.items(), key=lambda x: -x[1])[:10]),
        "top_products": list(sorted(product_qty.items(), key=lambda x: -x[1])[:12]),
        "lead_stages": dict(leads_by_stage),
        "total_revenue": total_revenue,
        "total_expense": total_expense,
        "total_sales": total_sales,
        "profit": total_revenue - total_expense,
        "employee_count": len(employees),
        "invoice_count": len(invoices),
        "order_count": len(orders),
        "lead_count": len(leads),
        "move_count": len(moves),
    }


def build_html(d):
    J = json.dumps
    m_labels = J(d["month_labels"])
    inv_vals = J(d["inv_vals"])
    bill_vals = J(d["bill_vals"])
    profit_vals = J([round(d["inv_vals"][i] - d["bill_vals"][i], 2) for i in range(len(d["inv_vals"]))])
    order_vals = J(d["order_vals"])
    order_counts = J(d["order_counts"])
    stock_vals = J(d["stock_vals"])
    lead_vals = J(d["lead_vals"])
    lead_rev = J(d["lead_rev"])

    mgr_labels = J([x[0] for x in d["top_managers"]])
    mgr_sales = J([round(x[1] / 1e9, 2) for x in d["top_managers"]])

    prod_labels = J([x[0] for x in d["top_products"]])
    prod_vals = J([round(x[1], 0) for x in d["top_products"]])

    stg_labels = J(list(d["lead_stages"].keys()))
    stg_vals = J(list(d["lead_stages"].values()))

    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    profit_margin = round((d["profit"] / d["total_revenue"] * 100) if d["total_revenue"] else 0, 1)

    # Color palette
    palette = "['#6366f1','#8b5cf6','#a855f7','#d946ef','#ec4899','#f43f5e','#ef4444','#f97316','#eab308','#22c55e','#06b6d4','#14b8a6']"

    return f"""<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dunyabunya Business Dashboard</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
body {{ font-family: 'Inter', sans-serif; background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0f172a 100%); min-height: 100vh; }}
.kpi {{ background: linear-gradient(135deg, rgba(99,102,241,0.15), rgba(139,92,246,0.1)); border: 1px solid rgba(99,102,241,0.2); }}
.kpi-green {{ background: linear-gradient(135deg, rgba(34,197,94,0.15), rgba(16,185,129,0.1)); border: 1px solid rgba(34,197,94,0.2); }}
.kpi-red {{ background: linear-gradient(135deg, rgba(239,68,68,0.15), rgba(244,63,94,0.1)); border: 1px solid rgba(239,68,68,0.2); }}
.kpi-yellow {{ background: linear-gradient(135deg, rgba(234,179,8,0.15), rgba(245,158,11,0.1)); border: 1px solid rgba(234,179,8,0.2); }}
.card {{ background: rgba(30,41,59,0.8); backdrop-filter: blur(16px); border: 1px solid rgba(148,163,184,0.08); border-radius: 16px; padding: 24px; transition: all 0.3s; }}
.card:hover {{ border-color: rgba(99,102,241,0.3); transform: translateY(-2px); box-shadow: 0 20px 40px rgba(0,0,0,0.3); }}
.pulse {{ animation: pulse 2s infinite; }}
@keyframes pulse {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:0.5; }} }}
</style>
</head>
<body class="text-white p-4 md:p-8">

<div class="flex items-center justify-between mb-8">
  <div>
    <h1 class="text-3xl md:text-4xl font-bold bg-gradient-to-r from-indigo-400 via-purple-400 to-pink-400 bg-clip-text text-transparent">
      Dunyabunya Dashboard
    </h1>
    <p class="text-slate-400 mt-1">Real-time business analytics &bull; {now_str}</p>
  </div>
  <div class="flex items-center gap-2">
    <span class="w-3 h-3 bg-green-400 rounded-full pulse"></span>
    <span class="text-green-400 text-sm font-medium">Live</span>
  </div>
</div>

<div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
  <div class="kpi rounded-2xl p-5">
    <div class="text-slate-400 text-sm mb-1">Tushum</div>
    <div class="text-2xl md:text-3xl font-bold text-indigo-300">{fmt(d['total_revenue'])}</div>
    <div class="text-xs text-slate-500 mt-1">{d['invoice_count']:,} ta faktura</div>
  </div>
  <div class="kpi-red rounded-2xl p-5">
    <div class="text-slate-400 text-sm mb-1">Xarajatlar</div>
    <div class="text-2xl md:text-3xl font-bold text-red-300">{fmt(d['total_expense'])}</div>
  </div>
  <div class="kpi-green rounded-2xl p-5">
    <div class="text-slate-400 text-sm mb-1">Sof foyda</div>
    <div class="text-2xl md:text-3xl font-bold text-green-300">{fmt(d['profit'])}</div>
    <div class="text-xs text-slate-500 mt-1">{profit_margin}% margin</div>
  </div>
  <div class="kpi-yellow rounded-2xl p-5">
    <div class="text-slate-400 text-sm mb-1">Buyurtmalar</div>
    <div class="text-2xl md:text-3xl font-bold text-yellow-300">{fmt(d['order_count'])}</div>
    <div class="text-xs text-slate-500 mt-1">{fmt(d['total_sales'])} sotuv</div>
  </div>
</div>

<div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
  <div class="card">
    <h3 class="text-lg font-semibold mb-4 text-slate-200">Tushum va Xarajatlar (mlrd so'm)</h3>
    <canvas id="c1" height="130"></canvas>
  </div>
  <div class="card">
    <h3 class="text-lg font-semibold mb-4 text-slate-200">Sotuv buyurtmalari (mlrd so'm)</h3>
    <canvas id="c2" height="130"></canvas>
  </div>
</div>

<div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
  <div class="card">
    <h3 class="text-lg font-semibold mb-4 text-slate-200">TOP-10 menejer — Sotuv (mlrd so'm)</h3>
    <canvas id="c3" height="160"></canvas>
  </div>
  <div class="card">
    <h3 class="text-lg font-semibold mb-4 text-slate-200">Menejerlar ulushi</h3>
    <canvas id="c4" height="160"></canvas>
  </div>
</div>

<div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
  <div class="card">
    <h3 class="text-lg font-semibold mb-4 text-slate-200">Ombor harakatlari</h3>
    <canvas id="c5" height="130"></canvas>
  </div>
  <div class="card">
    <h3 class="text-lg font-semibold mb-4 text-slate-200">Eng ko'p harakatlangan mahsulotlar</h3>
    <canvas id="c6" height="160"></canvas>
  </div>
</div>

<div class="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
  <div class="card">
    <h3 class="text-lg font-semibold mb-4 text-slate-200">CRM lidlar oqimi</h3>
    <canvas id="c7" height="140"></canvas>
  </div>
  <div class="card">
    <h3 class="text-lg font-semibold mb-4 text-slate-200">Lidlar bosqichlari</h3>
    <canvas id="c8" height="140"></canvas>
  </div>
  <div class="card">
    <h3 class="text-lg font-semibold mb-4 text-slate-200">Lidlar rejalashtirilgan tushum (mln)</h3>
    <canvas id="c9" height="140"></canvas>
  </div>
</div>

<div class="text-center text-slate-500 text-xs mt-8 mb-4">
  Dunyabunya Agent Tizimi &bull; Odoo real vaqtda ma'lumotlar &bull; {now_str}
</div>

<script>
Chart.defaults.color = '#94a3b8';
Chart.defaults.borderColor = 'rgba(148,163,184,0.08)';
const M = {m_labels};
const PAL = {palette};

// 1. Revenue + Expenses
new Chart(document.getElementById('c1'), {{
  type: 'bar',
  data: {{
    labels: M,
    datasets: [
      {{ label: 'Tushum', data: {inv_vals}, backgroundColor: 'rgba(99,102,241,0.7)', borderRadius: 6, borderSkipped: false }},
      {{ label: 'Xarajatlar', data: {bill_vals}, backgroundColor: 'rgba(239,68,68,0.7)', borderRadius: 6, borderSkipped: false }},
      {{ label: 'Foyda', data: {profit_vals}, type: 'line', borderColor: '#22c55e', backgroundColor: 'transparent', borderWidth: 2, pointRadius: 3, pointBackgroundColor: '#22c55e', tension: 0.3 }}
    ]
  }},
  options: {{ responsive: true, plugins: {{ legend: {{ position: 'top' }} }}, scales: {{ y: {{ beginAtZero: true, grid: {{ color: 'rgba(148,163,184,0.06)' }} }} }} }}
}});

// 2. Sales Orders
new Chart(document.getElementById('c2'), {{
  type: 'bar',
  data: {{
    labels: M,
    datasets: [
      {{ label: 'Sotuv (mlrd)', data: {order_vals}, backgroundColor: 'rgba(168,85,247,0.7)', borderRadius: 6, borderSkipped: false }},
      {{ label: 'Buyurtmalar soni', data: {order_counts}, type: 'line', borderColor: '#eab308', backgroundColor: 'transparent', borderWidth: 2, pointRadius: 3, pointBackgroundColor: '#eab308', tension: 0.3, yAxisID: 'y1' }}
    ]
  }},
  options: {{ responsive: true, plugins: {{ legend: {{ position: 'top' }} }}, scales: {{ y: {{ beginAtZero: true }}, y1: {{ position: 'right', beginAtZero: true, grid: {{ drawOnChartArea: false }} }} }} }}
}});

// 3. Top Managers Bar
new Chart(document.getElementById('c3'), {{
  type: 'bar',
  data: {{
    labels: {mgr_labels},
    datasets: [{{ label: 'Sotuv (mlrd)', data: {mgr_sales}, backgroundColor: PAL, borderRadius: 8, borderSkipped: false }}]
  }},
  options: {{ indexAxis: 'y', responsive: true, plugins: {{ legend: {{ display: false }} }}, scales: {{ x: {{ beginAtZero: true }} }} }}
}});

// 4. Managers Doughnut
new Chart(document.getElementById('c4'), {{
  type: 'doughnut',
  data: {{
    labels: {mgr_labels},
    datasets: [{{ data: {mgr_sales}, backgroundColor: PAL, borderWidth: 0 }}]
  }},
  options: {{ responsive: true, plugins: {{ legend: {{ position: 'right', labels: {{ padding: 12 }} }} }}, cutout: '60%' }}
}});

// 5. Stock Area
new Chart(document.getElementById('c5'), {{
  type: 'line',
  data: {{
    labels: M,
    datasets: [{{ label: 'Ombor harakati (dona)', data: {stock_vals}, borderColor: '#06b6d4', backgroundColor: 'rgba(6,182,212,0.15)', fill: true, tension: 0.4, borderWidth: 2, pointRadius: 4, pointBackgroundColor: '#06b6d4' }}]
  }},
  options: {{ responsive: true, plugins: {{ legend: {{ position: 'top' }} }}, scales: {{ y: {{ beginAtZero: true }} }} }}
}});

// 6. Products Horizontal Bar
new Chart(document.getElementById('c6'), {{
  type: 'bar',
  data: {{
    labels: {prod_labels},
    datasets: [{{ label: 'Harakat (dona)', data: {prod_vals}, backgroundColor: 'rgba(14,165,233,0.7)', borderRadius: 6, borderSkipped: false }}]
  }},
  options: {{ indexAxis: 'y', responsive: true, plugins: {{ legend: {{ display: false }} }}, scales: {{ x: {{ beginAtZero: true }} }} }}
}});

// 7. Leads Area
new Chart(document.getElementById('c7'), {{
  type: 'line',
  data: {{
    labels: M,
    datasets: [{{ label: 'Lidlar soni', data: {lead_vals}, borderColor: '#f59e0b', backgroundColor: 'rgba(245,158,11,0.15)', fill: true, tension: 0.4, borderWidth: 2, pointRadius: 4, pointBackgroundColor: '#f59e0b' }}]
  }},
  options: {{ responsive: true, plugins: {{ legend: {{ position: 'top' }} }}, scales: {{ y: {{ beginAtZero: true }} }} }}
}});

// 8. Leads Stages Pie
new Chart(document.getElementById('c8'), {{
  type: 'pie',
  data: {{
    labels: {stg_labels},
    datasets: [{{ data: {stg_vals}, backgroundColor: ['#6366f1','#8b5cf6','#22c55e','#eab308','#ef4444','#ec4899','#06b6d4','#f97316'], borderWidth: 0 }}]
  }},
  options: {{ responsive: true, plugins: {{ legend: {{ position: 'right' }} }} }}
}});

// 9. Lead Revenue Line
new Chart(document.getElementById('c9'), {{
  type: 'line',
  data: {{
    labels: M,
    datasets: [{{ label: 'Rejalashtirilgan (mln)', data: {lead_rev}, borderColor: '#a855f7', backgroundColor: 'rgba(168,85,247,0.15)', fill: true, tension: 0.4, borderWidth: 2, pointRadius: 4, pointBackgroundColor: '#a855f7' }}]
  }},
  options: {{ responsive: true, plugins: {{ legend: {{ position: 'top' }} }}, scales: {{ y: {{ beginAtZero: true }} }} }}
}});
</script>
</body>
</html>"""


if __name__ == "__main__":
    print("Collecting data from Odoo...")
    data = collect()
    print(f"  Invoices: {data['invoice_count']}, Orders: {data['order_count']}, "
          f"Moves: {data['move_count']}, Leads: {data['lead_count']}")

    fname = f"dashboard_{uuid.uuid4().hex[:8]}.html"
    fpath = os.path.join(OUT_DIR, fname)
    latest = os.path.join(OUT_DIR, "dashboard.html")

    html = build_html(data)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(html)
    with open(latest, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  Written: {fname}")
    print(f"  http://192.168.89.139:8002/reports/{fname}")
    print(f"  http://192.168.89.139:8002/reports/dashboard.html")
