#!/usr/bin/env python3
"""
Chart.js-only Dashboard — no HTML tables allowed.
"""

import asyncio
import uuid
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.odoo import OdooClient
from app.config import settings

REPORTS_DIR = Path(settings.STORAGE_DIR) / "reports"


async def fetch_data():
    odoo = OdooClient()

    # Sale orders by month
    sale_orders = await odoo.execute_kw(
        "sale.order", "read_group",
        [[["state", "in", ["sale", "done"]]],
         ["amount_total", "user_id"],
         ["date_order:month"]],
    )

    # Sale orders by manager
    manager_sales = await odoo.execute_kw(
        "sale.order", "read_group",
        [[["state", "in", ["sale", "done"]]],
         ["amount_total", "user_id"],
         ["user_id"]],
    )

    # Weekly sales
    weekly_sales = await odoo.execute_kw(
        "sale.order", "read_group",
        [[["state", "in", ["sale", "done"]]],
         ["amount_total"],
         ["date_order:week"]],
    )

    return sale_orders, manager_sales, weekly_sales


def generate_html(sale_orders, manager_sales, weekly_sales):
    # Monthly data
    monthly_labels = []
    monthly_orders = []
    monthly_amounts = []
    for row in sale_orders:
        label = row.get("date_order:month", "Noma'lum")
        count = row.get("__count", 0)
        amount = row.get("amount_total", 0) / 1_000_000  # mln
        monthly_labels.append(label)
        monthly_orders.append(count)
        monthly_amounts.append(round(amount, 1))

    # Manager data (top 10)
    manager_data = []
    for row in manager_sales:
        uid = row.get("user_id")
        if uid and isinstance(uid, list):
            name = uid[1]
        else:
            name = "Noma'lum"
        amount = row.get("amount_total", 0) / 1_000_000
        count = row.get("__count", 0)
        manager_data.append({"name": name, "amount": round(amount, 1), "count": count})

    manager_data.sort(key=lambda x: x["amount"], reverse=True)
    top_managers = manager_data[:10]

    manager_names = [m["name"].split()[-1] if len(m["name"].split()) > 1 else m["name"][:15] for m in top_managers]
    manager_amounts = [m["amount"] for m in top_managers]
    manager_counts = [m["count"] for m in top_managers]

    # Manager share (top 5 + others)
    total_amount = sum(m["amount"] for m in manager_data) or 1
    top5 = manager_data[:5]
    others_amount = sum(m["amount"] for m in manager_data[5:])
    share_labels = [m["name"].split()[-1] if len(m["name"].split()) > 1 else m["name"][:15] for m in top5] + ["Boshqalar"]
    share_data = [round(m["amount"], 1) for m in top5] + [round(others_amount, 1)]

    # Weekly data
    weekly_labels = []
    weekly_amounts = []
    for row in weekly_sales:
        label = row.get("date_order:week", "Noma'lum")
        amount = row.get("amount_total", 0) / 1_000_000
        weekly_labels.append(label)
        weekly_amounts.append(round(amount, 1))

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Oxirgi 3 oylik sotuv statistikasi</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  .card {{ background: white; border-radius: 16px; padding: 24px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }}
  .chart-box {{ position: relative; height: 320px; }}
</style>
</head>
<body class="bg-gray-100 min-h-screen">
<div class="max-w-6xl mx-auto px-4 py-8">
  <h1 class="text-3xl font-bold text-gray-800 mb-2">📊 Oxirgi 3 oylik sotuv statistikasi</h1>
  <p class="text-gray-500 mb-8">Dunyabunya kompaniyasi — {now}</p>

  <!-- 1. Oylik sotuv dinamikasi -->
  <div class="card mb-8">
    <h2 class="text-xl font-semibold text-gray-700 mb-4">📈 Oylik sotuv dinamikasi</h2>
    <div class="chart-box"><canvas id="monthlyChart"></canvas></div>
  </div>

  <!-- 2. Haftalik trend -->
  <div class="card mb-8">
    <h2 class="text-xl font-semibold text-gray-700 mb-4">📉 Haftalik sotuv trendi</h2>
    <div class="chart-box"><canvas id="weeklyChart"></canvas></div>
  </div>

  <!-- 3. TOP-10 menejer (bar) -->
  <div class="card mb-8">
    <h2 class="text-xl font-semibold text-gray-700 mb-4">🏆 TOP-10 menejer (sotuv hajmi, mln so'm)</h2>
    <div class="chart-box"><canvas id="managerBarChart"></canvas></div>
  </div>

  <!-- 4. Menejerlar ulushi (doughnut) -->
  <div class="card mb-8">
    <h2 class="text-xl font-semibold text-gray-700 mb-4">🍩 Menejerlar ulushi (% hajmdan)</h2>
    <div class="chart-box"><canvas id="shareChart"></canvas></div>
  </div>

  <!-- 5. Buyurtmalar soni (bar) -->
  <div class="card mb-8">
    <h2 class="text-xl font-semibold text-gray-700 mb-4">📦 Oylik buyurtmalar soni</h2>
    <div class="chart-box"><canvas id="ordersBarChart"></canvas></div>
  </div>

  <p class="text-center text-gray-400 text-sm mt-8">dunyabunya agent tomonidan generatsiya qilindi</p>
</div>

<script>
Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
Chart.defaults.color = '#6b7280';

const colors = {{
  blue: '#3b82f6', green: '#10b981', purple: '#8b5cf6',
  orange: '#f59e0b', red: '#ef4444', pink: '#ec4899',
  teal: '#14b8a6', indigo: '#6366f1'
}};

// 1. Monthly
new Chart(document.getElementById('monthlyChart'), {{
  type: 'bar',
  data: {{
    labels: {monthly_labels},
    datasets: [{{
      label: "Sotuv (mln so'm)",
      data: {monthly_amounts},
      backgroundColor: [colors.blue, colors.purple, colors.teal],
      borderRadius: 8,
      yAxisID: 'y'
    }}, {{
      label: "Buyurtmalar soni",
      data: {monthly_orders},
      type: 'line',
      borderColor: colors.orange,
      backgroundColor: 'transparent',
      tension: 0.4,
      pointRadius: 6,
      pointBackgroundColor: colors.orange,
      yAxisID: 'y1'
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    scales: {{
      y: {{ beginAtZero: true, position: 'left', title: {{ display: true, text: "mln so'm" }} }},
      y1: {{ beginAtZero: true, position: 'right', grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: "buyurtmalar" }} }}
    }},
    plugins: {{ tooltip: {{ callbacks: {{ label: ctx => ctx.dataset.label + ': ' + ctx.parsed.y.toLocaleString() }} }} }}
  }}
}});

// 2. Weekly
new Chart(document.getElementById('weeklyChart'), {{
  type: 'line',
  data: {{
    labels: {weekly_labels},
    datasets: [{{
      label: "Haftalik sotuv (mln so'm)",
      data: {weekly_amounts},
      borderColor: colors.green,
      backgroundColor: 'rgba(16,185,129,0.1)',
      fill: true,
      tension: 0.4,
      pointRadius: 5,
      pointBackgroundColor: colors.green
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    scales: {{ y: {{ beginAtZero: true }} }},
    plugins: {{ tooltip: {{ callbacks: {{ label: ctx => ctx.parsed.y.toLocaleString() + ' mln so\'m' }} }} }}
  }}
}});

// 3. Manager Bar
new Chart(document.getElementById('managerBarChart'), {{
  type: 'bar',
  data: {{
    labels: {manager_names},
    datasets: [{{
      label: "Sotuv (mln so'm)",
      data: {manager_amounts},
      backgroundColor: [
        colors.blue, colors.purple, colors.green, colors.orange, colors.red,
        colors.teal, colors.pink, colors.indigo, '#78716c', colors.orange
      ],
      borderRadius: 6
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    indexAxis: 'y',
    scales: {{ x: {{ beginAtZero: true }} }},
    plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: ctx => ctx.parsed.x.toLocaleString() + ' mln so\'m' }} }} }}
  }}
}});

// 4. Share Doughnut
new Chart(document.getElementById('shareChart'), {{
  type: 'doughnut',
  data: {{
    labels: {share_labels},
    datasets: [{{
      data: {share_data},
      backgroundColor: [colors.blue, colors.purple, colors.green, colors.orange, colors.red, '#d1d5db'],
      borderWidth: 2,
      borderColor: '#fff'
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{
      legend: {{ position: 'right' }},
      tooltip: {{ callbacks: {{ label: ctx => ctx.label + ': ' + ctx.parsed.toLocaleString() + ' mln so\'m' }} }}
    }}
  }}
}});

// 5. Orders Bar
new Chart(document.getElementById('ordersBarChart'), {{
  type: 'bar',
  data: {{
    labels: {monthly_labels},
    datasets: [{{
      label: "Buyurtmalar soni",
      data: {monthly_orders},
      backgroundColor: [colors.indigo, colors.pink, colors.teal],
      borderRadius: 8
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    scales: {{ y: {{ beginAtZero: true }} }},
    plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: ctx => ctx.parsed.y.toLocaleString() + ' ta' }} }} }}
  }}
}});
</script>
</body>
</html>"""
    return html


async def main():
    print("Fetching data from Odoo...")
    sale_orders, manager_sales, weekly_sales = await fetch_data()

    print("Generating Chart.js dashboard...")
    html = generate_html(sale_orders, manager_sales, weekly_sales)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # Save as dashboard.html (main)
    out = REPORTS_DIR / "dashboard.html"
    out.write_text(html, encoding="utf-8")
    print(f"Saved: {out}")

    # Also save as the static file
    static = REPORTS_DIR / "c68b131f7e594f60aa54d7b56363759f.html"
    static.write_text(html, encoding="utf-8")
    print(f"Saved: {static}")


if __name__ == "__main__":
    asyncio.run(main())
