"""Yordamchining umumiy Odoo tool'lari (app/services/agent_tools.py): saralab o'qish, guruhlab
hisoblash, ikki to'plamni solishtirish, model va maydonlarni topish.

Barcha testlar mok'langan Odoo bilan ishlaydi — tarmoqqa chiqilmaydi.
"""
from __future__ import annotations

import pytest

from app.services import agent_tools as at
from app.services import odoo_tools as ot


def F(ftype, **extra):
    return {"type": ftype, "string": extra.pop("string", ftype), "store": True, **extra}


SALE_META = {
    "sale.order": {
        "id": F("integer"), "name": F("char", string="Raqam"), "partner_id": F("many2one", relation="res.partner"),
        "date_order": F("datetime", string="Sana"), "validity_date": F("date"),
        "amount_total": F("monetary", string="Jami"), "currency_id": F("many2one", relation="res.currency"),
        "state": F("selection", string="Holat", selection=[["draft", "Qoralama"], ["sale", "Sotuv"]]),
        "note": F("html"), "access_token": F("char"), "signature": F("binary"),
        "amount_text": F("char", store=False), "message_ids": F("one2many"),
    },
    "sale.order.line": {
        "order_id": F("many2one", relation="sale.order"), "product_id": F("many2one", relation="product.product"),
        "price_subtotal": F("monetary"), "create_date": F("datetime"),
    },
}


def install_odoo(monkeypatch, *, meta=SALE_META, **handlers):
    """Odoo o'rniga: `fields_get` — meta'dan, qolgan metodlar — berilgan qiymat yoki funksiya."""
    calls = []

    async def execute_kw(model, method, args, kwargs=None):
        kwargs = kwargs or {}
        calls.append((model, method, args, kwargs))
        if method == "fields_get":
            return meta.get(model, {})
        handler = handlers.get(method)
        return handler(model, args, kwargs) if callable(handler) else handler

    monkeypatch.setattr(ot._odoo, "execute_kw", execute_kw)
    return calls


# ── Sana-vaqt: Toshkent -> UTC ──────────────────────────────────────────────

async def test_dates_on_datetime_fields_become_tashkent_day_bounds_in_utc(monkeypatch):
    install_odoo(monkeypatch)
    domain = await at.localize_domain("sale.order", [
        ["date_order", ">=", "2026-10-01"], ["date_order", "<=", "2026-10-06"],
        ["date_order", "<", "2026-10-07"], ["date_order", ">", "2026-10-06"],
    ])
    # Toshkent UTC+5: 1-oktabr 00:00 = 30-sentabr 19:00 UTC — tungi buyurtmalar ham oktabrga kiradi
    assert domain == [
        ["date_order", ">=", "2026-09-30 19:00:00"], ["date_order", "<=", "2026-10-06 18:59:59"],
        ["date_order", "<", "2026-10-06 19:00:00"], ["date_order", ">", "2026-10-06 18:59:59"],
    ]


async def test_equality_on_a_day_expands_to_the_whole_day_and_other_leaves_stay(monkeypatch):
    install_odoo(monkeypatch)
    domain = await at.localize_domain("sale.order.line", [
        "|", ["create_date", "=", "2026-10-03"], ["order_id.date_order", ">=", "2026-10-01 09:30"],
        ["order_id.validity_date", ">=", "2026-10-01"], ["order_id.state", "=", "sale"],
        ["product_id", "in", [1, 2]],
    ])
    assert domain == [
        "|", "&", ["create_date", ">=", "2026-10-02 19:00:00"], ["create_date", "<=", "2026-10-03 18:59:59"],
        ["order_id.date_order", ">=", "2026-10-01 04:30:00"],   # bog'lanish orqali ham datetime deb taniladi
        ["order_id.validity_date", ">=", "2026-10-01"],         # oddiy sana maydoni o'zgarmaydi
        ["order_id.state", "=", "sale"], ["product_id", "in", [1, 2]],
    ]


async def test_domain_must_be_a_list(monkeypatch):
    install_odoo(monkeypatch)
    with pytest.raises(at.ToolInputError):
        await at.localize_domain("sale.order", "state = sale")
    assert await at.localize_domain("sale.order", None) == []


# ── odoo_search_read ────────────────────────────────────────────────────────

async def test_search_read_passes_order_and_presents_rows_in_local_time(monkeypatch):
    rows = [{"id": 7, "name": "S1", "date_order": "2026-10-04 11:26:39", "note": "<p>Salom <b>dunyo</b></p>",
             "partner_id": [5, "Ali"], "signature": "AAAA"}]
    calls = install_odoo(monkeypatch, search_read=rows)
    result = await at.odoo_search_read("sale.order", [["state", "=", "sale"]],
                                       ["name", "date_order", "note", "partner_id", "signature", "access_token"],
                                       limit=5, order="amount_total desc, id desc")

    assert result == [{"id": 7, "name": "S1", "date_order": "2026-10-04 16:26", "note": "Salom dunyo",
                       "partner_id": [5, "Ali"], "signature": "<fayl>"}]
    kwargs = next(kw for _m, method, _a, kw in calls if method == "search_read")
    assert kwargs["order"] == "amount_total desc, id desc" and kwargs["limit"] == 5
    assert "access_token" not in kwargs["fields"]  # kalit/token maydonlari Odoo'dan so'ralmaydi ham


@pytest.mark.parametrize("order", ["amount_total; drop table", "name desc --", "(select 1)"])
async def test_search_read_rejects_a_malformed_order(monkeypatch, order):
    install_odoo(monkeypatch, search_read=[])
    with pytest.raises(at.ToolInputError):
        await at.odoo_search_read("sale.order", [], ["name"], order=order)


async def test_search_read_names_unknown_fields_instead_of_asking_odoo(monkeypatch):
    calls = install_odoo(monkeypatch, search_read=[])
    with pytest.raises(at.ToolInputError, match="total_sum"):
        await at.odoo_search_read("sale.order", [], ["name", "total_sum"])
    assert [method for _m, method, _a, _k in calls] == ["fields_get"]


async def test_unknown_or_blocked_models_are_refused_with_a_hint(monkeypatch):
    calls = install_odoo(monkeypatch, search_read=[{"id": 1}])
    with pytest.raises(at.ToolInputError, match="odoo_models"):
        await at.odoo_search_read("hr.expense", [], ["name"])     # modul o'rnatilmagan
    with pytest.raises(at.ToolInputError, match="kalit"):
        await at.odoo_search_read("ir.config_parameter", [], ["key", "value"])
    with pytest.raises(at.ToolInputError):
        await at.odoo_count("res.users; --", [])
    assert all(method == "fields_get" for _m, method, _a, _k in calls)  # hech narsa o'qilmadi


async def test_search_read_at_limit_adds_the_real_total(monkeypatch):
    install_odoo(monkeypatch, search_read=lambda m, a, kw: [{"id": i} for i in range(kw["limit"])], search_count=74917)
    result = await at.odoo_search_read("sale.order", [], ["name"], limit=3)
    assert result["korsatilgan"] == 3 and result["jami"] == 74917 and "74917" in result["ogohlantirish"]


# ── odoo_read_group ─────────────────────────────────────────────────────────

def _groups(model, args, kwargs):
    assert kwargs["lazy"] is False and kwargs["context"]["tz"] == "Asia/Tashkent"
    return [
        {"partner_id": [1, "Kichik"], "__count": 2, "agg0": 100.0, "agg1": 60.0},
        {"partner_id": [2, "Katta"], "__count": 5, "agg0": 900.0, "agg1": 400.0},
        {"partner_id": False, "__count": 1, "agg0": 50.0, "agg1": 50.0},
    ]


async def test_read_group_sorts_all_groups_then_limits_and_totals_everything(monkeypatch):
    calls = install_odoo(monkeypatch, read_group=_groups)
    result = await at.odoo_read_group("sale.order", [], ["partner_id"],
                                      ["amount_total:sum", "amount_total:max", "partner_id", "name"], limit=2)

    sent = next(a for _m, method, a, _k in calls if method == "read_group")
    # Bir maydonga ikki amal — kalitlar to'qnashmasligi uchun taxallus bilan yuboriladi;
    # guruh maydoni va jamlab bo'lmaydigan matn maydoni tushirib qoldiriladi
    assert sent[1] == ["agg0:sum(amount_total)", "agg1:max(amount_total)"]
    assert [g["partner_id"] for g in result["guruhlar"]] == [[2, "Katta"], [1, "Kichik"]]
    assert result["guruhlar"][0] == {"partner_id": [2, "Katta"], "soni": 5,
                                     "amount_total:sum": 900.0, "amount_total:max": 400.0}
    assert result["jami_guruhlar"] == 3 and result["jami"] == {"soni": 8, "amount_total:sum": 1050.0}
    assert "3 ta guruhdan" in result["ogohlantirish"]


async def test_read_group_order_by_count_ascending_and_bad_key(monkeypatch):
    install_odoo(monkeypatch, read_group=_groups)
    result = await at.odoo_read_group("sale.order", [], ["partner_id"], ["amount_total:sum", "amount_total:max"],
                                      order_by="soni asc")
    assert [g["soni"] for g in result["guruhlar"]] == [1, 2, 5]
    with pytest.raises(at.ToolInputError, match="order_by"):
        await at.odoo_read_group("sale.order", [], ["partner_id"], ["amount_total:sum"], order_by="amount desc")
    with pytest.raises(at.ToolInputError, match="yo'q"):
        await at.odoo_read_group("sale.order", [], ["partner_id"], ["margin:sum"])


async def test_read_group_by_month_is_chronological_with_iso_labels(monkeypatch):
    def by_month(model, args, kwargs):
        return [
            {"date_order:month": "oktabr 2026", "__count": 3, "agg0": 30.0,
             "__range": {"date_order:month": {"from": "2026-09-30 19:00:00", "to": "2026-10-31 19:00:00"}}},
            {"date_order:month": "sentabr 2026", "__count": 9, "agg0": 90.0,
             "__range": {"date_order:month": {"from": "2026-08-31 19:00:00", "to": "2026-09-30 19:00:00"}}},
        ]

    install_odoo(monkeypatch, read_group=by_month)
    result = await at.odoo_read_group("sale.order", [["date_order", ">=", "2026-09-01"]],
                                      ["date_order:month"], ["amount_total:sum"])
    assert [g["date_order:month"] for g in result["guruhlar"]] == ["2026-09", "2026-10"]


async def test_read_group_without_grouping_returns_only_the_overall_total(monkeypatch):
    install_odoo(monkeypatch, read_group=[{"__count": 7691, "agg0": 383700000.129}])
    result = await at.odoo_read_group("sale.order", [], [], ["amount_total:sum"])
    assert result == {"jami": {"soni": 7691, "amount_total:sum": 383700000.13}, "guruhlar": [], "jami_guruhlar": 0}


# ── odoo_compare ────────────────────────────────────────────────────────────

def _two_periods(model, args, kwargs):
    domain = args[0]
    if ["state", "=", "a"] in domain:
        return [{"partner_id": [1, "Ketgan"], "__count": 4, "agg0": 500.0},
                {"partner_id": [2, "Doimiy"], "__count": 2, "agg0": 200.0}]
    return [{"partner_id": [2, "Doimiy"], "__count": 3, "agg0": 150.0},
            {"partner_id": [3, "Yangi"], "__count": 1, "agg0": 80.0}]


async def test_compare_finds_lost_new_and_changed_groups_in_one_call(monkeypatch):
    install_odoo(monkeypatch, read_group=_two_periods)
    args = ("sale.order", "partner_id", "amount_total:sum", [["state", "=", "a"]], [["state", "=", "b"]])

    lost = await at.odoo_compare(*args, show="lost")
    assert [r["guruh"] for r in lost["qatorlar"]] == [[1, "Ketgan"]]
    assert lost["a"]["jami"] == 700.0 and lost["b"]["jami"] == 230.0 and lost["farq_foiz"] == -67.1
    assert lost["faqat_a_da_bor"] == 1 and lost["faqat_b_da_bor"] == 1

    new = await at.odoo_compare(*args, show="new")
    assert new["qatorlar"] == [{"guruh": [3, "Yangi"], "a": 0, "b": 80.0, "farq": 80.0}]

    changes = await at.odoo_compare(*args, label_a="Avgust", label_b="Sentabr")
    assert [r["guruh"][1] for r in changes["qatorlar"]] == ["Ketgan", "Yangi", "Doimiy"]  # eng katta farq oldinda
    assert changes["qatorlar"][2]["farq_foiz"] == -25.0 and changes["a"]["nomi"] == "Avgust"


async def test_compare_by_record_count(monkeypatch):
    install_odoo(monkeypatch, read_group=_two_periods)
    result = await at.odoo_compare("sale.order", "partner_id", "soni", [["state", "=", "a"]], [["state", "=", "b"]],
                                   show="top_b")
    assert result["qatorlar"][0] == {"guruh": [2, "Doimiy"], "a": 2, "b": 3, "farq": 1, "farq_foiz": 50.0}


# ── odoo_models / odoo_fields ───────────────────────────────────────────────

async def test_models_search_escapes_like_wildcards_and_drops_unreadable_models(monkeypatch):
    def search(model, args, kwargs):
        return [{"model": "x_kassa_chiqim", "name": "Kassa chiqim"}, {"model": "x_branch", "name": "Branch"},
                {"model": "ir.config_parameter", "name": "System Parameter"}, {"model": "x_abstract", "name": "A"}]

    def count(model, args, kwargs):
        if model == "x_abstract":
            raise RuntimeError("abstrakt model")
        return {"x_kassa_chiqim": 2789, "x_branch": 4}[model]

    calls = install_odoo(monkeypatch, search_read=search, search_count=count)
    result = await at.odoo_models("x_")

    assert result == [{"model": "x_kassa_chiqim", "nomi": "Kassa chiqim", "yozuvlar": 2789},
                      {"model": "x_branch", "nomi": "Branch", "yozuvlar": 4}]
    domain = next(a[0] for _m, method, a, _k in calls if method == "search_read")
    # "_" LIKE'da "istalgan belgi" — ekranlanmasa "x_" har qanday "x?" ga mos kelardi
    assert domain == [["transient", "=", False], ["model", "=like", "x\\_%"]]


async def test_models_search_matches_any_keyword_in_name_or_technical_name(monkeypatch):
    calls = install_odoo(monkeypatch, search_read=[], search_count=0)
    await at.odoo_models("purchase leave")
    domain = next(a[0] for _m, method, a, _k in calls if method == "search_read")
    assert domain == [["transient", "=", False], "|", "|", "|",
                      ["name", "=ilike", "%purchase%"], ["model", "=ilike", "%purchase%"],
                      ["name", "=ilike", "%leave%"], ["model", "=ilike", "%leave%"]]


async def test_fields_listing_hides_secrets_and_technical_fields(monkeypatch):
    install_odoo(monkeypatch)
    listing = await at.odoo_fields("sale.order")
    text = "\n".join(listing["maydonlar"])

    assert "state — Holat (selection) [qiymatlar: draft=Qoralama, sale=Sotuv]" in text
    assert "partner_id — many2one (many2one -> res.partner)" in text
    assert "amount_text — char (char) (saqlanmaydi)" in text
    assert "access_token" not in text and "signature" not in text and "message_ids" not in text

    narrowed = await at.odoo_fields("sale.order", "sana")
    assert narrowed["maydonlar"] == ["date_order — Sana (datetime)"]
    assert "topilmadi" in (await at.odoo_fields("sale.order", "zzz"))["ogohlantirish"]
