"""Vazifa (project.task) hayot aylanishi va OdooClient darajasidagi testlar:

- create_task yangi vazifani NEYTRAL ('Jarayonda') bosqichga qo'yishi — bo'lim
  loyihasi 0 ta bosqich bilan boshlanganda ham, hali tegilmagan vazifa
  "Bajarildi" ustunida ko'rinib qolmasligi kerak (jonli Odoo'da topilgan va
  tasdiqlangan regressiya: task #178/#182, state='01_in_progress' bo'lsa ham
  stage_id='Bajarildi' edi).
- update_task_stage "Bajarildi"/"Bekor qilindi" bosganda HAM stage_id, HAM
  `state` maydonini birga yozishi (task "In Progress"da qotib qolmasligi).
- XML-RPC timeout haqiqatan socket ulanishiga o'rnatilishi (ODOO_TIMEOUT_SECONDS
  ishlatilmay qolgan — botni abadiy osilib qolishga olib kelgan asl xato).

Barcha testlar mok'langan execute_kw bilan ishlaydi — haqiqiy Odoo'ga chiqilmaydi
(faqat timeout-transport testlari haqiqiy (lekin faqat LOKAL, tarmoqqa chiqmaydigan)
xmlrpc.client obyektlarini quradi).
"""
from __future__ import annotations

import pytest

import app.services.odoo as odoo_module
from app.services.odoo import OdooClient, OdooError, _TimeoutTransport, _TimeoutSafeTransport


@pytest.fixture(autouse=True)
def _clear_stage_and_meta_caches(monkeypatch):
    """stage_id/fields_get natijalari endi (tezlik uchun) modul darajasida
    keshlanadi — testlar bir-biriga bog'liq bo'lib qolmasligi uchun har safar tozalanadi."""
    monkeypatch.setattr(odoo_module, "_stage_cache", {})
    monkeypatch.setattr(odoo_module, "_task_state_meta_cache", None)
    yield


@pytest.fixture
def client():
    return OdooClient(
        url="https://test.odoo.example.com",
        db="test_db", login="test@test.com", api_key="test-key", timeout=7,
    )


# ── create_task: neytral bosqich kafolati ───────────────────────────────────

async def test_create_task_ensures_neutral_stage_when_project_has_none(client, monkeypatch):
    """Loyihada hali bosqich yo'q — create_task 'Jarayonda' bosqichini
    sequence=0 bilan yaratib, YANGI vazifaga aynan shu stage_id ni berishi shart."""
    calls = []

    async def fake_execute_kw(model, method, args, kwargs=None):
        calls.append((model, method, args, kwargs))
        if model == "project.task.type" and method == "search_read":
            return []  # hali bosqich yo'q
        if model == "project.task.type" and method == "create":
            assert args[0]["name"] == "Jarayonda"
            assert args[0]["sequence"] == 0
            return 501
        if model == "project.task" and method == "create":
            assert args[0]["stage_id"] == 501
            return 900
        raise AssertionError(f"unexpected call: {model}.{method}")

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    task_id = await client.create_task(project_id=39, name="Yangi xodim olish")
    assert task_id == 900


async def test_create_task_reuses_existing_neutral_stage(client, monkeypatch):
    """'Jarayonda' bosqichi allaqachon bo'lsa — qayta yaratilmaydi, mavjudi ishlatiladi."""
    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "project.task.type" and method == "search_read":
            return [{"id": 42}]
        if model == "project.task.type" and method == "create":
            raise AssertionError("Jarayonda allaqachon bor edi — qayta yaratilmasligi kerak")
        if model == "project.task" and method == "create":
            assert args[0]["stage_id"] == 42
            return 901
        raise AssertionError(f"unexpected call: {model}.{method}")

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    task_id = await client.create_task(project_id=39, name="Boshqa vazifa")
    assert task_id == 901


async def test_create_task_still_works_if_stage_ensure_fails(client, monkeypatch):
    """_ensure_working_stage xato bersa ham, vazifa yaratish TO'XTAMASLIGI kerak
    (stage_id shunchaki qo'yilmaydi, Odoo o'z standart bosqichini tanlaydi)."""
    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "project.task.type":
            raise RuntimeError("Odoo vaqtincha ishlamayapti")
        if model == "project.task" and method == "create":
            assert "stage_id" not in args[0]
            return 902
        raise AssertionError(f"unexpected call: {model}.{method}")

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    task_id = await client.create_task(project_id=39, name="Vazifa")
    assert task_id == 902


async def test_create_task_passes_through_description_assignee_deadline(client, monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "project.task.type" and method == "search_read":
            return [{"id": 1}]
        if model == "project.task" and method == "create":
            vals = args[0]
            assert vals["description"] == "Batafsil tavsif"
            assert vals["user_ids"] == [(6, 0, [55])]
            assert vals["date_deadline"] == "2026-09-10"
            return 903
        raise AssertionError

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    task_id = await client.create_task(
        project_id=39, name="Vazifa", description="Batafsil tavsif",
        user_ids=[55], date_deadline="2026-09-10",
    )
    assert task_id == 903


# ── update_task_stage: state VA stage_id birga yoziladi ─────────────────────

_STATE_SELECTION = [
    ["01_in_progress", "In Progress"],
    ["02_changes_requested", "Changes Requested"],
    ["03_approved", "Approved"],
    ["1_done", "Done"],
    ["1_canceled", "Cancelled"],
    ["04_waiting_normal", "Waiting"],
]


async def test_update_task_stage_done_writes_state_and_stage_together(client, monkeypatch):
    written = {}

    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "project.task" and method == "read":
            return [{"id": 182, "project_id": [39, "HR"]}]
        if model == "project.task.type" and method == "search_read":
            return []  # "Bajarildi" hali yo'q
        if model == "project.task.type" and method == "create":
            assert args[0]["name"] == "Bajarildi"
            assert args[0]["sequence"] == 10  # "Jarayonda"(0)dan KEYIN
            return 242
        if model == "project.task" and method == "fields_get":
            return {"state": {"selection": _STATE_SELECTION}}
        if model == "project.task" and method == "write":
            written.update(args[1])
            return True
        raise AssertionError(f"unexpected call: {model}.{method}")

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    await client.update_task_stage(182, done=True)

    # Bu aynan jonli topilgan xato: stage_id "Bajarildi"ga o'tsa-yu, state
    # "01_in_progress"da qolib ketmasligi kerak.
    assert written["stage_id"] == 242
    assert written["state"] == "1_done"


async def test_update_task_stage_cancel_writes_correct_state(client, monkeypatch):
    written = {}

    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "project.task" and method == "read":
            return [{"id": 5, "project_id": [40, "Marketing"]}]
        if model == "project.task.type" and method == "search_read":
            return [{"id": 77}]
        if model == "project.task" and method == "fields_get":
            return {"state": {"selection": _STATE_SELECTION}}
        if model == "project.task" and method == "write":
            written.update(args[1])
            return True
        raise AssertionError(f"unexpected call: {model}.{method}")

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    await client.update_task_stage(5, done=False)
    assert written["stage_id"] == 77
    assert written["state"] == "1_canceled"


async def test_update_task_stage_falls_back_without_state_if_write_rejects_it(client, monkeypatch):
    """Agar `state` bilan write bir marta xato bersa, kamida stage_id yozilishi
    (task ko'rinishi to'g'ri yangilanishi) kafolatlanadi — ikkinchi urinishda."""
    attempts = []

    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "project.task" and method == "read":
            return [{"id": 9, "project_id": [39, "HR"]}]
        if model == "project.task.type" and method == "search_read":
            return [{"id": 242}]
        if model == "project.task" and method == "fields_get":
            return {"state": {"selection": _STATE_SELECTION}}
        if model == "project.task" and method == "write":
            attempts.append(dict(args[1]))
            if "state" in args[1] and len(attempts) == 1:
                raise RuntimeError("ValidationError: state maydoni to'g'ridan-to'g'ri yozib bo'lmaydi")
            return True
        raise AssertionError(f"unexpected call: {model}.{method}")

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    await client.update_task_stage(9, done=True)
    assert len(attempts) == 2
    assert attempts[0]["state"] == "1_done"
    assert "state" not in attempts[1]  # fallback — faqat stage_id
    assert attempts[1]["stage_id"] == 242


async def test_update_task_stage_missing_task_raises(client, monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        if model == "project.task" and method == "read":
            return []
        if model == "project.task" and method == "fields_get":
            # get_task va _get_task_state_meta PARALLEL bajariladi — ikkalasi ham
            # chaqiriladi, task None bo'lsa ham (natija baribir ishlatilmaydi).
            return {"state": {"selection": _STATE_SELECTION}}
        raise AssertionError(f"unexpected call: {model}.{method}")

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    with pytest.raises(OdooError):
        await client.update_task_stage(999, done=True)


async def test_task_state_value_matches_english_labels(client):
    """Odoo state field'i odatda inglizcha ('Done'/'Cancelled') — o'zbekcha
    label bo'lmasa ham to'g'ri qiymat topilishi shart (jonli Odoo'da tasdiqlangan)."""
    meta = {"state": {"selection": _STATE_SELECTION}}
    assert await client._task_state_value(meta, done=True) == "1_done"
    assert await client._task_state_value(meta, done=False) == "1_canceled"


# ── Tezlik: stage_id va fields_get keshlanishi ("buni tezlashtir" so'roviga javob) ──

async def test_ensure_stage_caches_result_no_repeat_search_read(client, monkeypatch):
    """Bir xil (project_id, stage_name) uchun IKKINCHI chaqiriqda Odoo'ga
    umuman so'rov ketmasligi shart — bu vazifa yaratish/yakunlashni
    sezilarli tezlashtiradi (har safar search_read/create o'rniga xotiradan)."""
    calls = []

    async def fake_execute_kw(model, method, args, kwargs=None):
        calls.append((model, method))
        if model == "project.task.type" and method == "search_read":
            return [{"id": 55}]
        raise AssertionError(f"unexpected call: {model}.{method}")

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    first = await client._ensure_stage(39, "Jarayonda", sequence=0)
    second = await client._ensure_stage(39, "Jarayonda", sequence=0)
    assert first == second == 55
    assert calls == [("project.task.type", "search_read")]  # faqat 1 marta


async def test_ensure_stage_cache_is_scoped_per_project_and_name(client, monkeypatch):
    """Kesh (project_id, stage_name) juftligiga xos — boshqa loyiha/bosqich
    nomi uchun alohida so'ralishi kerak (noto'g'ri xotira ishlatilmasin)."""
    async def fake_execute_kw(model, method, args, kwargs=None):
        domain = args[0]
        name = next(cond[2] for cond in domain if cond[0] == "name")
        return [{"id": 100}] if name == "Jarayonda" else [{"id": 200}]

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    a = await client._ensure_stage(39, "Jarayonda", sequence=0)
    b = await client._ensure_stage(39, "Bajarildi", sequence=10)
    c = await client._ensure_stage(40, "Jarayonda", sequence=0)  # boshqa loyiha
    assert a == 100
    assert b == 200
    assert c == 100  # xuddi "Jarayonda" nomi, lekin BOSHQA project_id — alohida kesh yozuvi


async def test_get_task_state_meta_cached_across_calls(client, monkeypatch):
    """fields_get — Odoo MODELINING sxemasi, vazifaga bog'liq emas — butun
    jarayon davomida FAQAT bir marta so'ralishi kerak."""
    calls = []

    async def fake_execute_kw(model, method, args, kwargs=None):
        calls.append((model, method))
        return {"state": {"selection": _STATE_SELECTION}}

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    m1 = await client._get_task_state_meta()
    m2 = await client._get_task_state_meta()
    assert m1 == m2
    assert calls == [("project.task", "fields_get")]  # faqat 1 marta


async def test_update_task_stage_second_call_same_project_skips_stage_lookup(client, monkeypatch):
    """Bitta loyihada ikkinchi vazifa yakunlanganda — stage_id va fields_get
    ALLAQACHON keshlangani uchun faqat get_task+write so'raladi (tezroq javob)."""
    calls = []

    async def fake_execute_kw(model, method, args, kwargs=None):
        calls.append((model, method))
        if model == "project.task" and method == "read":
            return [{"id": args[0][0], "project_id": [39, "HR"]}]
        if model == "project.task.type" and method == "search_read":
            return [{"id": 242}]
        if model == "project.task" and method == "fields_get":
            return {"state": {"selection": _STATE_SELECTION}}
        if model == "project.task" and method == "write":
            return True
        raise AssertionError(f"unexpected call: {model}.{method}")

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    await client.update_task_stage(1, done=True)
    calls.clear()
    await client.update_task_stage(2, done=True)
    # Ikkinchi chaqiriqda faqat get_task (task-xos) va write (yozuv) qoladi —
    # stage qidiruvi va fields_get kesh orqali o'tkazib yuboriladi.
    assert ("project.task", "read") in calls
    assert ("project.task", "write") in calls
    assert ("project.task.type", "search_read") not in calls
    assert ("project.task", "fields_get") not in calls


# ── XML-RPC timeout haqiqatan transport'ga o'rnatiladi ──────────────────────

def test_https_url_uses_safe_transport_with_timeout(client):
    transport = client._make_transport()
    assert isinstance(transport, _TimeoutSafeTransport)
    conn = transport.make_connection("test.odoo.example.com")
    assert conn.timeout == 7


def test_http_url_uses_plain_transport_with_timeout():
    c = OdooClient(url="http://internal-odoo:8069", db="d", login="l", api_key="k", timeout=15)
    transport = c._make_transport()
    assert isinstance(transport, _TimeoutTransport)
    conn = transport.make_connection("internal-odoo:8069")
    assert conn.timeout == 15


async def test_get_uid_wraps_authenticate_timeout_as_odoo_error(client):
    """authenticate() chaqiruvi socket.timeout (yoki boshqa OSError) bersa —
    OdooError'ga o'raladi, abadiy osilib qolish o'rniga aniq xato chiqadi."""
    class _FakeCommonProxy:
        def authenticate(self, *a, **kw):
            raise TimeoutError("timed out")

    client._local.common_proxy = _FakeCommonProxy()
    with pytest.raises(OdooError, match="timeout"):
        client._get_uid()


async def test_execute_kw_wraps_object_proxy_timeout_as_odoo_error(client):
    """UID allaqachon bor (auth muammosi emas) holatda ham, execute_kw() ning
    o'zi (masalan search_read paytida) timeout bersa — OdooError'ga o'raladi."""
    client._local.uid = 1  # autentifikatsiya allaqachon muvaffaqiyatli bo'lgan deb hisoblaymiz

    class _FakeObjectProxy:
        def execute_kw(self, *a, **kw):
            raise TimeoutError("timed out")

    client._local.object_proxy = _FakeObjectProxy()
    with pytest.raises(OdooError, match="timeout"):
        client._execute_kw_sync("project.task", "search_count", [[]])
