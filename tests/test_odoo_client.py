"""OdooClient against a local XML-RPC server that behaves like the parts of Odoo we use."""

import base64
import hashlib
import socket
import ssl
import threading
import xmlrpc.client
from xmlrpc.server import MultiPathXMLRPCServer, SimpleXMLRPCDispatcher, SimpleXMLRPCRequestHandler

import pytest

from docbot.errors import TransientError
from docbot.odoo import OdooClient, OdooError

KEY = "0123456789abcdef"


class _Handler(SimpleXMLRPCRequestHandler):
    rpc_paths = ("/xmlrpc/2/common", "/xmlrpc/2/object")


class FakeOdooServer:
    def __init__(self):
        self.records = {
            ("sale.order", 89452): {"id": 89452, "name": "S82409", "access_token": "tok-1", "amount_total": 404000.0},
            ("stock.picking", 252495): {"id": 252495, "name": "OD/INT/01514"},
        }
        self.partners = {81983: {"id": 81983, "name": "MUSLIMBEK 0774", "phone": "+998906000774", "mobile": False}}
        self.attachments: list[dict] = []
        self.messages: list[dict] = []
        self.contexts: list[dict] = []
        self.server = MultiPathXMLRPCServer(
            ("127.0.0.1", 0), requestHandler=_Handler, logRequests=False, allow_none=True, use_builtin_types=True
        )
        common = SimpleXMLRPCDispatcher(allow_none=True, use_builtin_types=True)
        common.register_function(lambda: {"server_version": "18.0+e"}, "version")
        common.register_function(self.authenticate, "authenticate")
        obj = SimpleXMLRPCDispatcher(allow_none=True, use_builtin_types=True)
        obj.register_function(self.execute_kw, "execute_kw")
        self.server.add_dispatcher("/xmlrpc/2/common", common)
        self.server.add_dispatcher("/xmlrpc/2/object", obj)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def authenticate(self, db, login, key, _env):
        return 2 if (login, key) == ("docbot", KEY) else False

    def execute_kw(self, db, uid, key, model, method, args, kwargs):
        if key != KEY:
            raise xmlrpc.client.Fault(3, "Traceback...\nodoo.exceptions.AccessDenied: Access Denied")
        self.contexts.append(kwargs.pop("context", {}))
        if model == "res.users" and method == "read":
            return [{"id": uid, "company_ids": [1, 2, 6]}]
        if model == "res.partner" and method == "read":
            return [self.partners[i] for i in args[0] if i in self.partners]
        if method == "search_read":
            (field, _op, value), = args[0]
            rows = [r for (m, _), r in self.records.items() if m == model and r.get(field) == value]
            return [{k: v for k, v in r.items() if k in [*kwargs["fields"], "id"]} for r in rows][: kwargs.get("limit")]
        if model == "ir.attachment" and method == "search":
            want = {f: v for f, _op, v in args[0]}
            return [a["id"] for a in self.attachments if all(a[f] == v for f, v in want.items())][: kwargs.get("limit")]
        if model == "ir.attachment" and method == "create":
            values = args[0]
            raw = base64.b64decode(values["datas"])
            self.attachments.append({**values, "id": len(self.attachments) + 1, "checksum": hashlib.sha1(raw).hexdigest(), "raw": raw})
            return len(self.attachments)
        if model == "mail.message" and method == "search":
            want = {f: v for f, _op, v in args[0]}
            return [
                m["id"]
                for m in self.messages
                if m["model"] == want["model"] and m["res_id"] == want["res_id"] and set(want["attachment_ids"]) & set(m["attachment_ids"])
            ]
        if method == "message_post":
            if model == "sale.order" and (model, args[0][0]) not in self.records:
                raise xmlrpc.client.Fault(2, "Traceback (most recent call last):\n  ...\nodoo.exceptions.MissingError: Record does not exist")
            self.messages.append({"id": 900 + len(self.messages), "model": model, "res_id": args[0][0], **kwargs})
            return self.messages[-1]["id"]
        raise xmlrpc.client.Fault(1, f"not implemented in the fake: {model}.{method}")


@pytest.fixture
def odoo_server():
    fake = FakeOdooServer()
    fake.thread.start()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


@pytest.fixture
def client(odoo_server) -> OdooClient:
    return OdooClient(odoo_server.url, "db", "docbot", KEY, timeout=5)


async def test_records_are_found_by_id_and_by_name_in_all_companies(client, odoo_server):
    record = await client.read_by_id("sale.order", 89452, ["access_token", "amount_total"])
    assert (record.name, record.values["access_token"], record.values["amount_total"]) == ("S82409", "tok-1", 404000.0)
    [picking] = await client.find_by_name("stock.picking", "OD/INT/01514", [])
    assert picking.id == 252495
    assert await client.read_by_id("sale.order", 1, []) is None
    assert odoo_server.contexts[-1] == {"allowed_company_ids": [1, 2, 6]}  # documents of every company are visible


async def test_the_file_goes_into_the_chatter_once_even_when_repeated(client, odoo_server):
    data = b"\xff\xd8\xff photo bytes"
    kwargs = dict(filename="S82409.jpg", data=data, mimetype="image/jpeg", body="Telegram: Ali")
    first = await client.attach_to_chatter("sale.order", 89452, marker="job 1", **kwargs)
    again = await client.attach_to_chatter("sale.order", 89452, marker="job 1", **kwargs)  # a retry of the same job
    assert first == again
    assert len(odoo_server.attachments) == 1 and len(odoo_server.messages) == 1
    attachment, message = odoo_server.attachments[0], odoo_server.messages[0]
    assert (attachment["res_model"], attachment["res_id"], attachment["raw"]) == ("sale.order", 89452, data)
    assert message["attachment_ids"] == [attachment["id"]]
    assert (message["body"], message["message_type"], message["subtype_xmlid"]) == ("Telegram: Ali", "comment", "mail.mt_note")
    assert attachment["description"] == "job 1"


async def test_the_same_file_from_another_message_is_attached_again(client, odoo_server):
    kwargs = dict(filename="S82409.jpg", data=b"same bytes", mimetype="image/jpeg", body="b")
    await client.attach_to_chatter("sale.order", 89452, marker="job 1", **kwargs)
    await client.attach_to_chatter("sale.order", 89452, marker="job 2", **kwargs)
    assert len(odoo_server.attachments) == 2 and len(odoo_server.messages) == 2


async def test_an_upload_without_its_chatter_message_is_completed_not_duplicated(client, odoo_server):
    data = b"pdf bytes"
    await client.execute(
        "ir.attachment", "create",
        {"name": "x.pdf", "datas": base64.b64encode(data).decode(), "res_model": "sale.order", "res_id": 89452,
         "mimetype": "application/pdf", "description": "job 7"},
    )  # a crash happened right after this in a previous attempt
    await client.attach_to_chatter("sale.order", 89452, filename="x.pdf", data=data, mimetype="application/pdf", body="b", marker="job 7")
    assert len(odoo_server.attachments) == 1
    assert odoo_server.messages[0]["attachment_ids"] == [1]


async def test_an_odoo_refusal_is_permanent_and_carries_only_the_last_line(client):
    with pytest.raises(OdooError, match=r"^Odoo: odoo.exceptions.MissingError: Record does not exist$"):
        await client.attach_to_chatter("sale.order", 1, filename="a.jpg", data=b"a", mimetype="image/jpeg", body="b", marker="m")


async def test_a_rejected_login_makes_jobs_wait_and_is_reported(odoo_server):
    client = OdooClient(odoo_server.url, "db", "docbot", "wrong-key", timeout=5)
    with pytest.raises(TransientError, match="login rad etildi") as info:
        await client.read_by_id("sale.order", 89452, [])
    assert (info.value.retry_after, info.value.counts) == (120, False)  # waits, never fails the job
    assert "login rad etildi" in client.login_error
    client._key = KEY  # the key was fixed
    await client.read_by_id("sale.order", 89452, [])
    assert client.login_error is None


async def test_an_unreachable_odoo_is_a_transient_problem():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]  # nothing listens here once the socket is closed
    client = OdooClient(f"http://127.0.0.1:{port}", "db", "docbot", KEY, timeout=2)
    with pytest.raises(TransientError, match="Odoo bilan aloqa yo'q"):
        await client.read_by_id("sale.order", 1, [])


async def test_a_connection_closed_while_idle_is_reopened_at_once(client, odoo_server):
    await client.read_by_id("sale.order", 89452, [])
    calls = {"n": 0}
    real = client._execute

    def closed_once(*args):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ssl.SSLEOFError(8, "EOF occurred in violation of protocol")
        return real(*args)

    client._execute = closed_once
    assert (await client.read_by_id("sale.order", 89452, [])).name == "S82409"
    assert calls["n"] == 2


async def test_version_logs_in(client):
    assert await client.version() == "18.0+e"
