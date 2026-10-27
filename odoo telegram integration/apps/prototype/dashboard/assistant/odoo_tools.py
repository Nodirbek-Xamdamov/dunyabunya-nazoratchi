"""Read-only Odoo access for the agents created on the «AI agentlar» page.

Each agent connects with the Odoo login and API key typed on the page (best: a separate Odoo user
with read rights only -- Odoo's own access rules are the real limit). On top of that:

* only the read methods fields_get, search_read, search_count and read_group are ever called;
* only the models listed in AGENT_ODOO_MODELS (dashboard\\.env) can be read;
* the filter (domain) comes as JSON, is parsed with json.loads (never eval) and checked: field
  names, a fixed list of operators, plain values;
* results are cut to a size a model can read (rows, fields, text length).
"""

from __future__ import annotations

import json
import re
import socket
import xmlrpc.client
from urllib.parse import urlparse

from assistant.llm import Tool, ToolError, nullable, obj

READ_METHODS = frozenset({"fields_get", "search_read", "search_count", "read_group"})
OPERATORS = ("=", "!=", ">", ">=", "<", "<=", "like", "not like", "ilike", "not ilike", "=like", "=ilike", "in",
             "not in", "child_of", "parent_of", "=?")
_FIELD = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*){0,3}$")
_GROUPBY = re.compile(r"^[a-z_][a-z0-9_]*(:(day|week|month|quarter|year))?$")
_AGGREGATE = re.compile(r"^[a-z_][a-z0-9_]*(:(sum|avg|min|max|count|count_distinct))?$")
MAX_LIMIT, DEFAULT_LIMIT, MAX_FIELDS, MAX_DOMAIN, MAX_TEXT, MAX_RESULT = 50, 20, 25, 30, 300, 20000
TIMEOUT = 25.0


class OdooError(ValueError):
    """The text says what went wrong (for the page and for the model)."""


def normalize_url(url: str) -> str:
    """'https://x.odoo.com/odoo/' -> 'https://x.odoo.com'."""
    url = (url or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise OdooError("Odoo manzili https://... ko'rinishida bo'lishi kerak.")
    return f"{parsed.scheme}://{parsed.netloc}"


class _Timeout:
    def __init__(self, timeout: float) -> None:
        self._timeout = timeout

    def make_connection(self, host):
        connection = super().make_connection(host)  # type: ignore[misc]
        connection.timeout = self._timeout
        return connection


class _HttpTransport(_Timeout, xmlrpc.client.Transport):
    def __init__(self, timeout: float) -> None:
        _Timeout.__init__(self, timeout)
        xmlrpc.client.Transport.__init__(self)


class _HttpsTransport(_Timeout, xmlrpc.client.SafeTransport):
    def __init__(self, timeout: float) -> None:
        _Timeout.__init__(self, timeout)
        xmlrpc.client.SafeTransport.__init__(self)


def _fault_text(exc: xmlrpc.client.Fault) -> str:
    lines = [line.strip() for line in str(exc.faultString or "").splitlines() if line.strip()]
    return (lines[-1] if lines else "Odoo xatosi")[:300]


class Odoo:
    """One Odoo connection (url, database, login, API key)."""

    def __init__(self, url: str, db: str, login: str, api_key: str, timeout: float = TIMEOUT) -> None:
        self.url, self.db, self.login, self._key = normalize_url(url), (db or "").strip(), (login or "").strip(), api_key
        self.timeout = timeout
        self._uid: int | None = None
        self._fields: dict[str, dict] = {}

    def _proxy(self, endpoint: str) -> xmlrpc.client.ServerProxy:
        transport = (_HttpsTransport if self.url.startswith("https") else _HttpTransport)(self.timeout)
        return xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/{endpoint}", transport=transport, allow_none=True)

    def _guard(self, action):
        try:
            return action()
        except xmlrpc.client.Fault as exc:
            raise OdooError(f"Odoo rad etdi: {_fault_text(exc)}") from None
        except xmlrpc.client.ProtocolError as exc:
            raise OdooError(f"Odoo javob bermadi (HTTP {exc.errcode}). Manzilni tekshiring.") from None
        except (OSError, socket.timeout, xmlrpc.client.Error):
            raise OdooError("Odoo'ga ulanib bo'lmadi (manzil yoki internet).") from None

    def uid(self) -> int:
        if self._uid is None:
            if not (self.db and self.login and self._key):
                raise OdooError("Baza nomi, login va API kalit kerak.")
            uid = self._guard(lambda: self._proxy("common").authenticate(self.db, self.login, self._key, {}))
            if not uid:
                raise OdooError("Login yoki API kalit noto'g'ri (yoki baza nomi).")
            self._uid = int(uid)
        return self._uid

    def execute(self, model: str, method: str, args: list, kwargs: dict | None = None):
        if method not in READ_METHODS:  # this module never writes, whatever a caller asks
            raise OdooError(f"{method} -- faqat o'qish ruxsat etilgan.")
        uid = self.uid()
        return self._guard(lambda: self._proxy("object").execute_kw(self.db, uid, self._key, model, method, args,
                                                                     kwargs or {}))

    def check(self) -> dict:
        """Logs in and reads the user's own name: {"user": ..., "version": ...}."""
        version = self._guard(lambda: self._proxy("common").version()) or {}
        uid = self.uid()
        users = self._guard(lambda: self._proxy("object").execute_kw(self.db, uid, self._key, "res.users", "read",
                                                                      [[uid]], {"fields": ["name"]}))
        name = users[0]["name"] if users else self.login
        return {"user": str(name), "version": str(version.get("server_version") or "")}

    def fields(self, model: str) -> dict:
        if model not in self._fields:
            self._fields[model] = self.execute(model, "fields_get", [], {"attributes": ["string", "type", "relation"]})
        return self._fields[model]


# --------------------------------------------------------------------------- checking what the model asks for


def parse_domain(text) -> list:
    """The filter as JSON in Odoo's notation, e.g. [["state", "=", "sale"], ["date_order", ">=", "2026-10-01"]]
    or with "|", "&", "!". Never evaluated as code."""
    if text in (None, "", "[]"):
        return []
    if isinstance(text, str):
        try:
            text = json.loads(text)
        except ValueError:
            raise ToolError('domain must be JSON with double quotes, e.g. [["state", "=", "sale"]]') from None
    if not isinstance(text, list) or len(text) > MAX_DOMAIN:
        raise ToolError(f"domain must be a list of at most {MAX_DOMAIN} items")
    domain = []
    for item in text:
        if item in ("&", "|", "!"):
            domain.append(item)
            continue
        if not (isinstance(item, list) and len(item) == 3):
            raise ToolError(f"each condition is [field, operator, value]; got {json.dumps(item)[:80]}")
        field, operator, value = item
        if not isinstance(field, str) or not _FIELD.match(field):
            raise ToolError(f"bad field name {field!r}")
        if operator not in OPERATORS:
            raise ToolError(f"operator {operator!r} is not allowed; use one of {', '.join(OPERATORS)}")
        plain = (str, int, float, bool, type(None))
        if not (isinstance(value, plain) or (isinstance(value, list) and len(value) <= 200
                                             and all(isinstance(part, plain) for part in value))):
            raise ToolError(f"the value of {field} must be a number, text, true/false, null or a list of them")
        domain.append([field, operator, value])
    return domain


def _names(values, pattern: re.Pattern, what: str, limit: int) -> list[str]:
    values = [str(value).strip() for value in values or [] if str(value).strip()]
    if len(values) > limit:
        raise ToolError(f"at most {limit} {what}")
    for value in values:
        if not pattern.match(value):
            raise ToolError(f"bad {what[:-1]} {value!r}")
    return values


def _short(value):
    if isinstance(value, str):
        return value if len(value) <= MAX_TEXT else value[:MAX_TEXT] + "…"
    if isinstance(value, list):
        if len(value) == 2 and isinstance(value[0], int) and isinstance(value[1], str):  # many2one: [id, name]
            return f"{_short(value[1])} (#{value[0]})"
        shown = [_short(item) for item in value[:10]]
        return shown + ([f"… +{len(value) - 10}"] if len(value) > 10 else [])
    return value


def _fit(payload: dict, rows_key: str) -> dict:
    """Drops rows from the end until the JSON fits MAX_RESULT characters."""
    rows = payload[rows_key]
    while rows and len(json.dumps(payload, ensure_ascii=False, default=str)) > MAX_RESULT:
        rows.pop()
        payload["cut"] = "the result was too long; ask for fewer fields or rows"
    return payload


def _limit(value) -> int:
    try:
        return max(1, min(MAX_LIMIT, int(value or DEFAULT_LIMIT)))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT


def build(odoo: Odoo, models: tuple[str, ...]) -> list[Tool]:
    """The four read tools over one connection."""
    allowed = list(models)

    def model_of(args: dict) -> str:
        model = str(args.get("model") or "")
        if model not in allowed:
            raise ToolError(f"model {model!r} is not allowed; allowed: {', '.join(allowed)}")
        return model

    def odoo_fields(args: dict) -> dict:
        model = model_of(args)
        fields = odoo.fields(model)
        rows = [f"{name} ({info.get('type')}{', ' + info['relation'] if info.get('relation') else ''}) — "
                f"{info.get('string', '')}" for name, info in sorted(fields.items())
                if info.get("type") not in ("binary",)]
        return _fit({"model": model, "fields": rows[:250]}, "fields")

    def odoo_search(args: dict) -> dict:
        model, limit = model_of(args), _limit(args.get("limit"))
        fields = _names(args.get("fields"), _FIELD, "fields", MAX_FIELDS) or ["display_name"]
        order = str(args.get("order") or "").strip()
        if order and not re.match(r"^[a-z_][a-z0-9_]*( (asc|desc))?(, ?[a-z_][a-z0-9_]*( (asc|desc))?)*$", order):
            raise ToolError("order must look like 'date_order desc, id desc'")
        kwargs = {"fields": fields, "limit": limit}
        if order:
            kwargs["order"] = order
        rows = odoo.execute(model, "search_read", [parse_domain(args.get("domain"))], kwargs)
        records = [{key: _short(value) for key, value in row.items()} for row in rows]
        payload = {"model": model, "rows": len(records), "records": records}
        if len(records) == limit:
            payload["more"] = "there may be more rows: use odoo_count or a narrower filter"
        return _fit(payload, "records")

    def odoo_count(args: dict) -> dict:
        model = model_of(args)
        return {"model": model, "count": odoo.execute(model, "search_count", [parse_domain(args.get("domain"))])}

    def odoo_group(args: dict) -> dict:
        model, limit = model_of(args), _limit(args.get("limit"))
        groupby = _names(args.get("groupby"), _GROUPBY, "groupbys", 3)
        aggregates = _names(args.get("aggregates"), _AGGREGATE, "aggregates", 10)
        if not groupby:
            raise ToolError("give 1-3 groupby fields, e.g. ['date_order:month'] or ['partner_id']")
        rows = odoo.execute(model, "read_group", [parse_domain(args.get("domain")), aggregates, groupby],
                            {"lazy": False, "limit": limit})
        groups = [{key: _short(value) for key, value in row.items() if not key.startswith("__") or key == "__count"}
                  for row in rows]
        return _fit({"model": model, "groups": groups}, "groups")

    model_field = {"type": "string", "enum": allowed}
    domain_field = nullable({"type": "string"}, 'Filter as JSON in Odoo domain notation, e.g. [["state", "=", "sale"], '
                                                '["date_order", ">=", "2026-10-01 00:00:00"]]; null = no filter')
    strings = {"type": "array", "items": {"type": "string"}}
    return [
        Tool("odoo_fields", "List the fields of an Odoo model (name, type, related model, label). Use it before a "
                            "search when you are not sure about field names.", obj({"model": model_field}), odoo_fields),
        Tool("odoo_search", "Read records of an Odoo model (search_read): only the fields you list, newest first if "
                            "you give an order. Odoo returns false for empty values; many2one values come as "
                            "'name (#id)'.",
             obj({"model": model_field, "domain": domain_field, "fields": strings,
                  "order": nullable({"type": "string"}, "e.g. 'date_order desc'"),
                  "limit": nullable({"type": "integer"}, f"rows, 1-{MAX_LIMIT} (default {DEFAULT_LIMIT})")}),
             odoo_search),
        Tool("odoo_count", "Count the records of an Odoo model that match a filter.",
             obj({"model": model_field, "domain": domain_field}), odoo_count),
        Tool("odoo_group", "Totals per group (read_group): e.g. sales per month or per customer. aggregates like "
                           "'amount_total:sum'; groupby like 'date_order:month' or 'partner_id'. Each group has "
                           "__count.",
             obj({"model": model_field, "domain": domain_field, "aggregates": strings, "groupby": strings,
                  "limit": nullable({"type": "integer"}, f"groups, 1-{MAX_LIMIT}")}), odoo_group),
    ]


def reference(config: dict | None, models: tuple[str, ...]) -> str:
    """The Odoo part of an agent's system prompt."""
    if not config:
        return ("Odoo is NOT connected for you yet. If a request needs Odoo data, say that an Odoo connection has to "
                "be added to this agent on the «AI agentlar» page.")
    host = urlparse(config.get("url", "")).netloc
    return (f"Connected to Odoo at {host}, database {config.get('db', '')}, as {config.get('user') or config.get('login')}"
            f" (read-only: you cannot create, change or delete anything in Odoo).\n"
            f"Models you may read: {', '.join(models)}.\n"
            "Use odoo_fields when unsure about a field name, odoo_count for 'how many', odoo_group for totals per "
            "month/customer/product, odoo_search for the records themselves. Dates and times in Odoo are UTC "
            "('YYYY-MM-DD HH:MM:SS'); Tashkent is UTC+5. Amounts are in the company currency unless a currency "
            "field says otherwise. Take every number from tool results.")
