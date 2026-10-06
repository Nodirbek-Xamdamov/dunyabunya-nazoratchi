"""Yordamchining umumiy Odoo tool'lari — tayyor hisoboti yo'q har qanday savolga javob topish
uchun: yozuvlarni saralab o'qish, guruhlab hisoblash, ikki to'plamni solishtirish, kerakli
model va maydonlarni topish.

Sana-vaqt qoidasi: model HAMMA narsani Toshkent vaqtida ko'radi. Filtrdagi sana-vaqtlar shu
yerda UTC'ga, natijadagilar esa Toshkent vaqtiga o'giriladi (Odoo bazada UTC saqlaydi) —
"oyning birinchi kuni" degan filtr tungi soat 00:00–05:00 dagi hujjatlarni yo'qotmasligi uchun.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone

from app.services import odoo_tools as ot
from app.services.tool_errors import ToolInputError

MAX_READ_LIMIT = 500
_MAX_TEXT_CHARS = 400  # bitta uzun matn maydoni (tavsif, izoh) shundan oshsa qisqartiriladi


# Parol/kalit saqlaydigan modellar — CEO roli uchun ham o'qilmaydi: ma'lumot ichiga yashirilgan
# ko'rsatma (prompt injection) modelni shularni chatga chiqarishga undashi mumkin.
BLOCKED_MODELS = frozenset({
    "ir.config_parameter", "res.users.apikeys", "res.users.apikeys.description",
    "res.users.apikeys.show", "auth.totp.device", "ir.mail_server", "fetchmail.server",
    "payment.provider", "payment.token", "iap.account", "auth.oauth.provider",
    "res.users.identitycheck", "change.password.user", "change.password.own",
})
_SENSITIVE_FIELD_RE = re.compile(r"passw|api_?key|secret|token|totp|oauth_access|signup_", re.IGNORECASE)
_MODEL_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$")
_AGG_RE = re.compile(r"^([a-z0-9_]+)(?::([a-z_]+))?$")
_GRANULARITIES = ("day", "week", "month", "quarter", "year")


def check_model(model) -> str:
    if not isinstance(model, str) or not _MODEL_RE.match(model):
        raise ToolInputError("model nomi noto'g'ri — texnik nom kerak (masalan sale.order)")
    if model in BLOCKED_MODELS:
        raise ToolInputError(f"'{model}' modeli tizim sozlamalari va kalitlarni saqlaydi — yordamchi uni o'qimaydi")
    return model


async def _meta(model: str) -> dict:
    meta = await ot._fields_meta(model)
    if not meta:
        raise ToolInputError(
            f"'{model}' modeli bu Odoo bazasida yo'q yoki uni o'qib bo'lmadi. Mos modelni "
            "odoo_models orqali qidiring (modul o'rnatilmagan bo'lishi ham mumkin)."
        )
    return meta


# ─── Sana-vaqt: Toshkent <-> UTC ────────────────────────────────────────────

async def field_type(model: str, path: str) -> str | None:
    """Maydon turi; "order_id.date_order" kabi zanjirlarni ham bog'lanishlar bo'ylab kuzatadi."""
    meta = await ot._fields_meta(model)
    parts = path.split(".")
    for index, part in enumerate(parts):
        info = meta.get(part)
        if not info:
            return None
        if index == len(parts) - 1:
            return info.get("type")
        relation = info.get("relation")
        if not relation:
            return None
        meta = await ot._fields_meta(relation)
    return None


def _to_utc(local: datetime) -> str:
    return local.replace(tzinfo=ot._TZ).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


async def localize_domain(model: str, domain) -> list:
    """Filtrdagi sana-vaqt qiymatlarini Toshkent vaqtidan UTC'ga o'giradi (faqat datetime
    maydonlar; oddiy sana maydonlari o'zgarmaydi). Faqat sana berilgan bo'lsa ("2026-10-01")
    u Toshkent kunining boshi yoki oxiri deb olinadi — amalga qarab."""
    if domain in (None, False, ""):
        return []
    if not isinstance(domain, (list, tuple)):
        raise ToolInputError("domain ro'yxat bo'lishi kerak, masalan [[\"state\",\"=\",\"sale\"]]")
    out: list = []
    for leaf in domain:
        if not (isinstance(leaf, (list, tuple)) and len(leaf) == 3 and isinstance(leaf[0], str)):
            out.append(leaf)
            continue
        field, op, value = leaf
        if not isinstance(value, str) or not (_DATE_RE.match(value) or _DATETIME_RE.match(value)):
            out.append(list(leaf))
            continue
        if await field_type(model, field) != "datetime":
            out.append(list(leaf))
            continue
        if _DATETIME_RE.match(value):
            out.append([field, op, _to_utc(datetime.fromisoformat(value.replace("T", " ")))])
            continue
        day = datetime.strptime(value, "%Y-%m-%d")
        start, end = _to_utc(day), _to_utc(day + timedelta(days=1) - timedelta(seconds=1))
        if op in (">=", "<"):
            out.append([field, op, start])
        elif op in ("<=", ">"):
            out.append([field, op, end])
        elif op in ("=", "=="):
            out.extend(["&", [field, ">=", start], [field, "<=", end]])
        elif op in ("!=", "<>"):
            out.extend(["|", [field, "<", start], [field, ">", end]])
        else:
            out.append(list(leaf))
    return out


def _present_rows(rows: list[dict], meta: dict) -> None:
    """Qatorlarni model o'qishi uchun tayyorlaydi: sana-vaqt Toshkent vaqtida, HTML matnga
    aylantirilgan, uzun matn qisqartirilgan, fayl (binary) maydonlar olib tashlangan."""
    for row in rows:
        for field in list(row):
            info = meta.get(field) or {}
            ftype, value = info.get("type"), row[field]
            if ftype == "binary":
                row[field] = "<fayl>" if value else False
            elif not isinstance(value, str):
                continue
            elif ftype == "datetime":
                row[field] = ot._local_datetime(value)
            elif ftype == "html":
                text = ot._strip_tags(value)
                row[field] = text[:_MAX_TEXT_CHARS] + ("…" if len(text) > _MAX_TEXT_CHARS else "")
            elif ftype in ("text", "char") and len(value) > _MAX_TEXT_CHARS:
                row[field] = value[:_MAX_TEXT_CHARS] + "…"


def _safe_fields(fields, meta: dict) -> list[str]:
    if isinstance(fields, str):
        fields = [fields]
    wanted = [f for f in (fields or []) if isinstance(f, str) and f]
    unknown = [f for f in wanted if f not in meta and f != "id"]
    if unknown:
        raise ToolInputError(
            f"Bunday maydon(lar) yo'q: {', '.join(unknown[:8])}. To'g'ri nomlarni odoo_fields orqali ko'ring."
        )
    safe = [f for f in wanted if not _SENSITIVE_FIELD_RE.search(f)]
    return safe or ["display_name"]


# ─── O'qish, sanash, guruhlash ──────────────────────────────────────────────

async def odoo_search_read(model: str, domain: list | None = None, fields: list | None = None,
                           limit: int = 50, order: str = "", offset: int = 0):
    check_model(model)
    meta = await _meta(model)
    flds = _safe_fields(fields, meta)
    dom = await localize_domain(model, domain)
    limit = max(1, min(int(limit or 50), MAX_READ_LIMIT))
    kwargs: dict = {"fields": flds, "limit": limit, "context": ot._UZ_CTX}
    if order:
        kwargs["order"] = ot._safe_order(order)
    if offset:
        kwargs["offset"] = max(0, int(offset))
    rows = await ot._odoo.execute_kw(model, "search_read", [dom], kwargs)
    _present_rows(rows, meta)
    if len(rows) < limit:
        return rows
    # Limit to'ldi — bu JAMI son emas: jami sonni shu yerning o'zida qo'shib beramiz
    total = await ot._odoo.execute_kw(model, "search_count", [dom])
    if total <= len(rows) + (kwargs.get("offset") or 0):
        return rows
    return {
        "qatorlar": rows, "korsatilgan": len(rows), "jami": total,
        "ogohlantirish": (
            f"Bazada shartga mos {total} ta yozuv bor, shulardan {len(rows)} tasi ko'rsatildi. Yig'indi yoki "
            "reyting kerak bo'lsa odoo_read_group ishlating; qolgan qatorlar uchun offset bering."
        ),
    }


async def odoo_count(model: str, domain: list | None = None) -> int:
    check_model(model)
    return await ot._odoo.execute_kw(model, "search_count", [await localize_domain(model, domain)])


def _group_label(row: dict, spec: str):
    """Guruh qiymati: bog'lanish — [id, nom]; sana guruhi — "2026-10" / "2026-10-06" / "2026-Q4"."""
    value = row.get(spec)
    field, _, granularity = spec.partition(":")
    start = ((row.get("__range") or {}).get(spec) or {}).get("from")
    if start:
        try:
            # Guruh Toshkent vaqti bo'yicha, chegara esa UTC'da keladi ("...-30 19:00:00")
            moment = datetime.fromisoformat(str(start).replace(" ", "T")) + timedelta(hours=12)
        except ValueError:
            return value or None
        granularity = granularity or "month"
        if granularity == "day":
            return moment.strftime("%Y-%m-%d")
        if granularity == "week":
            return f"{moment.strftime('%Y-%m-%d')} haftasi"
        if granularity == "quarter":
            return f"{moment.year}-Q{(moment.month - 1) // 3 + 1}"
        if granularity == "year":
            return str(moment.year)
        return moment.strftime("%Y-%m")
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return [value[0], value[1]]
    return None if value in (False, None) else value


def _sort_value(value):
    if isinstance(value, (list, tuple)):
        value = value[1] if len(value) > 1 else None
    if value is None:
        return (1, 0, "")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (0, value, "")
    return (0, 0, str(value).casefold())


async def _grouped(model: str, domain, group_by: list[str], fields) -> tuple[list[dict], list[str]]:
    """read_group -> ixcham qatorlar [{guruh..., "soni": n, "<maydon>:<amal>": qiymat}] va
    jamlanma kalitlari. Bir maydonga ikki xil amal so'ralsa ham kalitlar to'qnashmaydi."""
    meta = await ot._fields_meta(model)
    grouped_fields = {g.partition(":")[0] for g in group_by}
    specs: list[tuple[str, str, str]] = []  # (natijadagi kalit, Odoo'ga yuboriladigan, Odoo qaytaradigan kalit)
    for index, raw in enumerate(fields or []):
        match = _AGG_RE.match(str(raw).strip())
        if not match:
            raise ToolInputError(f"fields elementi noto'g'ri: {raw!r} — masalan \"amount_total:sum\"")
        name, agg = match.group(1), match.group(2)
        if name in ("id", "__count") or name in grouped_fields:
            continue  # yozuvlar soni har doim "soni" kalitida qaytadi
        if name not in meta:
            raise ToolInputError(f"'{name}' maydoni {model} modelida yo'q — odoo_fields bilan tekshiring")
        if not agg and meta[name].get("type") not in ("integer", "float", "monetary"):
            continue  # amalsiz berilgan matn/bog'lanish maydonini jamlab bo'lmaydi
        if agg:
            alias = f"agg{index}"
            specs.append((f"{name}:{agg}", f"{alias}:{agg}({name})", alias))
        else:
            specs.append((name, name, name))
    dom = await localize_domain(model, domain)
    raw_rows = await ot._odoo.execute_kw(
        model, "read_group", [dom, [s[1] for s in specs], group_by],
        {"lazy": False, "context": ot._UZ_CTX},
    )
    rows = []
    for raw in raw_rows:
        row = {spec: _group_label(raw, spec) for spec in group_by}
        row["soni"] = ot._group_count(raw, group_by[0]) if group_by else int(raw.get("__count") or 0)
        for key, _sent, returned in specs:
            row[key] = raw.get(returned)
        rows.append(row)
    return rows, [s[0] for s in specs]


def _is_date_group(spec: str) -> bool:
    return spec.partition(":")[2] in _GRANULARITIES


async def odoo_read_group(model: str, domain: list | None = None, group_by=None,
                          fields: list | None = None, order_by: str = "", limit: int = 50) -> dict:
    check_model(model)
    await _meta(model)
    groups = [group_by] if isinstance(group_by, str) else [g for g in (group_by or []) if isinstance(g, str)]
    rows, agg_keys = await _grouped(model, domain, groups, fields)
    if not groups:
        # Guruhsiz — bitta umumiy qator: shartga mos barcha yozuvlar bo'yicha son va jamlanmalar
        total = rows[0] if rows else {"soni": 0}
        return {"jami": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in total.items()},
                "guruhlar": [], "jami_guruhlar": 0}

    key, _, direction = (order_by or "").strip().partition(" ")
    descending = direction.strip().lower() != "asc"
    if key:
        if key not in agg_keys and key not in groups and key != "soni":
            raise ToolInputError(f"order_by kaliti noto'g'ri: {key!r}. Mumkin: {', '.join(['soni', *agg_keys, *groups])}")
        if not direction.strip():
            descending = key not in groups  # guruh nomi bo'yicha — o'sish, son bo'yicha — kamayish
    elif _is_date_group(groups[0]):
        key, descending = groups[0], False
    else:
        key, descending = (agg_keys[0] if agg_keys else "soni"), True
    rows.sort(key=lambda r: _sort_value(r.get(key)), reverse=descending)
    # Qiymati yo'q guruhlar har doim oxirida turadi
    rows.sort(key=lambda r: r.get(key) is None)

    totals: dict = {"soni": sum(r["soni"] for r in rows)}
    for agg_key in agg_keys:
        if agg_key.endswith(":sum") or ":" not in agg_key:
            values = [r[agg_key] for r in rows if isinstance(r.get(agg_key), (int, float))]
            if values:
                totals[agg_key] = round(sum(values), 2)

    limit = max(1, min(int(limit or 50), MAX_READ_LIMIT))
    result: dict = {"guruhlar": rows[:limit], "jami_guruhlar": len(rows), "jami": totals}
    if len(rows) > limit:
        result["ogohlantirish"] = (f"{len(rows)} ta guruhdan dastlabki {limit} tasi ko'rsatildi "
                                   f"({key} bo'yicha saralangan); 'jami' barcha guruhlar bo'yicha hisoblangan.")
    return result


def _measure_value(row: dict, measure: str):
    value = row.get("soni" if measure == "soni" else measure)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


async def odoo_compare(model: str, group_by: str, measure: str = "soni",
                       domain_a: list | None = None, domain_b: list | None = None,
                       label_a: str = "A", label_b: str = "B",
                       show: str = "changes", limit: int = 15) -> dict:
    """Bitta ko'rsatkichni ikki to'plamda (odatda ikki davrda) guruhlar bo'yicha solishtiradi."""
    check_model(model)
    await _meta(model)
    if not isinstance(group_by, str) or not group_by:
        raise ToolInputError("group_by — bitta maydon nomi (masalan \"partner_id\")")
    fields = [] if measure == "soni" else [measure]
    (rows_a, _), (rows_b, _) = await asyncio.gather(
        _grouped(model, domain_a, [group_by], fields),
        _grouped(model, domain_b, [group_by], fields),
    )

    def keyed(rows: list[dict]) -> dict:
        out = {}
        for row in rows:
            label = row.get(group_by)
            out[label[0] if isinstance(label, list) else label] = (label, _measure_value(row, measure))
        return out

    a, b = keyed(rows_a), keyed(rows_b)
    merged = []
    for group_key in a.keys() | b.keys():
        label = (a.get(group_key) or b.get(group_key))[0]
        value_a, value_b = (a.get(group_key) or (None, 0))[1], (b.get(group_key) or (None, 0))[1]
        item = {"guruh": label, "a": round(value_a, 2), "b": round(value_b, 2), "farq": round(value_b - value_a, 2)}
        if value_a:
            item["farq_foiz"] = round((value_b - value_a) / abs(value_a) * 100, 1)
        merged.append(item)

    lost = [m for m in merged if m["a"] and not m["b"]]
    new = [m for m in merged if m["b"] and not m["a"]]
    if show == "lost":
        picked = sorted(lost, key=lambda m: m["a"], reverse=True)
    elif show == "new":
        picked = sorted(new, key=lambda m: m["b"], reverse=True)
    elif show == "top_a":
        picked = sorted(merged, key=lambda m: m["a"], reverse=True)
    elif show == "top_b":
        picked = sorted(merged, key=lambda m: m["b"], reverse=True)
    elif show == "growth":
        picked = sorted((m for m in merged if m["farq"] > 0), key=lambda m: m["farq"], reverse=True)
    elif show == "decline":
        picked = sorted((m for m in merged if m["farq"] < 0), key=lambda m: m["farq"])
    else:
        picked = sorted(merged, key=lambda m: abs(m["farq"]), reverse=True)

    total_a, total_b = sum(v for _, v in a.values()), sum(v for _, v in b.values())
    limit = max(1, min(int(limit or 15), 100))
    result = {
        "olchov": measure, "guruhlash": group_by, "korsatish": show,
        "a": {"nomi": label_a, "jami": round(total_a, 2), "guruhlar_soni": len(a)},
        "b": {"nomi": label_b, "jami": round(total_b, 2), "guruhlar_soni": len(b)},
        "farq": round(total_b - total_a, 2),
        "faqat_a_da_bor": len(lost), "faqat_b_da_bor": len(new),
        "qatorlar": picked[:limit], "jami_qatorlar": len(picked),
    }
    if total_a:
        result["farq_foiz"] = round((total_b - total_a) / abs(total_a) * 100, 1)
    return result


# ─── Model va maydonlarni topish ────────────────────────────────────────────

_COUNTED_MODELS = 20  # yozuvlar soni faqat shuncha model uchun so'raladi (har biri alohida so'rov)


def _like(word: str) -> str:
    """LIKE namunasi: "_" va "%" oddiy belgi sifatida qidiriladi (aks holda "x_" har qanday
    "x?" ga mos kelib, natija keraksiz modellar bilan to'lardi)."""
    escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def odoo_models(query: str = "", limit: int = 25) -> list[dict]:
    words = [w for w in re.split(r"[\s,;]+", query or "") if len(w) >= 2][:6]
    domain: list = [["transient", "=", False]]
    if words:
        leaves: list = []
        for word in words:
            if word.casefold().startswith("x_"):
                leaves.append(["model", "=like", _like(word.casefold())[1:]])  # faqat shu bilan BOSHLANADIGANLAR
            else:
                leaves += [["name", "=ilike", _like(word)], ["model", "=ilike", _like(word)]]
        domain += ["|"] * (len(leaves) - 1) + leaves
    found = await ot._odoo.execute_kw(
        "ir.model", "search_read", [domain],
        {"fields": ["model", "name"], "limit": 80, "order": "model", "context": ot._UZ_CTX},
    )
    found = [m for m in found if m.get("model") not in BLOCKED_MODELS]
    # Qisqa nomli (asosiy) modellar oldinda: "purchase.order" "purchase.order.line.match"dan muhimroq
    found.sort(key=lambda m: (m["model"].count("."), len(m["model"])))

    async def with_count(item: dict) -> dict | None:
        try:
            count = await ot._odoo.execute_kw(item["model"], "search_count", [[]])
        except Exception:  # noqa: BLE001 — abstrakt model yoki ruxsat yo'q: ro'yxatga kirmaydi
            return None
        return {"model": item["model"], "nomi": item.get("name") or "", "yozuvlar": count}

    limit = max(1, min(int(limit or 25), 60))
    counted = [m for m in await asyncio.gather(*[with_count(m) for m in found[:_COUNTED_MODELS]]) if m]
    counted.sort(key=lambda m: m["yozuvlar"], reverse=True)
    rest = [{"model": m["model"], "nomi": m.get("name") or ""} for m in found[_COUNTED_MODELS:]]
    return (counted + rest)[:limit]


async def odoo_fields(model: str, query: str = "") -> dict:
    check_model(model)
    meta = await _meta(model)
    needle = (query or "").strip().casefold()
    lines = []
    for name, info in sorted(meta.items()):
        label = str(info.get("string") or "")
        if _SENSITIVE_FIELD_RE.search(name) or info.get("type") == "binary":
            continue
        if needle:
            if needle not in name.casefold() and needle not in label.casefold():
                continue
        elif ot._TECHNICAL_FIELD_RE.search(name):
            continue
        ftype = info.get("type") or "?"
        if info.get("relation"):
            ftype += f" -> {info['relation']}"
        line = f"{name} — {label} ({ftype})"
        selection = info.get("selection") or []
        if selection:
            shown = ", ".join(f"{value}={text}" for value, text in selection[:15])
            line += f" [qiymatlar: {shown}{', ...' if len(selection) > 15 else ''}]"
        if info.get("store") is False:
            line += " (saqlanmaydi)"
        lines.append(line)
    result: dict = {"model": model, "maydonlar": lines[:160], "jami": len(lines)}
    if len(lines) > 160:
        result["ogohlantirish"] = "Maydonlar ko'p — query bilan toraytiring (masalan 'date', 'amount', 'partner')."
    if not lines and needle:
        result["ogohlantirish"] = f"'{query}' bo'yicha maydon topilmadi — query'siz chaqirib to'liq ro'yxatni ko'ring."
    return result


TOOLS = {
    "odoo_search_read": odoo_search_read,
    "odoo_read_group": odoo_read_group,
    "odoo_count": odoo_count,
    "odoo_compare": odoo_compare,
    "odoo_models": odoo_models,
    "odoo_fields": odoo_fields,
}

_DOMAIN_DESC = ("Odoo domain: shartlar ro'yxati, masalan [[\"state\",\"=\",\"sale\"],[\"date_order\",\">=\",\"2026-10-01\"]]. "
                "Amallar: =, !=, >, >=, <, <=, in, not in, ilike, child_of. YOKI uchun \"|\" prefiksi. Bog'langan "
                "maydon bo'yicha ham filtrlash mumkin (\"partner_id.name\", \"order_id.state\"). Sanalar Toshkent "
                "vaqtida, \"YYYY-MM-DD\" ko'rinishida. Shartsiz — [].")

SCHEMAS = {
    "odoo_search_read": {
        "name": "odoo_search_read",
        "description": (
            "Odoo'ning ISTALGAN modelidan yozuvlarni o'qiydi — filtr, saralash va limit bilan. \"Oxirgi N ta\", "
            "\"eng katta N ta\", \"falonchining ...lari\" kabi savollar uchun: order bering (masalan "
            "\"amount_total desc\", \"date_order desc\") va limit=N. Bog'lanish maydonlari [id, nom] ko'rinishida "
            "qaytadi. Yig'indi/reyting uchun bu emas, odoo_read_group ishlating. Maydon nomiga ishonchingiz "
            "komil bo'lmasa avval odoo_fields chaqiring."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Texnik model nomi, masalan sale.order"},
                "domain": {"type": "array", "description": _DOMAIN_DESC},
                "fields": {"type": "array", "items": {"type": "string"},
                           "description": "Kerakli maydonlar (faqat zarurlari — natija ixcham bo'lsin)"},
                "limit": {"type": "integer", "description": "Nechta yozuv (sukut 50, eng ko'pi 500)"},
                "order": {"type": "string", "description": "Saralash, masalan \"amount_total desc\" yoki \"date_order desc, id desc\""},
                "offset": {"type": "integer", "description": "Nechta yozuvni o'tkazib yuborish (sahifalash uchun)"},
            },
            "required": ["model", "domain", "fields"],
        },
    },
    "odoo_read_group": {
        "name": "odoo_read_group",
        "description": (
            "Yozuvlarni guruhlab hisoblaydi (SQL GROUP BY kabi): har guruh uchun \"soni\" va so'ralgan "
            "jamlanmalar. Yig'indi, o'rtacha, reyting (top-N), taqsimot va vaqt bo'yicha dinamika uchun ASOSIY "
            "tool. Natija to'liq hisoblanib, order_by bo'yicha saralanadi va limit qo'llanadi — \"eng ko'p/eng "
            "kam\" savollarida natija aniq. 'jami' — barcha guruhlar yig'indisi. Pul summasini doim "
            "currency_id bilan birga guruhlang (turli valyutalar qo'shilib ketmasin)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "domain": {"type": "array", "description": _DOMAIN_DESC},
                "group_by": {"type": "array", "items": {"type": "string"},
                             "description": "Guruhlash maydonlari, masalan [\"partner_id\"] yoki [\"user_id\",\"currency_id\"]. "
                                            "Sana uchun davr qo'shing: \"date_order:day\" | \":week\" | \":month\" | \":quarter\" | \":year\". "
                                            "Bo'sh [] — guruhsiz, faqat umumiy son va jamlanma ('jami')"},
                "fields": {"type": "array", "items": {"type": "string"},
                           "description": "Jamlanmalar: \"maydon:amal\", amal — sum, avg, min, max, count_distinct. "
                                          "Masalan [\"amount_total:sum\", \"amount_total:avg\"]. Faqat sanash kerak bo'lsa []."},
                "order_by": {"type": "string",
                             "description": "Saralash kaliti va yo'nalishi: \"amount_total:sum desc\", \"soni desc\" yoki guruh maydoni. "
                                            "Berilmasa: sana guruhi — xronologik, boshqasi — birinchi jamlanma bo'yicha kamayish tartibida"},
                "limit": {"type": "integer", "description": "Nechta guruh ko'rsatilsin (sukut 50)"},
            },
            "required": ["model", "domain", "group_by", "fields"],
        },
    },
    "odoo_count": {
        "name": "odoo_count",
        "description": "Shartga mos yozuvlar sonini qaytaradi (bitta son).",
        "input_schema": {
            "type": "object",
            "properties": {"model": {"type": "string"}, "domain": {"type": "array", "description": _DOMAIN_DESC}},
            "required": ["model", "domain"],
        },
    },
    "odoo_compare": {
        "name": "odoo_compare",
        "description": (
            "Bitta ko'rsatkichni IKKI to'plamda (odatda ikki davrda) guruhlar kesimida solishtiradi va farqni "
            "hisoblaydi. \"Sotuv nega tushdi/o'sdi?\", \"qaysi mijozlar ketib qoldi?\", \"yangi mijozlar kim?\", "
            "\"qaysi mahsulot/sotuvchi eng ko'p o'sdi yoki pasaydi?\" kabi savollar uchun — yuzlab guruhni "
            "birma-bir so'rash o'rniga BITTA chaqiruv. domain_a va domain_b — to'liq filtrlar (davr shartlari "
            "bilan); pul bo'lsa ikkalasiga ham bir xil valyuta shartini qo'shing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "group_by": {"type": "string", "description": "Bitta guruhlash maydoni: partner_id, product_id, user_id ..."},
                "measure": {"type": "string", "description": "\"soni\" (yozuvlar soni) yoki jamlanma, masalan \"amount_total:sum\""},
                "domain_a": {"type": "array", "description": "Birinchi to'plam (masalan oldingi davr) filtri. " + _DOMAIN_DESC},
                "domain_b": {"type": "array", "description": "Ikkinchi to'plam (masalan joriy davr) filtri"},
                "label_a": {"type": "string", "description": "A nomi, masalan \"Sentabr\""},
                "label_b": {"type": "string", "description": "B nomi, masalan \"Oktabr\""},
                "show": {"type": "string", "enum": ["changes", "growth", "decline", "lost", "new", "top_a", "top_b"],
                         "description": "changes — eng katta o'zgarishlar (sukut); growth/decline — faqat o'sganlar/pasayganlar; "
                                        "lost — A da bor, B da yo'q; new — B da bor, A da yo'q; top_a/top_b — eng kattalari"},
                "limit": {"type": "integer", "description": "Nechta qator (sukut 15)"},
            },
            "required": ["model", "group_by", "measure", "domain_a", "domain_b"],
        },
    },
    "odoo_models": {
        "name": "odoo_models",
        "description": (
            "Odoo bazasidagi modellarni (jadvallarni) nomi bo'yicha qidiradi va har biridagi yozuvlar sonini "
            "ko'rsatadi. So'ralgan ma'lumot qaysi modelda turishini bilmasangiz yoki tanish model \"yo'q\" deb "
            "xato bersa shu tool bilan toping. query — inglizcha kalit so'z yoki texnik nom bo'lagi: "
            "\"purchase\", \"leave\", \"fleet\", \"attendance\", \"mrp\". Kompaniyaning o'zi yaratgan (Studio) "
            "modellari \"x_\" bilan boshlanadi — query=\"x_\" ularning hammasini ko'rsatadi."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Bir yoki bir nechta kalit so'z (probel bilan) — istalgani mos kelsa topiladi"},
                "limit": {"type": "integer", "description": "Nechta model (sukut 25)"},
            },
            "required": ["query"],
        },
    },
    "odoo_fields": {
        "name": "odoo_fields",
        "description": (
            "Model maydonlari ro'yxati: texnik nomi, ko'rinadigan nomi, turi, bog'langan modeli va tanlov "
            "qiymatlari. Notanish model bilan ishlashdan oldin yoki \"Invalid field\" xatosidan keyin chaqiring — "
            "maydon nomini taxmin qilmang. \"(saqlanmaydi)\" belgili maydonni o'qish mumkin, lekin u bo'yicha "
            "guruhlash, saralash va filtrlash ishlamasligi mumkin."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "query": {"type": "string", "description": "Ixtiyoriy: nom yoki sarlavha bo'lagi (\"date\", \"amount\", \"partner\") — ro'yxatni toraytiradi"},
            },
            "required": ["model"],
        },
    },
}
