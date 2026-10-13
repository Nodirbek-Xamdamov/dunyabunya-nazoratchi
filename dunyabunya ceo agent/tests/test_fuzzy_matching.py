"""Fuzzy (Similarity) ism qidiruv uchun testlar.

RapidFuzz token_set_ratio/partial_ratio asosida xodim topish logikasi tekshiriladi:
- score >= 75 chegara;
- kelishik/erkalash qo'shimchalari ("Sardorbekka" -> "sardor") chidamliligi;
- ball bo'yicha saralash;
- res.users keshi (TTL) — takroriy Odoo so'rovi bo'lmasligi;
- difflib fallback (rapidfuzz o'rnatilmaganida ham ishlaydi).
Barcha testlar mok'larga asoslangan — tarmoqqa chiqilmaydi.
"""
from __future__ import annotations

import pytest

import app.services.odoo as odoo_module
from app.services.odoo import OdooClient


@pytest.fixture(autouse=True)
def _clear_users_cache(monkeypatch):
    """Kesh har testda tozalanadi — testlar bir-biriga bog'liq bo'lmaydi."""
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


USERS = [
    {"id": 2, "name": "Hasanxo'jayev Sardor G`anisher o`g`li"},
    {"id": 10, "name": "Sardorbek"},
    {"id": 20, "name": "Aziz Karimov"},
    {"id": 30, "name": "Nodir Xamdamov"},
]


async def test_find_users_fuzzy_with_suffix_variant(client, monkeypatch):
    """'Sardorbekka' so'rovi ham 'Sardorbek'ni, ham 'Hasanxo'jayev Sardor'ni topadi."""
    async def fake_execute_kw(model, method, args, kwargs=None):
        return USERS

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    result = await client.find_users_by_name("Sardorbekka")
    names = [r["name"] for r in result]
    assert "Sardorbek" in names
    assert "Hasanxo'jayev Sardor G`anisher o`g`li" in names
    assert all(r["score"] >= 75 for r in result)
    # Ball kamayish tartibida
    scores = [r["score"] for r in result]
    assert scores == sorted(scores, reverse=True)


async def test_find_users_fuzzy_simple_query(client, monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        return USERS

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    result = await client.find_users_by_name("Nodir")
    assert [r["name"] for r in result] == ["Nodir Xamdamov"]
    assert result[0]["score"] == 100.0


async def test_find_users_fuzzy_threshold_excludes_weak_matches(client, monkeypatch):
    async def fake_execute_kw(model, method, args, kwargs=None):
        return USERS

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    # "Aziz" bilan bog'liq bo'lmagan so'rov — topilmasligi kerak
    result = await client.find_users_by_name("Xatirjon")
    assert result == []


async def test_find_users_sorted_and_scored(client, monkeypatch):
    rows = [
        {"id": 1, "name": "Sardorbek"},
        {"id": 2, "name": "Sardorbek Aliyev"},
        {"id": 3, "name": "Botir Sardorov"},
    ]

    async def fake_execute_kw(model, method, args, kwargs=None):
        return rows

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    result = await client.find_users_by_name("Sardorbek")
    assert result  # hech bo'lmaganda bittasi topiladi
    assert all("score" in r for r in result)
    scores = [r["score"] for r in result]
    assert scores == sorted(scores, reverse=True)


async def test_users_cache_avoids_repeated_odoo_calls(client, monkeypatch):
    counter = {"calls": 0}

    async def fake_execute_kw(model, method, args, kwargs=None):
        counter["calls"] += 1
        return USERS

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)

    await client.find_users_by_name("Nodir")
    await client.find_users_by_name("Sardor")
    await client.find_users_by_name("Aziz")
    # 3 qidiruv — lekin Odoo'ga faqat 1 marta so'rov (kesh tufayli)
    assert counter["calls"] == 1


async def test_difflib_fallback_works_without_rapidfuzz(client, monkeypatch):
    """rapidfuzz o'rnatilmagan bo'lsa ham (difflib) topish ishlaydi."""
    monkeypatch.setattr(odoo_module, "_rapidfuzz_fuzz", None)

    async def fake_execute_kw(model, method, args, kwargs=None):
        return USERS

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    result = await client.find_users_by_name("Sardorbekka")
    names = [r["name"] for r in result]
    assert "Sardorbek" in names
    assert all(r["score"] >= 75 for r in result)


def test_similarity_score_suffix_stripped_inputs(client):
    """Qo'shimchalari tozalangan so'zlar o'rtasida yuqori o'xshashlik."""
    s = client._similarity_score("sardor", "sardor")
    assert s >= 90
    s2 = client._similarity_score("sardorbek", "sardor")
    assert s2 >= 75  # qisman moslik ham yetarli


def test_similarity_score_unrelated_low(client):
    assert client._similarity_score("aziz", "ganisher") < 75


# ── Fonetik yozilish farqlari ("Murat" -> "Murod") ──────────────────────────
# Bular ikkita talaffuz/imlo varianti (turkiy "Murat" vs o'zbekcha "Murod") —
# umumiy qo'shimcha/substring yo'q, faqat token_set/partial_ratio fonetik
# yaqinligi orqali topiladi. Jonli RapidFuzz bilan tasdiqlangan: partial_ratio
# roppa-rosa 75.0 (chegaraning o'zida) — shuning uchun bu holat aynan shu
# aniqlik bilan qulflab qo'yilgan regressiya testi sifatida saqlanadi; agar
# kelajakda RapidFuzz versiyasi yoki chegara o'zgarib bu test qizarsa, bu ataylab
# emas — chegarani yoki fonetik normalizatsiyani qayta ko'rib chiqish kerak bo'ladi.
async def test_find_users_phonetic_variant_murat_matches_murod(client, monkeypatch):
    rows = [{"id": 1, "name": "Murod"}]

    async def fake_execute_kw(model, method, args, kwargs=None):
        return rows

    monkeypatch.setattr(client, "execute_kw", fake_execute_kw)
    result = await client.find_users_by_name("Murat")
    assert any(r["id"] == 1 for r in result)


def test_similarity_score_murat_murod_at_threshold(client):
    score = client._similarity_score("murat", "murod")
    assert score >= 75.0