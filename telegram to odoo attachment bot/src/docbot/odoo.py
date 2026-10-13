"""Odoo 18 over XML-RPC: find records and put files into their chatter. Nothing else is ever written.

* Blocking ``xmlrpc.client`` calls run in threads. A ``ServerProxy`` keeps one HTTP connection and is
  not thread-safe, so every thread has its own.
* Every call carries ``allowed_company_ids`` = all companies of the API user: documents of every
  company are found, not just those of the user's default company.
* Network trouble, timeouts and HTTP 5xx/429 raise ``TransientError`` (the job waits and retries);
  a refusal by Odoo itself (access rights, missing record, validation) raises ``OdooError``.
* A refused login (wrong/revoked API key, rebuilt database) is not the job's fault: jobs wait without
  using up attempts (``counts=False``) and ``login_error`` tells the app to alert the admins once.
* ``attach_to_chatter`` is idempotent per job: the attachment carries the job's marker in its
  description, so a retry after a crash never adds a second copy or a second chatter message. The same
  file sent again in a new message is a new job and is attached again (the user's rule).
* Odoo.sh closes idle keep-alive connections; the first request after a pause then fails at once
  (SSLEOFError). That is retried immediately on a new connection instead of waiting for the backoff.
"""

import asyncio
import base64
import http.client
import logging
import ssl
import threading
import xmlrpc.client
from dataclasses import dataclass
from typing import Any

from docbot.errors import PermanentError, TransientError

log = logging.getLogger(__name__)


# A kept-alive connection the server has closed in the meantime: the request never reached Odoo.
_STALE_CONNECTION = (
    ssl.SSLEOFError,
    http.client.RemoteDisconnected,
    http.client.CannotSendRequest,
    http.client.ResponseNotReady,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
)


class OdooError(PermanentError):
    """Odoo refused the request. The message is the last line of Odoo's error (no traceback)."""


@dataclass(frozen=True)
class OdooRecord:
    model: str
    id: int
    name: str
    values: dict[str, Any]  # the fields read for it (raw XML-RPC values)

    def many2one(self, field: str | None) -> tuple[int, str] | None:
        value = self.values.get(field) if field else None
        if isinstance(value, list | tuple) and len(value) == 2:
            return int(value[0]), str(value[1])
        return None


class _Transport(xmlrpc.client.SafeTransport):
    def __init__(self, timeout: float):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self._timeout
        return connection


class _PlainTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: float):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self._timeout
        return connection


def _fault_message(fault: xmlrpc.client.Fault) -> str:
    lines = [line.strip() for line in str(fault.faultString).strip().splitlines() if line.strip()]
    return lines[-1] if lines else f"fault {fault.faultCode}"


class OdooClient:
    def __init__(self, url: str, db: str, login: str, api_key: str, *, timeout: float = 60.0):
        self._url = url.rstrip("/")
        self._db = db
        self._login = login
        self._key = api_key
        self._timeout = timeout
        self._local = threading.local()
        self._session_lock = threading.Lock()
        self._uid: int | None = None
        self._company_ids: list[int] = []
        self.login_error: str | None = None  # set while Odoo refuses our login

    # ------------------------------------------------------------------ blocking internals (threads)

    def _proxy(self, service: str) -> xmlrpc.client.ServerProxy:
        proxies = getattr(self._local, "proxies", None)
        if proxies is None:
            proxies = self._local.proxies = {}
        if service not in proxies:
            transport = (_Transport if self._url.startswith("https") else _PlainTransport)(self._timeout)
            proxies[service] = xmlrpc.client.ServerProxy(
                f"{self._url}/xmlrpc/2/{service}", transport=transport, allow_none=True
            )
        return proxies[service]

    def _session(self) -> tuple[int, list[int]]:
        with self._session_lock:
            if self._uid is None:
                uid = self._proxy("common").authenticate(self._db, self._login, self._key, {})
                if not uid:
                    self.login_error = "Odoo login rad etildi (ODOO_USER / ODOO_API_KEY ni tekshiring)"
                    raise TransientError(self.login_error, retry_after=120, counts=False)
                users = self._proxy("object").execute_kw(
                    self._db, uid, self._key, "res.users", "read", [[uid]], {"fields": ["company_ids"]}
                )
                self._uid, self._company_ids = uid, list(users[0]["company_ids"]) if users else []
                self.login_error = None
                log.info("Odoo: logged in as uid %s, companies %s", uid, self._company_ids)
            return self._uid, self._company_ids

    def _execute(self, model: str, method: str, args: list, kwargs: dict) -> Any:
        uid, companies = self._session()
        context = dict(kwargs.pop("context", None) or {})
        if companies:
            context.setdefault("allowed_company_ids", companies)
        kwargs["context"] = context
        return self._proxy("object").execute_kw(self._db, uid, self._key, model, method, args, kwargs)

    def _reconnecting(self, func, *args) -> Any:
        try:
            return func(*args)
        except _STALE_CONNECTION as exc:
            log.info("Odoo closed the idle connection (%s); reconnecting", type(exc).__name__)
            self._local.proxies = {}
            return func(*args)

    # ------------------------------------------------------------------ async API

    async def _call(self, func, *args) -> Any:
        try:
            async with asyncio.timeout(self._timeout + 10):
                return await asyncio.to_thread(self._reconnecting, func, *args)
        except xmlrpc.client.Fault as fault:
            message = _fault_message(fault)
            if "AccessDenied" in str(fault.faultString) or "Access Denied" in message:
                with self._session_lock:
                    self._uid = None  # the key may have changed; log in again next time
                    self.login_error = f"Odoo kirish rad etildi: {message}"
                raise TransientError(self.login_error, retry_after=120, counts=False) from None
            raise OdooError(f"Odoo: {message}") from None
        except xmlrpc.client.ProtocolError as exc:
            raise TransientError(f"Odoo HTTP {exc.errcode} {exc.errmsg}") from None
        except (OSError, http.client.HTTPException, TimeoutError) as exc:
            raise TransientError(f"Odoo bilan aloqa yo'q: {type(exc).__name__}: {exc}") from None

    async def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        return await self._call(self._execute, model, method, list(args), kwargs)

    async def version(self) -> str:
        info = await self._call(lambda: self._proxy("common").version())
        await self._call(self._session)
        return str(info.get("server_version", "?"))

    async def read_by_id(self, model: str, record_id: int, fields: list[str]) -> OdooRecord | None:
        rows = await self.execute(model, "search_read", [("id", "=", record_id)], fields=["name", *fields], limit=1)
        return self._record(model, rows[0]) if rows else None

    async def find_by_name(self, model: str, name: str, fields: list[str]) -> list[OdooRecord]:
        rows = await self.execute(model, "search_read", [("name", "=", name)], fields=["name", *fields], limit=2)
        return [self._record(model, row) for row in rows]

    async def partner(self, partner_id: int) -> dict[str, Any] | None:
        rows = await self.execute("res.partner", "read", [partner_id], fields=["name", "phone", "mobile"])
        return rows[0] if rows else None

    async def attach_to_chatter(
        self, model: str, res_id: int, *, filename: str, data: bytes, mimetype: str, body: str, marker: str
    ) -> tuple[int, int]:
        """Put the file into the record's chatter as a note. Returns (attachment id, message id)."""
        found = await self.execute(
            "ir.attachment",
            "search",
            [("res_model", "=", model), ("res_id", "=", res_id), ("description", "=", marker)],
            limit=1,
        )
        if found:
            attachment_id = found[0]
        else:
            attachment_id = await self.execute(
                "ir.attachment",
                "create",
                {
                    "name": filename,
                    "datas": base64.b64encode(data).decode("ascii"),
                    "res_model": model,
                    "res_id": res_id,
                    "mimetype": mimetype,
                    "description": marker,
                },
            )
        posted = await self.execute(
            "mail.message",
            "search",
            [("model", "=", model), ("res_id", "=", res_id), ("attachment_ids", "in", [attachment_id])],
            limit=1,
        )
        if posted:
            return attachment_id, posted[0]
        message_id = await self.execute(
            model,
            "message_post",
            [res_id],
            body=body,
            attachment_ids=[attachment_id],
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        return attachment_id, int(message_id)

    @staticmethod
    def _record(model: str, row: dict[str, Any]) -> OdooRecord:
        return OdooRecord(model=model, id=int(row["id"]), name=str(row.get("name") or row["id"]), values=row)
