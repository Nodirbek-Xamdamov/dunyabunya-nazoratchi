"""RBAC-4: Role-Based Access Control for db_multi_agent."""

from enum import Enum


class Role(str, Enum):
    CEO = "ceo"
    MOLIYA = "moliya"
    OMBOR = "ombor"
    SOTUV = "sotuv"
    HR = "hr"
    NONE = "none"  # tanib bo'lmagan/kutilmagan holat uchun xavfsiz zaxira — hech qanday tool/model ruxsati yo'q


# Role → allowed Odoo tools
ROLE_TOOLS: dict[Role, list[str]] = {
    Role.CEO: [
        # Analytics
        "odoo_search_read", "odoo_read_group", "odoo_count",
        "export_excel", "generate_html_report",
        "export_word", "export_pdf", "export_csv",
        "aggregate_data", "growth_dynamics",
        "search_employees_readonly",
        # Extra analytics
        "get_top_debtors", "get_overdue_invoices", "get_cashflow_summary",
        "get_sales_by_seller", "get_employees_by_department",
        "get_upcoming_deadlines", "get_crm_funnel", "get_profit_summary",
        # Statistics
        "generate_company_report", "generate_sales_report", "generate_invoice_report", "generate_task_report",
        # Manager stats (CEO superset bo'lishi uchun)
        "get_sales_stats", "get_invoice_stats", "get_sales_by_partner", "get_sales_by_product",
        "get_crm_stats", "get_task_stats",
        # Full access — all manager tools too
        "search_kassa_chiqim", "create_kassa_chiqim",
        "search_payments", "create_payment_draft", "create_currency_exchange",
        "get_invoice_pdf", "get_payment_receipt_pdf",
        "find_product", "check_stock_quant", "check_low_stock",
        "create_internal_transfer", "get_delivery_slip_pdf",
        "find_partner", "search_crm_leads", "create_crm_lead", "find_stale_leads",
        "create_sale_order_draft", "check_partner_debt", "get_sale_order_pdf",
    ],
    Role.MOLIYA: [
        "odoo_search_read", "odoo_read_group", "odoo_count",
        "search_kassa_chiqim", "create_kassa_chiqim",
        "search_payments", "create_payment_draft", "create_currency_exchange",
        "get_invoice_pdf", "get_payment_receipt_pdf",
        "get_sales_stats", "get_invoice_stats",
        "export_excel", "export_word", "export_pdf", "export_csv",
        "generate_html_report",
    ],
    Role.OMBOR: [
        "odoo_search_read", "odoo_read_group", "odoo_count",
        "find_product", "check_stock_quant", "check_low_stock",
        "create_internal_transfer", "get_delivery_slip_pdf",
        "get_task_stats",
        "export_excel", "export_word", "export_pdf", "export_csv",
        "generate_html_report",
    ],
    Role.SOTUV: [
        "odoo_search_read", "odoo_read_group", "odoo_count",
        "find_partner", "find_product",
        "search_crm_leads", "create_crm_lead", "find_stale_leads",
        "create_sale_order_draft", "check_partner_debt", "get_sale_order_pdf",
        "get_sales_stats", "get_sales_by_partner", "get_sales_by_product",
        "get_crm_stats",
        "export_excel", "export_word", "export_pdf", "export_csv",
        "generate_html_report",
    ],
    Role.HR: [
        "odoo_search_read", "odoo_read_group", "odoo_count",
        "search_employees_readonly", "get_employees_by_department",
        "export_excel", "export_word", "export_pdf", "export_csv",
        "generate_html_report",
    ],
    Role.NONE: [],
}

# Role → qaysi Odoo modellarga odoo_search_read/odoo_read_group/odoo_count
# orqali murojaat qilish mumkin (P0-3: bu tool'lar BASE_TOOLS sifatida BARCHA
# rollarga beriladi va model nomini erkin qabul qiladi — shu cheklov bo'lmasa,
# masalan Sotuv menejeri "model: account.move" deb so'rab Moliya
# ma'lumotlarini o'qib olishi mumkin edi, garchi role prompt buni "taqiqlagan"
# bo'lsa ham — prompt LLM uchun ko'rsatma, texnik cheklov emas).
# None = cheklovsiz (faqat CEO).
ROLE_ALLOWED_MODELS: dict[Role, set[str] | None] = {
    Role.CEO: None,
    Role.MOLIYA: {"account.move", "account.move.line", "account.payment", "res.partner"},
    Role.OMBOR: {"product.product", "stock.quant", "stock.picking", "stock.move"},
    Role.SOTUV: {"sale.order", "sale.order.line", "res.partner", "crm.lead", "product.product"},
    Role.HR: {"hr.employee", "hr.department"},
    Role.NONE: set(),
}

# Role → system prompt addition
ROLE_PROMPTS: dict[Role, str] = {
    Role.CEO: """Siz CEO yordamchisiz. Barcha bo'limlarga to'liq kirish huquqingiz bor.
Sotuv, ombor, moliya va analitika bo'limlaridagi barcha ma'lumotlarni ko'ra olasiz.
Dashboard, Excel hisobotlar, KPI va biznes xulosalar tayyorlay olasiz.""",

    Role.MOLIYA: """Siz Moliya menejeri yordamchisiz.
Faqat moliyaviy operatsiyalar: kassa chiqimlari, to'lovlar, valyuta ayirboshlash, schyot-fakturalar.
Boshqa bo'limlarga kirish huquqingiz yo'q.""",

    Role.OMBOR: """Siz Ombor menejeri yordamchisiz.
Faqat ombor operatsiyalari: mahsulotlar, qoldiqlar, ichki ko'chirishlar, nakladnoy.
Boshqa bo'limlarga kirish huquqingiz yo'q.""",

    Role.SOTUV: """Siz Sotuv menejeri yordamchisiz.
Faqat sotuv operatsiyalari: mijozlar, CRM lidlar, sotuv buyurtmalari, qarzdorlik.
Boshqa bo'limlarga kirish huquqingiz yo'q.""",

    Role.HR: """Siz HR menejeri yordamchisiz.
Faqat xodimlar ma'lumotlari: xodimlar ro'yxati, bo'limlar, lavozimlar.
Moliya, sotuv, ombor kabi boshqa bo'limlarga kirish huquqingiz yo'q.""",

    Role.NONE: """Sizga hech qanday bo'lim biriktirilmagan — hech qanday ma'lumotga kirish huquqingiz yo'q.
CEO bilan bog'laning.""",
}

# Role display name
ROLE_NAMES: dict[Role, str] = {
    Role.CEO: "👑 CEO (Analitika)",
    Role.MOLIYA: "💰 Moliya menejeri",
    Role.OMBOR: "📦 Ombor menejeri",
    Role.SOTUV: "🤝 Sotuv menejeri",
    Role.HR: "🧑‍💼 HR menejeri",
    Role.NONE: "❓ Noma'lum",
}
