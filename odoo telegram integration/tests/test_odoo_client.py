"""app.odoo_client XML-RPC wrapper funksiyalarini sinaydi. Haqiqiy Odoo'ga so'rov
ketmasligi uchun xmlrpc.client.ServerProxy monkeypatch orqali soxtalashtiriladi."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

import app.config as config_module
import app.odoo_client as odoo_client_module
from app.odoo_client import (
    TELEGRAM_ORDER_CRM_STAGE_ID,
    TELEGRAM_ORDER_CRM_TEAM_ID,
    TELEGRAM_ORDER_UTM_SOURCE_ID,
    create_ir_attachment,
    get_partner_manager,
    post_object_edit_chatter_message,
    update_object_contact,
    create_telegram_order_lead,
    find_partner_by_chat_id,
    get_customer_objects,
    get_monthly_statistics,
    get_quotations,
    set_object_location,
)


@pytest.fixture(autouse=True)
def _reset_odoo_client_cache():
    """2026-08-03: uid va ServerProxy'lar endi keshlanadi (tezlik uchun) -- bu keshlar
    modul darajasida/thread-local, shu sabab testlar orasida avtomatik tozalanmaydi.
    Har bir test o'zining fake proxy'lari bilan boshlanishi kerak, aks holda oldingi
    testda keshlangan uid/proxy keyingi testning yangi monkeypatch'ini chetlab o'tib
    qoladi."""
    odoo_client_module._cached_uid = None
    odoo_client_module._local.__dict__.clear()
    yield
    odoo_client_module._cached_uid = None
    odoo_client_module._local.__dict__.clear()


def _configure(monkeypatch):
    monkeypatch.setattr(config_module.settings, "ODOO_URL", "https://example.odoo.com")
    monkeypatch.setattr(config_module.settings, "ODOO_DB", "example_db")
    monkeypatch.setattr(config_module.settings, "ODOO_USERNAME", "admin@example.com")
    monkeypatch.setattr(config_module.settings, "ODOO_API_KEY", "secret-key")


def _install_fake_proxies(monkeypatch, *, uid=2, execute_kw_side_effect=None):
    common_proxy = MagicMock()
    common_proxy.authenticate.return_value = uid

    object_proxy = MagicMock()
    if execute_kw_side_effect is not None:
        object_proxy.execute_kw.side_effect = execute_kw_side_effect

    def fake_server_proxy(url):
        return common_proxy if url.endswith("/xmlrpc/2/common") else object_proxy

    monkeypatch.setattr(odoo_client_module.xmlrpc.client, "ServerProxy", fake_server_proxy)
    return common_proxy, object_proxy


def test_find_partner_returns_none_when_not_configured(monkeypatch):
    monkeypatch.setattr(config_module.settings, "ODOO_URL", None)
    assert find_partner_by_chat_id(123) is None


def test_find_partner_returns_none_when_authentication_fails(monkeypatch):
    _configure(monkeypatch)
    _install_fake_proxies(monkeypatch, uid=False)
    assert find_partner_by_chat_id(123) is None


def test_find_partner_by_chat_id_success(monkeypatch):
    """2026-08-03: tezlik uchun search()+read() bitta search_read() chaqiruviga
    birlashtirilgan -- foydalanuvchi Malumotlarim bo'limi 4-5 soniya ishlayapti deb
    shikoyat qilgach, Odoo'ga borish-kelish sonini kamaytirish uchun."""
    _configure(monkeypatch)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        if model == "res.partner" and method == "search_read":
            assert args[0] == [["x_telegram_id", "=", "233157033"]]
            assert kwargs["limit"] == 1
            return [{"id": 72120, "name": "SHAXNOZA OPA", "x_uzs_balance": 0.0, "x_usd_balance": 0.0}]
        raise AssertionError(f"unexpected call: {model}.{method}")

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)

    result = find_partner_by_chat_id(233157033)
    assert result == {"id": 72120, "name": "SHAXNOZA OPA", "x_uzs_balance": 0.0, "x_usd_balance": 0.0}


def test_find_partner_returns_none_when_no_match(monkeypatch):
    _configure(monkeypatch)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        if method == "search_read":
            return []
        raise AssertionError(f"unexpected call: {method}")

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    assert find_partner_by_chat_id(999) is None


def test_get_customer_objects_filters_by_delivery_type(monkeypatch):
    """2026-08-03: tezlik uchun search()+read() bitta search_read() chaqiruviga
    birlashtirilgan (find_partner_by_chat_id'dagi izohga qarang)."""
    _configure(monkeypatch)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        if method == "search_read":
            assert args[0] == [["parent_id", "=", 67638], ["type", "=", "delivery"]]
            assert "partner_latitude" in kwargs["fields"]
            assert "partner_longitude" in kwargs["fields"]
            assert "phone" in kwargs["fields"]
            assert "mobile" in kwargs["fields"]
            return [
                {
                    "id": 77585, "name": "NAZARBEK", "phone": "+998991234567", "mobile": False,
                    "x_uzs_balance": 0.0, "x_usd_balance": 0.0,
                    "partner_latitude": 0.0, "partner_longitude": 0.0,
                },
                {
                    "id": 77584, "name": "NURAFSHON", "phone": False, "mobile": False,
                    "x_uzs_balance": 0.0, "x_usd_balance": 0.0,
                    "partner_latitude": 41.311, "partner_longitude": 69.279,
                },
            ]
        raise AssertionError(f"unexpected call: {method}")

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    objects = get_customer_objects(67638)
    assert [o["name"] for o in objects] == ["NAZARBEK", "NURAFSHON"]
    assert objects[1]["partner_latitude"] == 41.311
    assert objects[0]["phone"] == "+998991234567"


def test_set_object_location_writes_lat_long(monkeypatch):
    _configure(monkeypatch)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        assert model == "res.partner"
        assert method == "write"
        assert args[0] == [77585]
        assert args[1] == {"partner_latitude": 41.311, "partner_longitude": 69.279}
        return True

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    assert set_object_location(77585, 41.311, 69.279) is True


def test_set_object_location_returns_false_on_failure(monkeypatch):
    _configure(monkeypatch)

    def execute_kw(*args, **kwargs):
        raise ConnectionError("boom")

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    assert set_object_location(77585, 41.311, 69.279) is False


def test_get_quotations_returns_empty_for_no_partner_ids(monkeypatch):
    _configure(monkeypatch)
    assert get_quotations([]) == []


def test_get_quotations_queries_draft_and_sent(monkeypatch):
    _configure(monkeypatch)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        assert model == "sale.order"
        assert args[0] == [["partner_id", "in", [1, 2]], ["state", "in", ["draft", "sent"]]]
        return [{"name": "S00001", "amount_total": 100.0, "date_order": "2026-01-01 00:00:00"}]

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = get_quotations([1, 2])
    assert result[0]["name"] == "S00001"


def test_execute_returns_none_on_exception(monkeypatch):
    _configure(monkeypatch)

    def execute_kw(*args, **kwargs):
        raise ConnectionError("boom")

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    assert find_partner_by_chat_id(1) is None


def test_execute_authenticates_only_once_across_multiple_calls(monkeypatch):
    """2026-08-03: uid keshlanishi kerak -- ikkita mustaqil yuqori darajali chaqiruv
    (ikkalasi ham _execute'ga tayanadi) atigi BITTA marta authenticate() chaqirishi
    kerak, avvalgidek har safar emas."""
    _configure(monkeypatch)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        return True

    common_proxy, _ = _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)

    set_object_location(1, 1.0, 2.0)
    set_object_location(2, 3.0, 4.0)

    assert common_proxy.authenticate.call_count == 1


def test_execute_reuses_server_proxy_across_calls(monkeypatch):
    """ServerProxy konstruktori har CHAQIRUVDA emas, thread boshiga bir marta (common
    uchun bittasi, object uchun bittasi) chaqirilishi kerak -- ulanishni qayta ochish
    xarajatini kamaytirish uchun (2026-08-03)."""
    _configure(monkeypatch)

    proxy_creation_count = {"n": 0}
    common_proxy = MagicMock()
    common_proxy.authenticate.return_value = 2
    object_proxy = MagicMock()
    object_proxy.execute_kw.return_value = True

    def fake_server_proxy(url):
        proxy_creation_count["n"] += 1
        return common_proxy if url.endswith("/xmlrpc/2/common") else object_proxy

    monkeypatch.setattr(odoo_client_module.xmlrpc.client, "ServerProxy", fake_server_proxy)

    set_object_location(1, 1.0, 2.0)
    set_object_location(2, 3.0, 4.0)

    assert proxy_creation_count["n"] == 2


def test_execute_retries_once_after_stale_uid_failure(monkeypatch):
    """Keshlangan uid eskirgan/yaroqsiz bo'lib qolgan holatni simulyatsiya qiladi --
    execute_kw birinchi urinishda xato bersa, _execute majburiy qayta autentifikatsiya
    qilib so'rovni BIR MARTA qayta urinib ko'rishi va muvaffaqiyatli natija bilan
    qaytishi kerak (2026-08-03)."""
    _configure(monkeypatch)

    call_count = {"n": 0}

    def execute_kw(db, uid, key, model, method, args, kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception("stale uid, unauthorized")
        return True

    common_proxy, _ = _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)

    result = set_object_location(77585, 41.311, 69.279)

    assert result is True
    assert call_count["n"] == 2
    assert common_proxy.authenticate.call_count == 2


def test_execute_returns_none_when_retry_also_fails(monkeypatch):
    """Qayta urinish ham muvaffaqiyatsiz bo'lsa (masalan haqiqiy tarmoq xatosi, uid bilan
    aloqasi yo'q), oldingi xatti-harakatga mos ravishda None qaytishi kerak (cheksiz
    qayta urinish YO'Q -- atigi bitta qayta urinish)."""
    _configure(monkeypatch)

    def execute_kw(*args, **kwargs):
        raise ConnectionError("boom")

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)

    assert set_object_location(1, 1.0, 2.0) is False


def test_create_telegram_order_lead_with_object(monkeypatch):
    _configure(monkeypatch)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        assert model == "crm.lead"
        assert method == "create"
        vals = args[0]
        assert vals["partner_id"] == 42
        assert vals["name"] == "Telegram buyurtma - Test Partner"
        assert vals["description"] == "2 dona sement kerak"
        assert vals["x_telegram_chat_id"] == "1"
        assert vals["x_telegram_object_name"] == "NAZARBEK"
        assert "x_telegram_latitude" not in vals
        assert "x_telegram_longitude" not in vals
        assert vals["team_id"] == TELEGRAM_ORDER_CRM_TEAM_ID
        assert vals["stage_id"] == TELEGRAM_ORDER_CRM_STAGE_ID
        assert vals["source_id"] == TELEGRAM_ORDER_UTM_SOURCE_ID
        return 555

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = create_telegram_order_lead(
        partner_id=42, partner_name="Test Partner", chat_id=1, order_details="2 dona sement kerak", object_name="NAZARBEK"
    )
    assert result == 555


def test_create_telegram_order_lead_with_location(monkeypatch):
    _configure(monkeypatch)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        vals = args[0]
        assert vals["x_telegram_latitude"] == 41.311
        assert vals["x_telegram_longitude"] == 69.279
        assert "x_telegram_object_name" not in vals
        return 556

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = create_telegram_order_lead(
        partner_id=42,
        partner_name="Test Partner",
        chat_id=1,
        order_details="lokatsiyaga yetkazib bering",
        latitude=41.311,
        longitude=69.279,
    )
    assert result == 556


def test_create_telegram_order_lead_existing_partner_with_phone_appends_contact_line(monkeypatch):
    """2026-08-03: MAVJUD (partner_id bilan) lead'larda Odoo `name` maydonini description
    matnining BOSHIDAGI qismidan avtomatik yozib qo'yadi (live tekshirilgan) -- shu sabab
    qabul qiluvchi ismi/telefoni description OXIRIGA qo'shiladi (boshiga emas), aks holda
    lead sarlavhasi haqiqiy buyurtma matni o'rniga "☎️ Aloqa: ..." bilan boshlanib qolardi.
    Bu ayniqsa "Yangi obyekt yaratish" oqimida muhim -- boshqa menejer nomi/raqami
    kiritilgan bo'lishi mumkin, partner_id orqali ko'rinadigan standart ma'lumot noto'g'ri
    bo'lib qoladi."""
    _configure(monkeypatch)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        vals = args[0]
        assert vals["partner_id"] == 42
        assert vals["description"] == "2 dona sement kerak\n\n☎️ Aloqa: Shiribom menejeri, +998991112233"
        return 558

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = create_telegram_order_lead(
        partner_id=42,
        partner_name="Shiribom menejeri",
        chat_id=1,
        order_details="2 dona sement kerak",
        phone="+998991112233",
    )
    assert result == 558


def test_create_telegram_order_lead_new_customer_uses_contact_name_and_phone(monkeypatch):
    """2026-08-02: partner_id=None (Odoo'da topilmagan, butunlay yangi mijoz) bo'lsa,
    lead partner'ga bog'lanmaydi -- o'rniga standart contact_name/phone maydonlariga
    to'g'ridan-to'g'ri yoziladi (fields_get + haqiqiy create/read/unlink orqali
    tasdiqlangan, custom field emas)."""
    _configure(monkeypatch)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        vals = args[0]
        assert "partner_id" not in vals
        assert vals["contact_name"] == "Yangi Mijoz"
        assert vals["phone"] == "+998900000000"
        assert vals["name"] == "Telegram buyurtma - Yangi Mijoz"
        return 557

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = create_telegram_order_lead(
        partner_id=None,
        partner_name="Yangi Mijoz",
        chat_id=1,
        order_details="2 dona sement kerak",
        phone="+998900000000",
    )
    assert result == 557


def test_create_ir_attachment_writes_expected_fields(monkeypatch):
    """2026-08-03: rasm/video/ovozli xabar endi URL-havola sifatida description'ga emas,
    HAQIQIY ir.attachment sifatida crm.lead'ga biriktiriladi -- Odoo'ning o'z
    "Attachments" bo'limida ko'rinadi (live create/read + message_attachment_count
    orqali tasdiqlangan, custom field emas)."""
    _configure(monkeypatch)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        assert model == "ir.attachment"
        assert method == "create"
        vals = args[0]
        assert vals["name"] == "1_777.jpg"
        assert vals["datas"] == "ZmFrZWRhdGE="
        assert vals["res_model"] == "crm.lead"
        assert vals["res_id"] == 99
        assert vals["mimetype"] == "image/jpeg"
        return 4242

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = create_ir_attachment(lead_id=99, filename="1_777.jpg", base64_data="ZmFrZWRhdGE=", mimetype="image/jpeg")
    assert result == 4242


def test_create_ir_attachment_returns_none_on_failure(monkeypatch):
    _configure(monkeypatch)

    def execute_kw(*args, **kwargs):
        raise ConnectionError("boom")

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = create_ir_attachment(lead_id=99, filename="1_777.jpg", base64_data="ZmFrZWRhdGE=", mimetype="image/jpeg")
    assert result is None


def test_update_object_contact_writes_phone_only(monkeypatch):
    """2026-08-05: "Ma'lumotlarni tahrirlash" oqimidan telefon raqami res.partner'ga
    to'g'ridan-to'g'ri yoziladi -- live-verified haqiqiy test obyektida (yozib-o'qib-
    qaytarib tekshirilgan, API foydalanuvchisi write huquqiga ega)."""
    _configure(monkeypatch)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        assert model == "res.partner"
        assert method == "write"
        assert args[0] == [80505]
        assert args[1] == {"phone": "+998900000111"}
        return True

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = update_object_contact(80505, phone="+998900000111")
    assert result is True


def test_update_object_contact_writes_location_only(monkeypatch):
    _configure(monkeypatch)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        assert model == "res.partner"
        assert method == "write"
        assert args[0] == [80505]
        assert args[1] == {"partner_latitude": 41.3, "partner_longitude": 69.2}
        return True

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = update_object_contact(80505, latitude=41.3, longitude=69.2)
    assert result is True


def test_update_object_contact_returns_false_when_nothing_to_write():
    """Hech qanday maydon berilmasa (phone=None, latitude/longitude=None) Odoo'ga
    umuman so'rov yubormasdan False qaytaradi."""
    result = update_object_contact(80505)
    assert result is False


def test_update_object_contact_returns_false_on_failure(monkeypatch):
    _configure(monkeypatch)

    def execute_kw(*args, **kwargs):
        raise ConnectionError("boom")

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = update_object_contact(80505, phone="+998900000111")
    assert result is False


def test_post_object_edit_chatter_message_calls_message_post(monkeypatch):
    """2026-08-06, mijoz aniq so'ragan: Odoo xodimi obyektni ochganda o'zgarishni
    darhol ko'rishi uchun, res.partner'ning o'z "chatter"iga xabar yoziladi.
    MUHIM: body KWARG sifatida uzatiladi, positional dict sifatida EMAS -- live
    tekshiruvda "message_post() takes 1 positional argument but 2 were given"
    xatosi shu sabab chiqqan edi (positional bo'lgan holatda)."""
    _configure(monkeypatch)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        assert model == "res.partner"
        assert method == "message_post"
        assert args == [[80505]]
        assert kwargs == {"body": "Test xabar"}
        return 3213331

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = post_object_edit_chatter_message(80505, "Test xabar")
    assert result is True


def test_post_object_edit_chatter_message_returns_false_on_failure(monkeypatch):
    _configure(monkeypatch)

    def execute_kw(*args, **kwargs):
        raise ConnectionError("boom")

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = post_object_edit_chatter_message(80505, "Test xabar")
    assert result is False


def test_get_partner_manager_returns_name_and_phone(monkeypatch):
    """2026-08-06: res.partner.user_id -> res.users.phone (standart Odoo maydonlari,
    custom emas) -- live-verified real test mijozida (partner 80494 -> user_id=2)."""
    _configure(monkeypatch)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        assert method == "read"
        if model == "res.partner":
            assert args[0] == [80494]
            return [{"id": 80494, "user_id": [2, "Hasanxo'jayev Sardor"]}]
        if model == "res.users":
            assert args[0] == [2]
            return [{"id": 2, "name": "Hasanxo'jayev Sardor", "phone": "+998 90 970 28 58", "mobile": False}]
        raise AssertionError(f"unexpected model: {model}")

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = get_partner_manager(80494)
    assert result == {"name": "Hasanxo'jayev Sardor", "phone": "+998 90 970 28 58"}


def test_get_partner_manager_returns_none_when_no_user_id(monkeypatch):
    """user_id Odoo'da o'rnatilmagan (False) bo'lsa -- res.users so'ralmasdan None qaytadi."""
    _configure(monkeypatch)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        assert model == "res.partner"
        return [{"id": 80494, "user_id": False}]

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = get_partner_manager(80494)
    assert result is None


def test_get_partner_manager_falls_back_to_mobile(monkeypatch):
    _configure(monkeypatch)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        if model == "res.partner":
            return [{"id": 80494, "user_id": [2, "Menejer"]}]
        return [{"id": 2, "name": "Menejer", "phone": False, "mobile": "+998901112233"}]

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = get_partner_manager(80494)
    assert result == {"name": "Menejer", "phone": "+998901112233"}


def test_get_partner_manager_returns_none_on_failure(monkeypatch):
    _configure(monkeypatch)

    def execute_kw(*args, **kwargs):
        raise ConnectionError("boom")

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = get_partner_manager(80494)
    assert result is None


def test_create_telegram_order_lead_returns_none_on_failure(monkeypatch):
    _configure(monkeypatch)

    def execute_kw(*args, **kwargs):
        raise ConnectionError("boom")

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = create_telegram_order_lead(partner_id=42, partner_name="Test Partner", chat_id=1, order_details="x")
    assert result is None


class _FakeDatetime(datetime):
    """`get_monthly_statistics()`ning "hozir"ni sun'iy qiymatga qattiq bog'lab
    qo'yish uchun -- 2026-08-04, Statistika funksiyasi."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 8, 15, tzinfo=timezone.utc)


def test_get_monthly_statistics_always_shows_full_current_year_jan_to_dec(monkeypatch):
    """Har doim joriy yilning 1-Yanvar - 31-Dekabr oralig'i so'raladi, hali kelmagan
    oylar ham natijada 0 bilan ko'rinadi. 2026-08-04, mijoz aniq so'ragan: Odoo'ning
    "cancel" holati endi UMUMAN so'ralmaydi (faqat sale/done), `amount_total > 0`
    filtri ham olib tashlandi -- tasnif endi belgiga (musbat/manfiy) qarab bo'ladi."""
    _configure(monkeypatch)
    monkeypatch.setattr(odoo_client_module, "datetime", _FakeDatetime)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        if model == "sale.order":
            assert args[0] == [
                ["partner_id", "=", 42],
                ["state", "in", ["sale", "done"]],
                ["date_order", ">=", "2026-01-01"],
                ["date_order", "<=", "2026-12-31"],
            ]
        elif model == "account.payment":
            assert args[0] == [
                ["partner_id", "=", 42],
                ["payment_type", "=", "inbound"],
                ["partner_type", "=", "customer"],
                ["state", "=", "paid"],
                ["date", ">=", "2026-01-01"],
                ["date", "<=", "2026-12-31"],
            ]
        return []

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = get_monthly_statistics(42)

    assert [m["month"] for m in result["months"]] == [
        "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06",
        "2026-07", "2026-08", "2026-09", "2026-10", "2026-11", "2026-12",
    ]
    assert result["by_object"] == []


def test_get_monthly_statistics_returns_none_on_odoo_failure(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(odoo_client_module, "datetime", _FakeDatetime)

    def execute_kw(*args, **kwargs):
        raise ConnectionError("boom")

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    assert get_monthly_statistics(42) is None


def test_get_monthly_statistics_returns_none_when_payment_query_fails(monkeypatch):
    """sale.order so'rovi muvaffaqiyatli bo'lsa-da, `account.payment` so'rovi
    muvaffaqiyatsiz bo'lsa ham butun funksiya None qaytarishi kerak (0 to'lovdan
    farqli) -- ikkala Odoo so'rovi ham mustaqil ravishda muvaffaqiyatsiz bo'lishi
    mumkin."""
    _configure(monkeypatch)
    monkeypatch.setattr(odoo_client_module, "datetime", _FakeDatetime)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        if model == "sale.order":
            return []
        raise ConnectionError("boom")

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    assert get_monthly_statistics(42) is None


def test_get_monthly_statistics_classifies_by_amount_sign_not_state(monkeypatch):
    """2026-08-04, mijoz aniq so'ragan: Odoo'ning "cancel" holati endi hisobga
    olinmaydi -- tasnif faqat amount_total belgisiga qarab bo'ladi: musbat -> spent,
    manfiy -> returned (DOIM manfiy saqlanadi, "-" bilan). Ikkala buyurtma ham
    state="sale" bo'lsa-da, biri manfiy summali (qaytarilgan tovar)."""
    _configure(monkeypatch)
    monkeypatch.setattr(odoo_client_module, "datetime", _FakeDatetime)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        return [
            {"amount_total": 1000.0, "date_order": "2026-08-05 10:00:00", "partner_shipping_id": False},
            {"amount_total": 500.0, "date_order": "2026-08-10 10:00:00", "partner_shipping_id": False},
            {"amount_total": -200.0, "date_order": "2026-03-01 10:00:00", "partner_shipping_id": False},
        ]

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = get_monthly_statistics(42)
    months = {m["month"]: m for m in result["months"]}

    assert months["2026-08"] == {"month": "2026-08", "spent": 1500.0, "returned": 0.0, "paid": 0.0}
    assert months["2026-03"] == {"month": "2026-03", "spent": 0.0, "returned": -200.0, "paid": 0.0}
    assert months["2026-01"] == {"month": "2026-01", "spent": 0.0, "returned": 0.0, "paid": 0.0}


def test_get_monthly_statistics_sums_paid_amounts_from_account_payment(monkeypatch):
    """2026-08-04, mijoz aniq so'ragan: "to'langan" endi sale.order'ning
    `x_studio_paid_amount` maydonidan EMAS, `account.payment` modelining `amount`
    maydonidan, TO'LOV SANASI (`date`) bo'yicha guruhlanadi -- buyurtma qaysi oyda
    yaratilganidan mutlaqo mustaqil (masalan o'tgan oydagi qarzni shu oy to'lasa,
    bu to'lov ANIQ shu oyning "to'langan" summasiga qo'shiladi, buyurtma yaratilgan
    oyga emas). Faqat `state="paid"`, `payment_type="inbound"`, `partner_type="customer"`
    to'lovlar hisoblanadi."""
    _configure(monkeypatch)
    monkeypatch.setattr(odoo_client_module, "datetime", _FakeDatetime)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        if model == "sale.order":
            # Iyulda yaratilgan buyurtma -- to'lovi esa Avgustda amalga oshirilgan
            return [
                {"amount_total": 1000.0, "date_order": "2026-07-05 10:00:00", "partner_shipping_id": False},
            ]
        if model == "account.payment":
            assert kwargs["fields"] == ["amount", "date", "reconciled_invoice_ids"]
            return [
                {"amount": 600.0, "date": "2026-08-05"},
                {"amount": 500.0, "date": "2026-08-10"},
            ]
        return []

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = get_monthly_statistics(42)
    months = {m["month"]: m for m in result["months"]}

    # To'lov Avgustda amalga oshirilgani uchun "paid" AVGUSTGA yoziladi, buyurtma
    # yaratilgan IYULGA emas -- bu aynan eski usuldan farq qiluvchi jihat.
    assert months["2026-08"]["paid"] == 1100.0
    assert months["2026-07"]["paid"] == 0.0
    assert months["2026-07"]["spent"] == 1000.0


def test_get_monthly_statistics_ignores_non_paid_or_outbound_payments(monkeypatch):
    """Qoralama/bekor qilingan/rad etilgan to'lovlar va chiquvchi (bizdan mijozga)
    to'lovlar "to'langan" summasiga umuman qo'shilmasligi kerak -- bular Odoo domenida
    (`state="paid"`, `payment_type="inbound"`, `partner_type="customer"`) filtrlanadi,
    bu yerda faqat domen to'g'ri yuborilganini emas, natija to'g'ri qo'shilishini
    tekshiramiz (fake execute_kw domenni tekshirmasdan hamma narsani qaytarsa ham)."""
    _configure(monkeypatch)
    monkeypatch.setattr(odoo_client_module, "datetime", _FakeDatetime)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        if model == "sale.order":
            return []
        if model == "account.payment":
            # Odoo domeni orqali filtrlangan holda -- shu sabab bu yerga faqat
            # tasdiqlangan kirim to'lovlari keladi deb faraz qilinadi
            return [{"amount": 250.0, "date": "2026-05-15"}]
        return []

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = get_monthly_statistics(42)
    months = {m["month"]: m for m in result["months"]}

    assert months["2026-05"]["paid"] == 250.0


def test_get_monthly_statistics_groups_by_object_via_shipping_id(monkeypatch):
    """2026-08-04, mijoz aniq so'ragan: grafikdan tashqari, jadval uchun obyektlar
    bo'yicha ham xarajat/qaytarilgan -- `get_quotations`'dagi bilan bir xil
    `partner_shipping_id` orqali guruhlanadi ("Umumiy" hal qilish FastAPI qatlamining
    ishi, bu yerda faqat object_id/object_name xom holida qaytadi)."""
    _configure(monkeypatch)
    monkeypatch.setattr(odoo_client_module, "datetime", _FakeDatetime)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        return [
            {"amount_total": 1000.0, "date_order": "2026-08-05 10:00:00",
             "partner_shipping_id": (5, "Olmazor")},
            {"amount_total": -300.0, "date_order": "2026-08-06 10:00:00",
             "partner_shipping_id": (5, "Olmazor")},
            {"amount_total": 700.0, "date_order": "2026-08-07 10:00:00",
             "partner_shipping_id": False},  # aniq obyekt tanlanmagan -> asosiy mijoz (id=42)
        ]

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = get_monthly_statistics(42)
    by_object = {obj["object_id"]: obj for obj in result["by_object"]}

    assert by_object[5] == {
        "object_id": 5, "object_name": "Olmazor", "spent": 1000.0, "returned": -300.0, "paid": 0.0,
    }
    assert by_object[42] == {
        "object_id": 42, "object_name": None, "spent": 700.0, "returned": 0.0, "paid": 0.0,
    }


def test_get_monthly_statistics_attributes_paid_to_object_via_reconciled_invoice(monkeypatch):
    """2026-08-04, real "qarshi talmarjon" obyektida to'lov qilingan lekin PDF'da
    ko'rinmayapti degan xabar tekshirilib tuzatildi -- live Odoo'da tasdiqlangan
    zanjir: to'lovning o'z `partner_id`'si har doim asosiy mijoz bo'lsa-da, u yopgan
    invoys (`account.payment.reconciled_invoice_ids`) o'zining `partner_shipping_id`si
    orqali aniq obyektga bog'liq bo'lishi mumkin -- shu zanjir orqali to'lov to'g'ri
    obyektga (asosiy mijozga emas) yozilishi kerak."""
    _configure(monkeypatch)
    monkeypatch.setattr(odoo_client_module, "datetime", _FakeDatetime)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        if model == "sale.order":
            return [
                {"amount_total": 420000.0, "date_order": "2026-08-03 13:42:28",
                 "partner_shipping_id": (80499, "qarshi talmarjon")},
            ]
        if model == "account.payment":
            return [{"amount": 420000.0, "date": "2026-08-04", "reconciled_invoice_ids": [590166]}]
        if model == "account.move":
            assert args[0] == [590166]
            return [{"id": 590166, "amount_total": 420000.0, "partner_shipping_id": (80499, "qarshi talmarjon")}]
        return []

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = get_monthly_statistics(80494)
    by_object = {obj["object_id"]: obj for obj in result["by_object"]}

    assert by_object[80499]["paid"] == 420000.0
    # Asosiy mijoz ("Umumiy") uchun hech qanday to'lov qolmasligi kerak -- to'liq
    # obyektga o'tkazilgan.
    assert 80494 not in by_object or by_object[80494]["paid"] == 0.0
    assert {m["month"]: m["paid"] for m in result["months"]}["2026-08"] == 420000.0


def test_get_monthly_statistics_payment_without_reconciled_invoice_stays_general(monkeypatch):
    """Hech qanday invoysga bog'lanmagan to'lov (masalan avans, hali yopilmagan) --
    to'liq "Umumiy" (`object_id == partner_id`)da qolishi kerak, xatolik bermasligi
    yoki yo'qolib ketmasligi kerak."""
    _configure(monkeypatch)
    monkeypatch.setattr(odoo_client_module, "datetime", _FakeDatetime)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        if model == "sale.order":
            return []
        if model == "account.payment":
            return [{"amount": 150000.0, "date": "2026-08-04", "reconciled_invoice_ids": []}]
        return []

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = get_monthly_statistics(80494)
    by_object = {obj["object_id"]: obj for obj in result["by_object"]}

    assert by_object[80494]["paid"] == 150000.0


def test_get_monthly_statistics_splits_payment_across_objects_proportionally(monkeypatch):
    """Bitta to'lov bir nechta turli obyektning invoysini yopsa (kam uchraydigan,
    lekin mumkin bo'lgan holat) -- summasi invoyslar `amount_total`iga
    PROPORTSIONAL bo'lib beriladi, xato chiqmaydi va yig'indi to'lov summasiga teng
    bo'lib qoladi."""
    _configure(monkeypatch)
    monkeypatch.setattr(odoo_client_module, "datetime", _FakeDatetime)

    def execute_kw(db, uid, key, model, method, args, kwargs):
        if model == "sale.order":
            return []
        if model == "account.payment":
            return [{"amount": 300000.0, "date": "2026-08-04", "reconciled_invoice_ids": [1, 2]}]
        if model == "account.move":
            assert sorted(args[0]) == [1, 2]
            return [
                {"id": 1, "amount_total": 200000.0, "partner_shipping_id": (5, "Olmazor")},
                {"id": 2, "amount_total": 100000.0, "partner_shipping_id": (6, "Qo'yliq")},
            ]
        return []

    _install_fake_proxies(monkeypatch, execute_kw_side_effect=execute_kw)
    result = get_monthly_statistics(80494)
    by_object = {obj["object_id"]: obj for obj in result["by_object"]}

    assert by_object[5]["paid"] == 200000.0
    assert by_object[6]["paid"] == 100000.0
