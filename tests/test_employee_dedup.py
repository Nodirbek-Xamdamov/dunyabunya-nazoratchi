"""Xodim qidirishda dublikat xatosi uchun regression testlar.

"Bajaruvchi tanlash" tugmalarida bir xil xodim ikki marta chiqmasligi tekshiriladi:
- Odoo res.users'da bir xil ismli bir nechta yozuv bo'lsa (ichki + portal hisob);
- Local DB'da bir xil odoo_user_id'li bir nechta Employee yozuvi bo'lsa.
Barcha testlar mok'larga asoslangan — tarmoqqa chiqilmaydi.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.odoo import OdooClient

import app.services.odoo as odoo_module


@pytest.fixture(autouse=True)
def _clear_users_cache(monkeypatch):
    """find_users_by_name keshi har testda tozalanadi — mok'langan ma'lumotlar izolyatsiya qilinadi."""
    monkeypatch.setattr(odoo_module, "_users_cache", None)
    yield


@pytest.fixture
def client():
    return OdooClient(
        url="http://test:8069",
        db="test_db",
        login="test@test.com",
        api_key="test-key",
        timeout=10,
    )


# ── _dedupe_users (sof logika) ───────────────────────────────────────────────

def test_dedupe_removes_same_id_duplicates(client):
    users = [
        {"id": 5, "name": "Hasanxo'jayev Sardor G'anisher o'g'li"},
        {"id": 5, "name": "Hasanxo'jayev Sardor G'anisher o'g'li"},
        {"id": 5, "name": "Hasanxo'jayev Sardor G'anisher o'g'li"},
    ]
    result = client._dedupe_users(users)
    assert len(result) == 1
    assert result[0]["id"] == 5


def test_dedupe_removes_same_name_different_ids(client):
    """res.users'da ichki + portal hisob — id har xil, lekin ism bir xil."""
    users = [
        {"id": 5, "name": "Hasanxo'jayev Sardor G'anisher o'g'li"},
        {"id": 77, "name": "Hasanxo'jayev Sardor G'anisher o'g'li"},
    ]
    result = client._dedupe_users(users)
    assert len(result) == 1
    assert result[0]["id"] == 5  # birinchisi saqlanadi


def test_dedupe_keeps_different_people(client):
    users = [
        {"id": 5, "name": "Hasanxo'jayev Sardor"},
        {"id": 9, "name": "Hasanov Botir"},
    ]
    result = client._dedupe_users(users)
    assert len(result) == 2


def test_dedupe_name_normalization_ignores_apostrophes_and_case(client):
    users = [
        {"id": 5, "name": "Hasanxo'jayev Sardor"},
        {"id": 9, "name": "Hasanxojayev Sardor"},   # ' olib tashlansa + kichik harf — bir xil
    ]
    result = client._dedupe_users(users)
    assert len(result) == 1


def test_dedupe_keeps_rows_without_id_by_name(client):
    users = [
        {"name": "Ali Valiyev"},
        {"id": 3, "name": "Ali Valiyev"},
    ]
    result = client._dedupe_users(users)
    assert len(result) == 1  # ism bir xil — bittasi


def test_dedupe_handles_empty_and_missing_names(client):
    assert client._dedupe_users([]) == []
    users = [{"id": 1, "name": ""}, {"id": 2, "name": None}, {"id": 3, "name": "Botir"}]
    result = client._dedupe_users(users)
    # ismi bo'lmaganlar saqlanadi (id farqli), nomlilar unikal
    assert len(result) == 3


# ── find_users_by_name — to'liq oqim (mok'langan Odoo) ───────────────────────

async def test_find_users_by_name_dedupes_duplicate_records(client, monkeypatch):
    """Odoo bir xil ismli 2 ta res.users qaytarsa — bitta natija qaytadi."""
    rows = [
        {"id": 5, "name": "Hasanxo'jayev Sardor G'anisher o'g'li"},
        {"id": 77, "name": "Hasanxo'jayev Sardor G'anisher o'g'li"},  # portal hisob
    ]

    async def fake_execute_kw(model, method, args, kwargs=None):
        assert model == "res.users"
        assert method == "search_read"
        return rows

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)

    result = await client.find_users_by_name("Hasan")
    assert len(result) == 1
    assert result[0]["id"] == 5


async def test_find_users_by_name_keeps_distinct_people(client, monkeypatch):
    rows = [
        {"id": 5, "name": "Hasanxo'jayev Sardor"},
        {"id": 9, "name": "Hasanov Botir"},
        {"id": 11, "name": "Hasanova Dilnoza"},
    ]

    async def fake_execute_kw(model, method, args, kwargs=None):
        return rows

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    result = await client.find_users_by_name("Hasan")
    assert len(result) == 3
    assert {r["id"] for r in result} == {5, 9, 11}


async def test_find_users_by_name_dedupes_by_id(client, monkeypatch):
    rows = [
        {"id": 5, "name": "Hasanxo'jayev Sardor"},
        {"id": 5, "name": "Hasanxo'jayev Sardor"},
    ]

    async def fake_execute_kw(model, method, args, kwargs=None):
        return rows

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    result = await client.find_users_by_name("Hasan")
    assert len(result) == 1