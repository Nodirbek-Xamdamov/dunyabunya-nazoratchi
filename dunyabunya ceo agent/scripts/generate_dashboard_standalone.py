#!/usr/bin/env python3
"""
Standalone Odoo Dashboard Generator
"""

import xmlrpc.client
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
import os

# Configuration from .env
ODOO_URL = "https://hasanxojayev-odoo-prod-35427738.dev.odoo.com"
ODOO_DB = "hasanxojayev-odoo-prod-35427738"
ODOO_LOGIN = "admin@dunyabunya.com"
ODOO_API_KEY = "6de48a0cbcd4950b49e165e006fdfb25982c6ba3"
STORAGE_DIR = "./storage/reports"


def authenticate():
    """Authenticate with Odoo and return uid"""
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
    uid = common.authenticate(ODOO_DB, ODOO_LOGIN, ODOO_API_KEY, {})
    if not uid:
        raise Exception("Authentication failed")
    print(f"✅ Authenticated with uid: {uid}")
    return uid


def execute_kw(uid, model, method, args, kwargs=None):
    """Execute Odoo RPC call"""
    kwargs = kwargs or {}
    obj = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
    return obj.execute_kw(ODOO_DB, uid, ODOO_API_KEY, model, method, args, kwargs)


def fetch_statistics(uid):
    """Fetch all statistics from Odoo"""
    stats = {}
    
    print("📡 Fetching invoices...")
    try:
        stats["invoices"] = execute_kw(uid, "account.move", "search_read", [
            [["move_type", "=", "out_invoice"], ["state", "=", "posted"]],
            ["id", "invoice_date", "amount_total", "partner_id", "invoice_user_id"]
        ])
        print(f"   Found {len(stats['invoices'])} invoices")
    except Exception as e:
        print(f"   Error: {e}")
        stats["invoices"] = []
    
    print("📡 Fetching bills...")
    try:
        stats["bills"] = execute_kw(uid, "account.move", "search_read", [
            [["move_type", "=", "in_invoice"], ["state", "=", "posted"]],
            ["id", "invoice_date", "amount_total", "partner_id"]
        ])
        print(f"   Found {len(stats['bills'])} bills")
    except Exception as e:
        print(f"   Error: {e}")
        stats["bills"] = []
    
    print("📡 Fetching sale orders...")
    try:
        stats["sale_orders"] = execute_kw(uid, "sale.order", "search_read", [
            [["state", "in", ["sale", "done"]]],
            ["id", "date_order", "amount_total", "partner_id", "user_id"]
        ])
        print(f"   Found {len(stats['sale_orders'])} sale orders")
    except Exception as e:
        print(f"   Error: {e}")
        stats["sale_orders"] = []
    
    print("📡 Fetching products...")
    try:
        stats["products"] = execute_kw(uid, "product.product", "search_read", [
            [["type", "=", "product"]],
            ["id", "name", "list_price", "qty_available", "categ_id"]
        ])
        print(f"   Found {len(stats['products'])} products")
    except Exception as e:
        print(f"   Error: {e}")
        stats["products"] = []
    
    print("📡 Fetching stock moves...")
    try:
        stats["stock_moves"] = execute_kw(uid, "stock.move", "search_read", [
            [["state", "=", "done"]],
            ["id", "date", "product_id", "product_uom_qty"]
        ])
        print(f"   Found {len(stats['stock_moves'])} stock moves")
    except Exception as e:
        print(f"   Error: {e}")
        stats["stock_moves"] = []
    
    print("📡 Fetching CRM leads...")
    try:
        stats["crm_leads"] = execute_kw(uid, "crm.lead", "search_read", [
            [],
            ["id", "name", "stage_id", "planned_revenue", "create_date", "user_id"]
        ])
        print(f"   Found {len(stats['crm_leads'])} CRM leads")
    except Exception as e:
        print(f"   Error: {e}")
        stats["crm_leads"] = []
    
    print("📡 Fetching users...")
    try:
        stats["users"] = execute_kw(uid, "res.users", "search_read", [
            [["active", "=", True]],
            ["id", "name"]
        ])
        print(f"   Found {len(stats['users'])} users")
    except Exception as e:
        print(f"   Error: {e}")
        stats["users"] = []
    
    return stats


def process_statistics(stats):
    """Process raw statistics into dashboard-ready format"""
    processed = {}
    
    # Financial Overview
    invoices = stats.get("invoices", [])
    bills = stats.get("bills", [])
    
    total_revenue = sum(inv.get("amount_total", 0) for inv in invoices)
    total_expenses = sum(bill.get("amount_total", 0) for bill in bills)
    profit = total_revenue - total_expenses
    
    processed["financial"] = {
        "total_revenue": total_revenue,
        "total_expenses": total_expenses,
        "profit": profit,
        "invoice_count": len(invoices),
        "bill_count": len(bills)
    }
    
    # Monthly Revenue (last 6 months)
    monthly_revenue = {}
    for inv in invoices:
        if inv.get("invoice_date"):
            month_key = inv["invoice_date"][:7]
            monthly_revenue[month_key] = monthly_revenue.get(month_key, 0) + inv.get("amount_total", 0)
    processed["monthly_revenue"] = dict(sorted(monthly_revenue.items())[-6:])
    
    # Top Products by inventory
    products = stats.get("products", [])
    product_sales = {}
    for prod in products:
        product_name = prod.get("name", "Unknown")
        qty = prod.get("qty_available", 0)
        if qty > 0:
            product_sales[product_name] = qty
    
    top_products = dict(sorted(product_sales.items(), key=lambda x: x[1], reverse=True)[:10])
    processed["top_products"] = top_products
    
    # Sales by Manager
    sale_orders = stats.get("sale_orders", [])
    sales_by_manager = {}
    for order in sale_orders:
        manager_id = order.get("user_id", [None, "Unknown"])
        manager_name = manager_id[1] if isinstance(manager_id, list) else "Unknown"
        sales_by_manager[manager_name] = sales_by_manager.get(manager_name, 0) + order.get("amount_total", 0)
    processed["sales_by_manager"] = dict(sorted(sales_by_manager.items(), key=lambda x: x[1], reverse=True)[:10])
    
    # Daily Sales Trend (last 30 days)
    daily_sales = {}
    thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    for order in sale_orders:
        if order.get("date_order"):
            order_date = order["date_order"][:10]
            if order_date >= thirty_days_ago:
                daily_sales[order_date] = daily_sales.get(order_date, 0) + order.get("amount_total", 0)
    processed["daily_sales"] = dict(sorted(daily_sales.items())[-30:])
    
    # CRM Leads Statistics
    crm_leads = stats.get("crm_leads", [])
    leads_by_stage = {}
    for lead in crm_leads:
        stage = lead.get("stage_id", [None, "Unknown"])
        stage_name = stage[1] if isinstance(stage, list) else "Unknown"
        leads_by_stage[stage_name] = leads_by_stage.get(stage_name, 0) + 1
    processed["leads_by_stage"] = leads_by_stage
    
    leads_by_month = {}
    for lead in crm_leads:
        if lead.get("create_date"):
            month_key = lead["create_date"][:7]
            leads_by_month[month_key] = leads_by_month.get(month_key, 0) + 1
    processed["leads_by_month"] = dict(sorted(leads_by_month.items())[-6:])
    
    # Warehouse Statistics
    stock_moves = stats.get("stock_moves", [])
    moves_by_month = {}
    for move in stock_moves:
        if move.get("date"):
            month_key = move["date"][:7]
            moves_by_month[month_key] = moves_by_month.get(month_key, 0) + 1
    processed["stock_moves_by_month"] = dict(sorted(moves_by_month.items())[-6:])
    
    # Product categories
    categories = {}
    for prod in products:
        cat = prod.get("categ_id", [None, "Unknown"])
        cat_name = cat[1] if isinstance(cat, list) else "Unknown"
        categories[cat_name] = categories.get(cat_name, 0) + 1
    processed["categories"] = dict(sorted(categories.items(), key=lambda x: x[1], reverse=True)[:8])
    
    return processed


def generate_html_dashboard(processed):
    """Generate comprehensive HTML dashboard"""
    
    monthly_revenue_labels = list(processed.get("monthly_revenue", {}).keys())
    monthly_revenue_data = list(processed.get("monthly_revenue", {}).values())
    
    top_products_labels = list(processed.get("top_products", {}).keys())[:10]
    top_products_data = list(processed.get("top_products", {}).values())[:10]
    
    sales_by_manager_labels = list(processed.get("sales_by_manager", {}).keys())
    sales_by_manager_data = list(processed.get("sales_by_manager", {}).values())
    
    daily_sales_labels = list(processed.get("daily_sales", {}).keys())
    daily_sales_data = list(processed.get("daily_sales", {}).values())
    
    leads_by_stage_labels = list(processed.get("leads_by_stage", {}).keys())
    leads_by_stage_data = list(processed.get("leads_by_stage", {}).values())
    
    leads_by_month_labels = list(processed.get("leads_by_month", {}).keys())
    leads_by_month_data = list(processed.get("leads_by_month", {}).values())
    
    stock_moves_labels = list(processed.get("stock_moves_by_month", {}).keys())
    stock_moves_data = list(processed.get("stock_moves_by_month", {}).values())
    
    categories_labels = list(processed.get("categories", {}).keys())
    categories_data = list(processed.get("categories", {}).values())
    
    financial = processed.get("financial", {})
    
    # Generate top products table rows
    top_products_rows = ""
    for name, qty in list(processed.get("top_products", {}).items())[:10]:
        pct = min(100, (qty / max(processed.get("top_products", {1: 1}).values()) * 100)) if qty > 0 else 0
        top_products_rows += f"""
        <tr class="hover:bg-gray-50">
            <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">{name}</td>
            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{qty:,.0f} dona</td>
            <td class="px-6 py-4 whitespace-nowrap">
                <div class="w-full bg-gray-200 rounded-full h-2">
                    <div class="bg-blue-600 h-2 rounded-full" style="width: {pct:.0f}%"></div>
                </div>
            </td>
        </tr>
        """
    
    html = f"""<!DOCTYPE html>
<html lang="uz">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Odoo Dashboard - Dunyabunya</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script>
        tailwind.config = {{
            theme: {{
                extend: {{
                    colors: {{
                        primary: {{
                            50: '#f0f9ff',
                            100: '#e0f2fe',
                            200: '#bae6fd',
                            300: '#7dd3fc',
                            400: '#38bdf8',
                            500: '#0ea5e9',
                            600: '#0284c7',
                            700: '#0369a1',
                            800: '#075985',
                            900: '#0c4a6e',
                        }}
                    }}
                }}
            }}
        }}
    </script>
    <style>
        .gradient-bg {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        }}
        .card-hover {{
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        .card-hover:hover {{
            transform: translateY(-2px);
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1);
        }}
        .chart-container {{
            position: relative;
            height: 300px;
        }}
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(20px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        .fade-in {{
            animation: fadeIn 0.5s ease-out forwards;
        }}
    </style>
</head>
<body class="bg-gray-50 min-h-screen">
    <!-- Header -->
    <header class="gradient-bg text-white shadow-lg">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
            <div class="flex items-center justify-between">
                <div>
                    <h1 class="text-3xl font-bold">📊 Odoo Dashboard</h1>
                    <p class="mt-1 text-blue-100">Dunyabunya Kompaniyasi - Batafsil Statistika</p>
                </div>
                <div class="text-right">
                    <p class="text-sm text-blue-100">Yangilangan:</p>
                    <p class="font-semibold">{datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
                </div>
            </div>
        </div>
    </header>

    <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <!-- KPI Cards -->
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8 fade-in">
            <div class="bg-white rounded-xl shadow-md p-6 card-hover border-l-4 border-green-500">
                <div class="flex items-center">
                    <div class="p-3 rounded-full bg-green-100 text-green-600">
                        <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                    </div>
                    <div class="ml-4">
                        <p class="text-sm font-medium text-gray-500">Jami Tushum</p>
                        <p class="text-2xl font-bold text-gray-900">{financial.get('total_revenue', 0):,.0f} so'm</p>
                    </div>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6 card-hover border-l-4 border-red-500">
                <div class="flex items-center">
                    <div class="p-3 rounded-full bg-red-100 text-red-600">
                        <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 9V7a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-6a2 2 0 00-2-2H9a2 2 0 00-2 2v6a2 2 0 002 2zm7-5a2 2 0 11-4 0 2 2 0 014 0z"></path>
                        </svg>
                    </div>
                    <div class="ml-4">
                        <p class="text-sm font-medium text-gray-500">Jami Xarajatlar</p>
                        <p class="text-2xl font-bold text-gray-900">{financial.get('total_expenses', 0):,.0f} so'm</p>
                    </div>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6 card-hover border-l-4 border-blue-500">
                <div class="flex items-center">
                    <div class="p-3 rounded-full bg-blue-100 text-blue-600">
                        <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"></path>
                        </svg>
                    </div>
                    <div class="ml-4">
                        <p class="text-sm font-medium text-gray-500">Sof Foyda</p>
                        <p class="text-2xl font-bold text-gray-900">{financial.get('profit', 0):,.0f} so'm</p>
                    </div>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6 card-hover border-l-4 border-purple-500">
                <div class="flex items-center">
                    <div class="p-3 rounded-full bg-purple-100 text-purple-600">
                        <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                        </svg>
                    </div>
                    <div class="ml-4">
                        <p class="text-sm font-medium text-gray-500">Buyurtmalar Soni</p>
                        <p class="text-2xl font-bold text-gray-900">{financial.get('invoice_count', 0):,}</p>
                    </div>
                </div>
            </div>
        </div>

        <!-- Charts Row 1 -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
            <div class="bg-white rounded-xl shadow-md p-6 card-hover fade-in">
                <h3 class="text-lg font-semibold text-gray-800 mb-4">📈 Oylik Tushum Dinamikasi</h3>
                <div class="chart-container">
                    <canvas id="monthlyRevenueChart"></canvas>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6 card-hover fade-in">
                <h3 class="text-lg font-semibold text-gray-800 mb-4">🏆 Eng Ko'p Sotilgan Mahsulotlar (TOP 10)</h3>
                <div class="chart-container">
                    <canvas id="topProductsChart"></canvas>
                </div>
            </div>
        </div>

        <!-- Charts Row 2 -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
            <div class="bg-white rounded-xl shadow-md p-6 card-hover fade-in">
                <h3 class="text-lg font-semibold text-gray-800 mb-4">👥 Menejerlar Kesimida Sotuvlar</h3>
                <div class="chart-container">
                    <canvas id="salesByManagerChart"></canvas>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6 card-hover fade-in">
                <h3 class="text-lg font-semibold text-gray-800 mb-4">📅 Kunlik Sotuv Trendi (Oxirgi 30 kun)</h3>
                <div class="chart-container">
                    <canvas id="dailySalesChart"></canvas>
                </div>
            </div>
        </div>

        <!-- Charts Row 3 -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
            <div class="bg-white rounded-xl shadow-md p-6 card-hover fade-in">
                <h3 class="text-lg font-semibold text-gray-800 mb-4">🎯 CRM Lidlar Bosqichlar Bo'yicha</h3>
                <div class="chart-container">
                    <canvas id="leadsByStageChart"></canvas>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6 card-hover fade-in">
                <h3 class="text-lg font-semibold text-gray-800 mb-4">📆 Oylik Yangi Lidlar</h3>
                <div class="chart-container">
                    <canvas id="leadsByMonthChart"></canvas>
                </div>
            </div>
        </div>

        <!-- Charts Row 4 -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
            <div class="bg-white rounded-xl shadow-md p-6 card-hover fade-in">
                <h3 class="text-lg font-semibold text-gray-800 mb-4">📦 Ombor Harakatlari (Oylik)</h3>
                <div class="chart-container">
                    <canvas id="stockMovesChart"></canvas>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6 card-hover fade-in">
                <h3 class="text-lg font-semibold text-gray-800 mb-4">🏷️ Mahsulot Kategoriyalari</h3>
                <div class="chart-container">
                    <canvas id="categoriesChart"></canvas>
                </div>
            </div>
        </div>

        <!-- Top Products Table -->
        <div class="bg-white rounded-xl shadow-md p-6 mb-8 fade-in">
            <h3 class="text-lg font-semibold text-gray-800 mb-4">📋 TOP Mahsulotlar Ro'yxati</h3>
            <div class="overflow-x-auto">
                <table class="min-w-full divide-y divide-gray-200">
                    <thead class="bg-gray-50">
                        <tr>
                            <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Mahsulot Nomi</th>
                            <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Qoldiq Miqdori</th>
                            <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Sotish Darajasi</th>
                        </tr>
                    </thead>
                    <tbody class="bg-white divide-y divide-gray-200">
                        {top_products_rows}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Footer -->
        <footer class="text-center py-6 text-gray-500 text-sm">
            <p>📊 Odoo Dashboard - Dunyabunya Kompaniyasi</p>
            <p class="mt-1">AI Agent tomonidan avtomatik generatsiya qilindi</p>
        </footer>
    </main>

    <script>
        Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        Chart.defaults.color = '#6b7280';

        const colors = {{
            blue: 'rgba(59, 130, 246, 1)',
            blueLight: 'rgba(59, 130, 246, 0.2)',
            green: 'rgba(34, 197, 94, 1)',
            greenLight: 'rgba(34, 197, 94, 0.2)',
            purple: 'rgba(147, 51, 234, 1)',
            purpleLight: 'rgba(147, 51, 234, 0.2)',
            red: 'rgba(239, 68, 68, 1)',
            redLight: 'rgba(239, 68, 68, 0.2)',
            orange: 'rgba(249, 115, 22, 1)',
            orangeLight: 'rgba(249, 115, 22, 0.2)',
            pink: 'rgba(236, 72, 153, 1)',
            pinkLight: 'rgba(236, 72, 153, 0.2)'
        }};

        // 1. Monthly Revenue Chart (Line)
        new Chart(document.getElementById('monthlyRevenueChart'), {{
            type: 'line',
            data: {{
                labels: {json.dumps(monthly_revenue_labels)},
                datasets: [{{
                    label: "Tushum (so'm)",
                    data: {json.dumps(monthly_revenue_data)},
                    borderColor: colors.blue,
                    backgroundColor: colors.blueLight,
                    tension: 0.4,
                    fill: true,
                    pointBackgroundColor: colors.blue,
                    pointBorderColor: '#fff',
                    pointBorderWidth: 2,
                    pointRadius: 5
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }},
                    tooltip: {{
                        callbacks: {{
                            label: function(context) {{
                                return context.parsed.y.toLocaleString('uz-UZ') + " so'm";
                            }}
                        }}
                    }}
                }},
                scales: {{
                    y: {{
                        beginAtZero: true,
                        ticks: {{
                            callback: function(value) {{
                                return value.toLocaleString('uz-UZ');
                            }}
                        }}
                    }}
                }}
            }}
        }});

        // 2. Top Products Chart (Bar - Horizontal)
        new Chart(document.getElementById('topProductsChart'), {{
            type: 'bar',
            data: {{
                labels: {json.dumps(top_products_labels)},
                datasets: [{{
                    label: 'Qoldiq (dona)',
                    data: {json.dumps(top_products_data)},
                    backgroundColor: [
                        'rgba(59, 130, 246, 0.8)',
                        'rgba(34, 197, 94, 0.8)',
                        'rgba(147, 51, 234, 0.8)',
                        'rgba(249, 115, 22, 0.8)',
                        'rgba(236, 72, 153, 0.8)',
                        'rgba(20, 184, 166, 0.8)',
                        'rgba(245, 158, 11, 0.8)',
                        'rgba(99, 102, 241, 0.8)',
                        'rgba(239, 68, 68, 0.8)',
                        'rgba(168, 85, 247, 0.8)'
                    ],
                    borderRadius: 6
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: {{
                    legend: {{ display: false }},
                    tooltip: {{
                        callbacks: {{
                            label: function(context) {{
                                return context.parsed.x.toLocaleString('uz-UZ') + ' dona';
                            }}
                        }}
                    }}
                }},
                scales: {{
                    x: {{
                        beginAtZero: true,
                        ticks: {{
                            callback: function(value) {{
                                return value.toLocaleString('uz-UZ');
                            }}
                        }}
                    }}
                }}
            }}
        }});

        // 3. Sales by Manager Chart (Doughnut)
        new Chart(document.getElementById('salesByManagerChart'), {{
            type: 'doughnut',
            data: {{
                labels: {json.dumps(sales_by_manager_labels)},
                datasets: [{{
                    data: {json.dumps(sales_by_manager_data)},
                    backgroundColor: [
                        'rgba(59, 130, 246, 0.8)',
                        'rgba(34, 197, 94, 0.8)',
                        'rgba(147, 51, 234, 0.8)',
                        'rgba(249, 115, 22, 0.8)',
                        'rgba(236, 72, 153, 0.8)',
                        'rgba(20, 184, 166, 0.8)'
                    ],
                    borderWidth: 2,
                    borderColor: '#ffffff'
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        position: 'right',
                        labels: {{
                            padding: 15,
                            usePointStyle: true
                        }}
                    }},
                    tooltip: {{
                        callbacks: {{
                            label: function(context) {{
                                return context.label + ': ' + context.parsed.toLocaleString('uz-UZ') + " so'm";
                            }}
                        }}
                    }}
                }}
            }}
        }});

        // 4. Daily Sales Trend Chart (Area)
        new Chart(document.getElementById('dailySalesChart'), {{
            type: 'line',
            data: {{
                labels: {json.dumps(daily_sales_labels)},
                datasets: [{{
                    label: 'Kunlik Sotuv',
                    data: {json.dumps(daily_sales_data)},
                    borderColor: colors.green,
                    backgroundColor: colors.greenLight,
                    tension: 0.4,
                    fill: true,
                    pointRadius: 3,
                    pointHoverRadius: 6
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }},
                    tooltip: {{
                        callbacks: {{
                            label: function(context) {{
                                return context.parsed.y.toLocaleString('uz-UZ') + " so'm";
                            }}
                        }}
                    }}
                }},
                scales: {{
                    y: {{
                        beginAtZero: true,
                        ticks: {{
                            callback: function(value) {{
                                return value.toLocaleString('uz-UZ');
                            }}
                        }}
                    }}
                }}
            }}
        }});

        // 5. Leads by Stage Chart (Pie)
        new Chart(document.getElementById('leadsByStageChart'), {{
            type: 'pie',
            data: {{
                labels: {json.dumps(leads_by_stage_labels)},
                datasets: [{{
                    data: {json.dumps(leads_by_stage_data)},
                    backgroundColor: [
                        'rgba(59, 130, 246, 0.8)',
                        'rgba(34, 197, 94, 0.8)',
                        'rgba(147, 51, 234, 0.8)',
                        'rgba(249, 115, 22, 0.8)',
                        'rgba(236, 72, 153, 0.8)',
                        'rgba(20, 184, 166, 0.8)'
                    ],
                    borderWidth: 2,
                    borderColor: '#ffffff'
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        position: 'right',
                        labels: {{
                            padding: 15,
                            usePointStyle: true
                        }}
                    }}
                }}
            }}
        }});

        // 6. Leads by Month Chart (Bar)
        new Chart(document.getElementById('leadsByMonthChart'), {{
            type: 'bar',
            data: {{
                labels: {json.dumps(leads_by_month_labels)},
                datasets: [{{
                    label: 'Yangi Lidlar',
                    data: {json.dumps(leads_by_month_data)},
                    backgroundColor: colors.purpleLight,
                    borderColor: colors.purple,
                    borderWidth: 2,
                    borderRadius: 6
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }}
                }},
                scales: {{
                    y: {{
                        beginAtZero: true
                    }}
                }}
            }}
        }});

        // 7. Stock Moves Chart (Line)
        new Chart(document.getElementById('stockMovesChart'), {{
            type: 'line',
            data: {{
                labels: {json.dumps(stock_moves_labels)},
                datasets: [{{
                    label: 'Ombor Harakatlari',
                    data: {json.dumps(stock_moves_data)},
                    borderColor: colors.orange,
                    backgroundColor: colors.orangeLight,
                    tension: 0.4,
                    fill: true,
                    pointBackgroundColor: colors.orange,
                    pointBorderColor: '#fff',
                    pointBorderWidth: 2,
                    pointRadius: 5
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }}
                }},
                scales: {{
                    y: {{
                        beginAtZero: true
                    }}
                }}
            }}
        }});

        // 8. Categories Chart (Doughnut)
        new Chart(document.getElementById('categoriesChart'), {{
            type: 'doughnut',
            data: {{
                labels: {json.dumps(categories_labels)},
                datasets: [{{
                    data: {json.dumps(categories_data)},
                    backgroundColor: [
                        'rgba(59, 130, 246, 0.8)',
                        'rgba(34, 197, 94, 0.8)',
                        'rgba(147, 51, 234, 0.8)',
                        'rgba(249, 115, 22, 0.8)',
                        'rgba(236, 72, 153, 0.8)',
                        'rgba(20, 184, 166, 0.8)',
                        'rgba(245, 158, 11, 0.8)',
                        'rgba(99, 102, 241, 0.8)'
                    ],
                    borderWidth: 2,
                    borderColor: '#ffffff'
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        position: 'right',
                        labels: {{
                            padding: 12,
                            usePointStyle: true
                        }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>"""
    
    return html


def main():
    """Main function to generate dashboard"""
    print("🚀 Starting Odoo Dashboard Generator...")
    
    # Create reports directory
    Path(STORAGE_DIR).mkdir(parents=True, exist_ok=True)
    
    # Authenticate
    uid = authenticate()
    
    # Fetch data
    print("📡 Fetching data from Odoo...")
    stats = fetch_statistics(uid)
    
    # Process data
    print("📊 Processing statistics...")
    processed = process_statistics(stats)
    
    # Generate HTML
    print("🎨 Generating HTML dashboard...")
    html = generate_html_dashboard(processed)
    
    # Save file
    filename = f"dashboard_{uuid.uuid4().hex[:8]}.html"
    out_path = Path(STORAGE_DIR) / filename
    out_path.write_text(html, encoding="utf-8")
    
    # Also save as main dashboard
    main_dashboard = Path(STORAGE_DIR) / "dashboard.html"
    main_dashboard.write_text(html, encoding="utf-8")
    
    print(f"✅ Dashboard generated successfully!")
    print(f"📁 File saved to: {out_path}")
    print(f"🌐 Main dashboard: {main_dashboard}")
    print(f"🔗 Dashboard URL: http://192.168.89.139:8002/reports/{filename}")


if __name__ == "__main__":
    main()
