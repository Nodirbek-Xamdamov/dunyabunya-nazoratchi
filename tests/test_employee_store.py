"""Fayl-asosli hodimlar katalogi (`app/employee_store.py`) uchun testlar.

Postgres `employees` jadvali o'rniga JSON faylga o'tkazilgan qidiruv/yozish
logikasi tekshiriladi: ism bo'yicha substring moslik avvalgi SQL ILIKE
xulq-atvori bilan bir xil bo'lishi, yozishlar diskka saqlanishi va jarayon
qayta ishga tushganda (fayldan qayta yuklanganda) ma'lumot yo'qolmasligi shart.
Barcha testlar vaqtinchalik faylga yoziladi — haqiqiy storage/employees.json
teginilmaydi.
"""
from __future__ import annotations

import pytest

from app import employee_store


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Har test o'z vaqtinchalik faylida ishlaydi, modul holati tozalanadi."""
    monkeypatch.setattr(employee_store, "_PATH", tmp_path / "employees.json")
    employee_store._employees = []
    employee_store._by_telegram_id = {}
    employee_store._next_id = 1
    employee_store._loaded = False
    yield
    employee_store._employees = []
    employee_store._by_telegram_id = {}
    employee_store._next_id = 1
    employee_store._loaded = False


async def test_seed_from_odoo_adds_new_and_skips_existing():
    users = [{"id": 1, "name": "Nodir Xamdamov"}, {"id": 2, "name": "Aziz Karimov"}]
    added = await employee_store.seed_employees_from_odoo(users)
    assert added == 2

    added_again = await employee_store.seed_employees_from_odoo(users)
    assert added_again == 0
    assert len(employee_store._employees) == 2


async def test_get_employee_by_name_substring_case_insensitive():
    await employee_store.seed_employees_from_odoo(
        [{"id": 1, "name": "Nodir Xamdamov"}, {"id": 2, "name": "Aziz Karimov"}]
    )
    emp = await employee_store.get_employee_by_name("nodir")
    assert emp is not None
    assert emp.odoo_user_id == 1


async def test_get_employee_by_name_ambiguous_returns_none():
    await employee_store.seed_employees_from_odoo(
        [{"id": 1, "name": "Nodir Xamdamov"}, {"id": 2, "name": "Nodirbek Aliyev"}]
    )
    # "Nodir" ikkalasiga ham mos keladi -> aniq bitta nomzod yo'q
    assert await employee_store.get_employee_by_name("Nodir") is None
    matches = await employee_store.get_employees_by_name("Nodir")
    assert len(matches) == 2


async def test_name_suffix_stripped_before_search():
    """'Nodirga' so'rovi ham 'Nodir Xamdamov'ni topishi kerak (name_utils orqali)."""
    await employee_store.seed_employees_from_odoo([{"id": 1, "name": "Nodir Xamdamov"}])
    emp = await employee_store.get_employee_by_name("Nodirga")
    assert emp is not None
    assert emp.odoo_user_id == 1


async def test_upsert_and_get_by_telegram_id_round_trip():
    emp = await employee_store.upsert_employee(
        telegram_user_id=555,
        telegram_username="tester",
        full_name="Test User",
        odoo_user_id=10,
        odoo_user_name="Test User",
    )
    assert emp.telegram_user_id == 555

    found = await employee_store.get_employee_by_telegram_id(555)
    assert found is not None
    assert found.odoo_user_id == 10

    found_by_odoo = await employee_store.get_employee_by_odoo_user_id(10)
    assert found_by_odoo is not None
    assert found_by_odoo.telegram_user_id == 555


async def test_unlink_clears_telegram_fields_but_keeps_catalog_entry():
    await employee_store.upsert_employee(
        telegram_user_id=555, telegram_username="tester", full_name="Test User",
        odoo_user_id=10, odoo_user_name="Test User",
    )
    unlinked = await employee_store.unlink_employee(555)
    assert unlinked is not None
    assert unlinked.telegram_user_id is None
    assert unlinked.is_active is False

    assert await employee_store.get_employee_by_telegram_id(555) is None
    # Odoo katalog yozuvi o'chirilmagan, faqat bog'lanish uzilgan
    assert await employee_store.get_employee_by_odoo_user_id(10) is None


async def test_unlink_unknown_telegram_id_returns_none():
    assert await employee_store.unlink_employee(999999) is None


async def test_writes_persist_across_reload(tmp_path):
    await employee_store.seed_employees_from_odoo([{"id": 1, "name": "Nodir Xamdamov"}])
    await employee_store.upsert_employee(
        telegram_user_id=555, telegram_username="tester", full_name="Test User",
        odoo_user_id=1, odoo_user_name="Nodir Xamdamov",
    )

    # Jarayon qayta ishga tushganini simulyatsiya qilamiz: xotirani tozalab,
    # faqat diskdagi fayldan qayta yuklaymiz.
    employee_store._employees = []
    employee_store._by_telegram_id = {}
    employee_store._next_id = 1
    employee_store._loaded = False

    found = await employee_store.get_employee_by_telegram_id(555)
    assert found is not None
    assert found.full_name == "Test User"


async def test_reload_reads_utf8_names_regardless_of_system_locale(tmp_path):
    """Regressiya: `_load()` avval encoding ko'rsatmasdan `read_text()` chaqirardi
    — bu Windows'da tizim kodировкаси (masalan cp1251)dan foydalanadi, UTF-8
    emas, va o'zbekcha apostrofli ismlarda (masalan "O'g'li", U+2018)
    UnicodeDecodeError bilan BUTUN katalogni yuklab bo'lmasdi."""
    await employee_store.upsert_employee(
        telegram_user_id=777, telegram_username="tester",
        full_name="ABDIYEV MUROD MIRSOBID O‘G‘LI",
        odoo_user_id=1, odoo_user_name="ABDIYEV MUROD MIRSOBID O‘G‘LI",
    )

    employee_store._employees = []
    employee_store._by_telegram_id = {}
    employee_store._next_id = 1
    employee_store._loaded = False

    found = await employee_store.get_employee_by_telegram_id(777)
    assert found is not None
    assert found.full_name == "ABDIYEV MUROD MIRSOBID O‘G‘LI"


async def test_find_active_employee_by_name_requires_telegram_registration():
    """Odoo bog'lanishi (odoo_user_id) bo'lmasa ham, agar hodim botda /start
    bosgan (telegram_user_id bor) bo'lsa, ismi bo'yicha topilishi kerak —
    bu bildirishnomani Odoo login holatidan mustaqil yetkazish uchun ishlatiladi."""
    # Faqat Odoo katalogda bor, botda RO'YXATDAN O'TMAGAN (telegram_user_id yo'q)
    await employee_store.seed_employees_from_odoo([{"id": 1, "name": "Botir Yusupov"}])
    assert await employee_store.find_active_employee_by_name("Botir") is None

    # Botda ro'yxatdan o'tgan, lekin Odoo login/hr.employee bog'lanishi umuman yo'q
    await employee_store.upsert_employee(
        telegram_user_id=888, telegram_username="nodira", full_name="Nodira Qosimova",
    )
    found = await employee_store.find_active_employee_by_name("Nodira")
    assert found is not None
    assert found.telegram_user_id == 888


async def test_find_active_employee_by_name_ambiguous_returns_none():
    await employee_store.upsert_employee(telegram_user_id=1, telegram_username="a", full_name="Nodir Aliyev")
    await employee_store.upsert_employee(telegram_user_id=2, telegram_username="b", full_name="Nodirbek Karimov")
    assert await employee_store.find_active_employee_by_name("Nodir") is None
