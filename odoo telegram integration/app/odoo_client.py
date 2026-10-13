"""
Odoo bilan to'g'ridan-to'g'ri (XML-RPC) integratsiya -- Malumotlarim (F.I.Sh/telefon/balans/
obyektlar) va Tijorat takliflarim bo'limlari uchun. Push/kesh tizimi ishlatilmaydi: bot
so'rov yuborganda shu paytning o'zida Odoo'dan real vaqtli ma'lumot olinadi.

Sxema (dunyabunya Odoo bazasida tekshirilgan):
    res.partner.x_telegram_id       -- mijozning Telegram chat_id'si (string)
    res.partner.phone / mobile      -- mijozning telefon raqami (Malumotlarim bo'limi uchun)
    res.partner.x_uzs_balance       -- mijozning joriy UZS balansi
    res.partner.x_usd_balance       -- mijozning joriy USD balansi
    res.partner.child_ids (type="delivery") -- mijozning obyektlari (qurilish joylari) --
                                                standart manzil maydonlari (street/city)
                                                to'ldirilmagan, shuning uchun obyekt NOMI
                                                (masalan "NAZARBEK", "ISTIROXAT") lokatsiya
                                                sifatida ishlatiladi; har biri o'zining
                                                x_uzs_balance/x_usd_balance'iga ega
    sale.order (state=draft/sent)   -- "Quotation" holatidagi buyurtmalar (tijorat taklifi)
    crm.lead                        -- "Buyurtma berish"dan kelgan BARCHA buyurtma so'rovlari
                                        (Odoo'da MAVJUD -- x_telegram_id orqali topilgan --
                                        VA butunlay yangi mijozlar, 2026-08-02'dan boshlab)
                                        shu yerga yoziladi (2026-07-30, Odoo tarafi bilan
                                        kelishilgan sxema -- pastdagi create_telegram_order_lead
                                        izohiga qarang). MAVJUD mijoz uchun partner_id orqali
                                        bog'lanadi; yangi mijoz uchun contact_name/phone
                                        to'g'ridan-to'g'ri lead'ga yoziladi (partner_id yo'q).
                                        Odoo'ga yozish muvaffaqiyatsiz bo'lsa mijozga xatolik
                                        xabari ko'rsatiladi -- call-center guruhiga forward
                                        qilish 2026-08-03'da butunlay olib tashlandi.
    ir.attachment                   -- rasm/video/ovozli xabar (agar bo'lsa) crm.lead'ga
                                        HAQIQIY fayl biriktirmasi sifatida yoziladi (2026-08-03,
                                        pastdagi create_ir_attachment()'ga qarang) -- avval
                                        sinab ko'rilgan URL-havola yondashuvi shu kunning
                                        o'zida bundan foydasiga almashtirildi.

Bu modul faqat SINXRON (bloklovchi) xmlrpc.client chaqiruvlarini o'z ichiga oladi --
chaqiruvchi tomon (fastapi_app.py) ularni asyncio.to_thread() orqali ishga tushirishi
kerak, aks holda event loop bloklanadi.

Tezlik (2026-08-03): `uid` process darajasida keshlanadi (avval HAR BIR chaqiruvda qayta
autentifikatsiya qilinar edi -- shu sabab Quotation kabi bir nechta ketma-ket chaqiruv
talab qiladigan bo'limlarda sekinlik/timeout bo'lgan). ServerProxy'lar thread-local
saqlanadi (qayta ulanish ochish xarajatini kamaytirish uchun, lekin thread'lar orasida
BITTA ulanishni baham ko'rish xavfsiz emasligi uchun). Pastdagi `_execute()`'ga qarang.
Shu kunning o'zida (foydalanuvchi "Malumotlarim 4-5 soniya ishlayapti" deb shikoyat
qilgach) yana bir bosqich: `find_partner_by_chat_id`/`get_customer_objects` avval
alohida `search()` + `read()` (har biri o'z tarmoq so'rovi) edi -- ikkalasi ham bitta
`search_read()` chaqiruviga birlashtirildi (get_quotations'da allaqachon ishlatilgan
naqsh), Malumotlarim/Tijorat-takliflarim/Buyurtma-berish uchun Odoo'ga borish-kelish
sonini deyarli yarmiga tushiradi. Kesh (natijalarni saqlab qo'yish) ATAYLAB
ishlatilmaydi -- bu funksiyalar har doim Odoo'dan REAL VAQTLI (balans kabi moliyaviy
ma'lumot uchun eskirgan qiymat ko'rsatib qo'ymaslik uchun) o'qiydi."""

import concurrent.futures
import logging
import threading
import xmlrpc.client
from datetime import datetime, timezone

from app.config import settings

logger = logging.getLogger(__name__)

# Tezlik uchun (2026-08-03): avval HAR BIR _execute() chaqiruvi qayta autentifikatsiya
# qilib (uid so'rab) + yangi ServerProxy ochib 2 ta to'liq tarmoq so'rovi/ulanish sarflar
# edi. Endi: `uid` process darajasida (barcha threadlar orasida) bir marta olinib
# keshlanadi -- keyingi chaqiruvlar shu qiymatni qayta ishlatadi, faqat xato chiqqanda
# (masalan uid eskirgan/yaroqsiz bo'lib qolgan bo'lsa) bir marta majburiy qayta
# autentifikatsiya qilinadi. `uid`ning o'zi konnektsiyaga bog'liq emas (Odoo XML-RPC'da
# har bir execute_kw so'roviga alohida uzatiladi) -- shu sabab thread'lar orasida
# baham ko'rish xavfsiz. ServerProxy'lar esa thread-local qilib saqlanadi: bitta
# ServerProxy'ni bir nechta thread bir vaqtning o'zida ishlatishi (asyncio.to_thread
# orqali parallel so'rovlar kelishi mumkin) xavfsiz EMAS (ichidagi HTTP ulanishi shared
# holatga ega) -- thread-local shu muammoni oldini oladi, shu bilan birga BIR thread
# ichida ketma-ket kelgan so'rovlar uchun ulanishni qayta ochish xarajatidan qutqaradi.
_cached_uid: int | None = None
_local = threading.local()


def _is_configured() -> bool:
    return bool(settings.ODOO_URL and settings.ODOO_DB and settings.ODOO_USERNAME and settings.ODOO_API_KEY)


def _get_common_proxy() -> xmlrpc.client.ServerProxy:
    proxy = getattr(_local, "common_proxy", None)
    if proxy is None:
        proxy = xmlrpc.client.ServerProxy(f"{settings.ODOO_URL}/xmlrpc/2/common")
        _local.common_proxy = proxy
    return proxy


def _get_object_proxy() -> xmlrpc.client.ServerProxy:
    proxy = getattr(_local, "object_proxy", None)
    if proxy is None:
        proxy = xmlrpc.client.ServerProxy(f"{settings.ODOO_URL}/xmlrpc/2/object")
        _local.object_proxy = proxy
    return proxy


def _authenticate(force: bool = False) -> int | None:
    """`uid`ni qaytaradi -- keshlangan qiymat bo'lsa (va `force=False` bo'lsa) shuni,
    aks holda Odoo'dan yangidan so'rab keshlab qo'yadi."""
    global _cached_uid
    if not _is_configured():
        return None
    if _cached_uid is not None and not force:
        return _cached_uid
    try:
        uid = _get_common_proxy().authenticate(settings.ODOO_DB, settings.ODOO_USERNAME, settings.ODOO_API_KEY, {})
        _cached_uid = uid or None
        return _cached_uid
    except Exception:
        logger.exception("Odoo'ga autentifikatsiyada xatolik")
        _cached_uid = None
        return None


def _execute(model: str, method: str, *args, **kwargs):
    """Odoo'ga execute_kw so'rovini yuboradi. Sozlanmagan/xato holatda None qaytaradi.
    Keshlangan `uid` eskirgan/yaroqsiz bo'lib qolgan bo'lishi mumkin (masalan Odoo
    tomonidan bekor qilingan) -- shu sabab birinchi urinish xato bersa, bir marta
    majburiy qayta autentifikatsiya qilib qayta urinib ko'riladi (2026-08-03)."""
    uid = _authenticate()
    if not uid:
        return None
    try:
        return _get_object_proxy().execute_kw(
            settings.ODOO_DB, uid, settings.ODOO_API_KEY, model, method, list(args), kwargs
        )
    except Exception:
        uid = _authenticate(force=True)
        if not uid:
            return None
        try:
            return _get_object_proxy().execute_kw(
                settings.ODOO_DB, uid, settings.ODOO_API_KEY, model, method, list(args), kwargs
            )
        except Exception:
            logger.exception("Odoo so'rovida xatolik: %s.%s", model, method)
            return None


def find_partner_by_chat_id(chat_id: int) -> dict | None:
    """Telegram chat_id (x_telegram_id) bo'yicha mijozning asosiy partner yozuvini topadi.
    Tezlik uchun (2026-08-03, foydalanuvchi "Malumotlarim 4-5 soniya" deb shikoyat
    qilgach): avval alohida `search()` + `read()` (2 ta ketma-ket tarmoq so'rovi) edi --
    endi bitta `search_read()` chaqiruvi (get_quotations'da allaqachon ishlatilgan naqsh),
    Odoo'ga borish-kelish sonini shu funksiya uchun yarmiga tushiradi."""
    records = _execute(
        "res.partner",
        "search_read",
        [["x_telegram_id", "=", str(chat_id)]],
        fields=["id", "name", "phone", "mobile", "x_uzs_balance", "x_usd_balance"],
        limit=1,
    )
    return records[0] if records else None


def get_customer_objects(partner_id: int) -> list[dict]:
    """Mijozning obyektlarini (type="delivery" bo'lgan pastki kontaktlar) qaytaradi --
    Odoo'ning o'zining standart geolokatsiya maydonlari (partner_latitude/partner_longitude,
    2026-08-02'da tekshirilgan -- res.partner'da tayyor mavjud, alohida custom maydon
    kerak emas) bilan birga. 0.0/0.0 -- lokatsiya hali kiritilmagan degani (Odoo'ning
    o'zi shu qiymatni default sifatida ishlatadi). `phone`/`mobile` (2026-08-03, live
    tekshirilgan -- ko'p obyektlarda to'ldirilgan, asosiy mijoz raqamidan FARQLI, ya'ni
    shu aniq obyekt/qurilish joyidagi mas'ul shaxsning raqami) -- Malumotlarim bo'limida
    har bir obyekt qatorida ko'rsatish uchun. Tezlik uchun (2026-08-03): alohida
    `search()` + `read()` o'rniga bitta `search_read()` -- find_partner_by_chat_id'dagi
    izohga qarang."""
    return (
        _execute(
            "res.partner",
            "search_read",
            [["parent_id", "=", partner_id], ["type", "=", "delivery"]],
            fields=[
                "id", "name", "phone", "mobile", "x_uzs_balance", "x_usd_balance",
                "partner_latitude", "partner_longitude",
            ],
        )
        or []
    )


def set_object_location(object_id: int, latitude: float, longitude: float) -> bool:
    """Mavjud obyekt (child res.partner, type="delivery") uchun GPS lokatsiyasini
    to'g'ridan-to'g'ri Odoo'ning o'z standart maydonlariga (partner_latitude/
    partner_longitude) yozadi -- Odoo xaritasida darhol ko'rinadi, alohida sync/poll
    kerak emas (get_customer_objects keyingi safar shu qiymatlarni o'qiydi)."""
    result = _execute(
        "res.partner", "write", [object_id], {"partner_latitude": latitude, "partner_longitude": longitude}
    )
    return bool(result)


def get_quotations(partner_ids: list[int]) -> list[dict] | None:
    """Berilgan partnerlar (asosiy + obyektlar) uchun "quotation" holatidagi (draft/sent)
    buyurtmalarni qaytaradi. Qaysi obyektga tegishli ekanini aniqlash uchun avval
    `partner_id` (asosiy mijoz+obyekt qidiruv doirasi) ishlatilgan edi, lekin LIVE
    tekshirilganda `sale.order.partner_id` shu Odoo instansiyasida HAR DOIM asosiy
    mijozning o'zi ekani (hech qachon obyekt emasligi) aniqlandi -- guruhlash uchun
    yaroqsiz. 2026-08-03, mijoz ko'rsatgan standart Odoo maydoni orqali tuzatildi:
    `partner_shipping_id` ("Yetkazib berish manzili", domain child_of partner_id +
    type=delivery -- aynan obyekt uchun mo'ljallangan) -- live tekshirilganda haqiqatan
    ham ba'zi buyurtmalarda aniq obyektga (masalan "Olmazor") o'rnatilgan holda
    topildi, boshqalarida esa (hech kim aniq yetkazib berish manzilini tanlamagan
    bo'lsa) standart bo'yicha asosiy mijozning o'ziga teng bo'ladi -- shu holatlar
    "Umumiy" sifatida guruhlanadi (bot_menu_quotation'ga qarang)."""
    if not partner_ids:
        return []
    return _execute(
        "sale.order",
        "search_read",
        [["partner_id", "in", partner_ids], ["state", "in", ["draft", "sent"]]],
        fields=["name", "amount_total", "date_order", "partner_id", "partner_shipping_id"],
        order="date_order desc",
        limit=20,
    )


def get_monthly_statistics(partner_id: int) -> dict | None:
    """Berilgan mijoz uchun JORIY YILNING to'liq statistikasini qaytaradi:
    `{"months": [...12 ta, Yanvar->Dekabr...], "by_object": [...]}`.

    Oy oralig'i: har doim joriy yilning 1-Yanvaridan 31-Dekabrigacha, 12 oy -- hali
    kelmagan (kelajakdagi) oylar ham jadvalda 0 bilan ko'rinadi.

    Tasnif AMOUNT_TOTAL BELGISIGA qarab (2026-08-04, mijoz aniq so'ragan -- Odoo'ning
    "cancel" HOLATI ENDI UMUMAN ISHLATILMAYDI, faqat tasdiqlangan state="sale"/"done"
    buyurtmalar so'raladi): musbat summa -> `spent` (sotib olingan), manfiy summa ->
    `returned` (qaytarilgan -- mijoz tovarni qaytarib bergan holat, Odoo'da shu
    partnerning salbiy summali sale.order yozuvi sifatida qayd etiladi). `returned`
    QIYMATI DOIM MANFIY saqlanadi (chaqiruvchi tomon -- jadval -- shu holicha "-"
    bilan ko'rsatishi kerak, ijobiy qiymatga FAQAT grafik chizishda o'tkaziladi,
    `app/bot/statistics_pdf.py`ga qarang).

    `by_object` (jadval uchun obyektlar bo'yicha ham xarajat/qaytarilgan/to'langan): xuddi
    `get_quotations`'dagi bilan bir xil `partner_shipping_id` maydoni orqali
    guruhlanadi (`sale.order.partner_id` shu Odoo instansiyasida har doim asosiy
    mijozning o'zi -- guruhlash uchun yaroqsiz, lekin qidiruv doirasi sifatida
    to'g'ri ishlatiladi). Har element: `{"object_id", "object_name", "spent",
    "returned", "paid"}` -- `object_id == partner_id` bo'lsa hech qanday aniq obyekt
    tanlanmagan degani ("Umumiy" sifatida ko'rsatish chaqiruvchi tomonning ishi,
    xuddi bot_menu_quotation'dagi kabi -- shu yerda i18n haqida bilim shart emas).
    Faqat shu yilgi ma'lumotdan chiqqan (buyurtma YOKI to'lov orqali) obyektlar
    ro'yxatda ko'rinadi.

    `paid` -- Odoo'ning `account.payment` modelidan (2026-08-04, mijoz aniq so'ragan --
    avval sale.order'ning `x_studio_paid_amount` maydoni ishlatilgan edi, lekin bu
    buyurtma YARATILGAN oyga bog'liq edi, HAQIQIY to'lov QAYSI oyda amalga oshirilgani
    bilan mos kelmasligi mumkin edi -- masalan o'tgan oydagi qarzni shu oy to'lasa,
    eski usul buni sezmas edi). Endi to'lov SANASI (`date`) bo'yicha guruhlanadi --
    faqat HAQIQIY tasdiqlangan (`state="paid"`), mijozdan KIRIM (`payment_type="inbound"`,
    `partner_type="customer"`) to'lovlar hisoblanadi (qoralama/bekor qilingan/rad
    etilganlar -- yo'q). OYLIK jami shu bilan hisoblanadi (obyektga bog'liq emas).

    `paid` OBYEKT darajasida (2026-08-04, "qarshi talmarjon" obyektida to'lov PDF'da
    ko'rinmayapti degan real xabar tekshirilib tuzatildi): LIVE tekshirilganda
    `account.payment.partner_id` shu Odoo instansiyasida HAR DOIM asosiy mijozning
    o'zi ekani tasdiqlandi (aniq obyektga hech qachon bog'lanmaydi) -- LEKIN bu
    to'lov qaysi OBYEKT uchun ekanini bilishning yo'li yo'q degani EMAS: har bir
    to'lov odatda bitta (yoki bir nechta) HAQIQIY invoys (`account.move`)ni yopadi
    (standart Odoo `account.payment.reconciled_invoice_ids` maydoni orqali, live
    tasdiqlangan -- masalan to'lov 51804 invoys 590166'ni yopgan, invoys 590166'ning
    o'zi esa `partner_shipping_id`=80499 "qarshi talmarjon"ga ega, chunki sale.order
    S56652 aynan shu obyekt uchun yaratilgan bo'lgan). Demak invoysning O'ZI --
    xuddi sale.order'dagi kabi -- `partner_shipping_id` orqali aniq obyektga bog'liq
    bo'lishi mumkin, hatto to'lovning o'z `partner_id`'si har doim asosiy mijoz bo'lsa
    ham. Shu sabab pastda: har bir to'lov uchun uning `reconciled_invoice_ids`
    ro'yxatidagi invoyslar (bitta so'rovda batch o'qiladi) tekshiriladi, har biri
    o'z `partner_shipping_id`'siga (yoki hech kim tanlamagan bo'lsa asosiy mijozga)
    guruhlanadi, keyin to'lov summasi shu guruhlarga ularning invoys `amount_total`
    og'irligiga PROPORTSIONAL bo'lib beriladi (bitta to'lov bir nechta turli
    obyektning invoysini yopishi ham mumkin -- kam uchraydigan holat, lekin xato
    chiqarmaslik uchun hisobga olingan). Hech qanday invoysga bog'lanmagan to'lov
    (masalan oldindan/avans to'lov, hali hech narsaga yopilmagan) -- to'liq
    "Umumiy" (`object_id == partner_id`)da qoladi.

    Odoo so'rovi muvaffaqiyatsiz bo'lsa (masalan autentifikatsiya xatosi) None
    qaytadi -- chaqiruvchi tomon buni "0 xarajat" bilan adashtirmasligi kerak."""
    year = datetime.now(timezone.utc).year
    since = f"{year:04d}-01-01"
    until = f"{year:04d}-12-31"

    # sale.order va account.payment so'rovlari bir-biriga bog'liq emas -- PARALLEL
    # ishga tushiriladi (2026-08-04, tezlik uchun: ketma-ket bo'lsa 2 ta XML-RPC
    # tarmoq borish-kelishi yig'indisi, parallel bo'lsa faqat sekinrog'ining vaqti).
    # ServerProxy'lar thread-local (yuqoridagi modul docstringiga qarang) -- shu
    # sabab 2 ta thread bir vaqtda xavfsiz ishlaydi, bitta ulanishni baham ko'rmaydi.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        orders_future = executor.submit(
            _execute,
            "sale.order",
            "search_read",
            [
                ["partner_id", "=", partner_id],
                ["state", "in", ["sale", "done"]],
                ["date_order", ">=", since],
                ["date_order", "<=", until],
            ],
            fields=["amount_total", "date_order", "partner_shipping_id"],
        )
        payments_future = executor.submit(
            _execute,
            "account.payment",
            "search_read",
            [
                ["partner_id", "=", partner_id],
                ["payment_type", "=", "inbound"],
                ["partner_type", "=", "customer"],
                ["state", "=", "paid"],
                ["date", ">=", since],
                ["date", "<=", until],
            ],
            fields=["amount", "date", "reconciled_invoice_ids"],
        )
        records = orders_future.result()
        payments = payments_future.result()

    if records is None or payments is None:
        return None

    # To'lovlar qaysi obyektga tegishli ekanini aniqlash uchun ularning yopgan
    # invoyslarini (agar bo'lsa) BATCH qilib bitta so'rovda o'qiymiz -- har bir
    # to'lov uchun alohida so'rov yubormaslik uchun (yuqoridagi docstringga qarang).
    all_invoice_ids = sorted({
        invoice_id
        for payment in payments
        for invoice_id in (payment.get("reconciled_invoice_ids") or [])
    })
    invoices = (
        _execute("account.move", "read", all_invoice_ids, fields=["amount_total", "partner_shipping_id"])
        if all_invoice_ids
        else []
    )
    if all_invoice_ids and invoices is None:
        return None
    invoice_map = {invoice["id"]: invoice for invoice in invoices}

    month_keys = [(year, month) for month in range(1, 13)]
    month_totals: dict[tuple[int, int], dict[str, float]] = {
        key: {"spent": 0.0, "returned": 0.0, "paid": 0.0} for key in month_keys
    }
    object_totals: dict[int, dict] = {}

    def _object_bucket(obj_id: int, obj_name: str | None) -> dict:
        return object_totals.setdefault(
            obj_id, {"object_name": obj_name, "spent": 0.0, "returned": 0.0, "paid": 0.0}
        )

    for record in records:
        amount = record.get("amount_total") or 0.0
        is_returned = amount < 0

        date_order = record.get("date_order") or ""
        try:
            key = (int(date_order[0:4]), int(date_order[5:7]))
        except (ValueError, IndexError):
            key = None
        if key in month_totals:
            month_totals[key]["returned" if is_returned else "spent"] += amount

        shipping = record.get("partner_shipping_id")
        obj_id, obj_name = shipping if shipping else (partner_id, None)
        obj_bucket = _object_bucket(obj_id, obj_name)
        obj_bucket["returned" if is_returned else "spent"] += amount

    for payment in payments:
        amount = payment.get("amount") or 0.0

        payment_date = payment.get("date") or ""
        try:
            key = (int(payment_date[0:4]), int(payment_date[5:7]))
        except (ValueError, IndexError):
            key = None
        if key in month_totals:
            month_totals[key]["paid"] += amount

        # Obyekt darajasida: to'lov yopgan invoys(lar)ning `partner_shipping_id`si
        # bo'yicha guruhlanadi, invoys `amount_total`iga proportsional bo'lib beriladi
        # (bitta invoysga yopilgan oddiy holatda bu shunchaki to'lovning to'liq
        # o'zi bo'ladi). Hech qanday invoysga bog'lanmagan bo'lsa -- "Umumiy"da qoladi.
        weights: dict[tuple[int, str | None], float] = {}
        for invoice_id in payment.get("reconciled_invoice_ids") or []:
            invoice = invoice_map.get(invoice_id)
            if not invoice:
                continue
            shipping = invoice.get("partner_shipping_id")
            obj_id, obj_name = shipping if shipping else (partner_id, None)
            weight_key = (obj_id, obj_name)
            weights[weight_key] = weights.get(weight_key, 0.0) + (invoice.get("amount_total") or 0.0)

        total_weight = sum(weights.values())
        if not weights or total_weight == 0:
            _object_bucket(partner_id, None)["paid"] += amount
        else:
            for (obj_id, obj_name), weight in weights.items():
                _object_bucket(obj_id, obj_name)["paid"] += amount * (weight / total_weight)

    months = [
        {
            "month": f"{y:04d}-{m:02d}",
            "spent": month_totals[(y, m)]["spent"],
            "returned": month_totals[(y, m)]["returned"],
            "paid": month_totals[(y, m)]["paid"],
        }
        for y, m in month_keys
    ]
    by_object = [
        {"object_id": obj_id, "object_name": data["object_name"],
         "spent": data["spent"], "returned": data["returned"], "paid": data["paid"]}
        for obj_id, data in object_totals.items()
    ]

    return {"months": months, "by_object": by_object}


# "Buyurtma berish" -- Odoo'da MAVJUD mijozdan kelgan so'rov shu CRM jamoa/bosqich/manbaga
# yoziladi. 2026-07-30'da Odoo tarafi tomonidan maxsus shu maqsad uchun yaratilgan va
# live create/read/unlink orqali tasdiqlangan (taxmin qilinmagan) -- ID'lar shu Odoo
# instansiyasiga xos, boshqa muhitga ko'chirilganda qayta tekshirilishi kerak.
TELEGRAM_ORDER_CRM_TEAM_ID = 15  # crm.team "Telegram buyurtmalar"
TELEGRAM_ORDER_CRM_STAGE_ID = 17  # crm.stage "Yangi" (shu jamoaga tegishli)
TELEGRAM_ORDER_UTM_SOURCE_ID = 15  # utm.source "Telegram Bot"


def create_telegram_order_lead(
    partner_id: int | None,
    partner_name: str,
    chat_id: int,
    order_details: str,
    object_name: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    phone: str | None = None,
) -> int | None:
    """"Buyurtma berish" so'rovini yangi crm.lead sifatida yozadi -- MAVJUD (Odoo'da
    x_telegram_id orqali topilgan) VA butunlay yangi (Odoo'da topilmagan) mijozlar uchun
    bitta funksiya: `partner_id` berilsa lead shu res.partner'ga bog'lanadi (ism/telefon
    Odoo'da partner orqali allaqachon bor); `partner_id=None` bo'lsa (yangi mijoz) o'rniga
    `contact_name`/`phone` -- crm.lead'ning partner'ga bog'lanmagan lead uchun mo'ljallangan
    standart maydonlari (custom field emas, 2026-08-02'da fields_get + haqiqiy create/read/
    unlink orqali tasdiqlangan) -- to'g'ridan-to'g'ri lead'ning o'ziga yoziladi. Muvaffaqiyatsiz
    bo'lsa (Odoo xatosi, ulanish yo'q va h.k.) None qaytaradi -- chaqiruvchi tomon
    (fastapi_app.py orqali menu.py) bu holda mijozga xatolik haqida xabar beradi. Rasm/video/
    ovozli xabar (agar bo'lsa) URL sifatida description'ga qo'shish 2026-08-03'da sinab
    ko'rilgan (fields_get + real create/read orqali) va keyinroq shu kunning o'zida
    ir.attachment orqali haqiqiy fayl biriktirish foydasiga BUTUNLAY olib tashlangan --
    pastdagi create_ir_attachment()'ga qarang.

    `phone` (2026-08-03, kengaytirildi): endi HAR DOIM (partner_id bor-yo'qligidan qat'i
    nazar) description matni OXIRIGA alohida "☎️ Aloqa" qatori sifatida ham qo'shiladi.
    Sabab: MAVJUD (partner_id bilan) lead'larda Odoo lead'ning `name` maydonini o'zi
    avtomatik yozib qo'yadi -- LIVE tekshirilgan (2026-08-03): bu qayta yozish partner'dan
    emas, balki description matnining O'ZIDAN olinar ekan (uzun bo'lsa ~63 belgigacha
    kesib) -- shu sabab description'ning BOSHIDA nima tursa, lead sarlavhasida ham o'sha
    ko'rinadi. Aynan shu sabab "☎️ Aloqa" qatori description OXIRIGA qo'shiladi (BOSHIGA
    emas) -- aks holda har bir buyurtma lead sarlavhasida haqiqiy buyurtma matni o'rniga
    "☎️ Aloqa: ..." bilan boshlanib qolar edi. Bu ayniqsa "Yangi obyekt yaratish" oqimida
    muhim -- MA'LUM mijoz yangi obyekt uchun boshqa (o'zinikidan farqli) menejer ismi/
    telefonini kiritishi mumkin, bu holda partner_id orqali ko'rinadigan standart ism/
    telefon noto'g'ri (asosiy mijozniki) bo'lib qoladi, description esa description
    OXIRIDA bo'lsa ham baribir kafolatlangan ko'rinish joyi bo'lib qoladi."""
    description = f"{order_details}\n\n☎️ Aloqa: {partner_name}, {phone}" if phone else order_details
    vals = {
        "name": f"Telegram buyurtma - {partner_name}",
        "description": description,
        "x_telegram_chat_id": str(chat_id),
        "team_id": TELEGRAM_ORDER_CRM_TEAM_ID,
        "stage_id": TELEGRAM_ORDER_CRM_STAGE_ID,
        "source_id": TELEGRAM_ORDER_UTM_SOURCE_ID,
    }
    if partner_id is not None:
        vals["partner_id"] = partner_id
    else:
        vals["contact_name"] = partner_name
        if phone:
            vals["phone"] = phone
    if object_name:
        vals["x_telegram_object_name"] = object_name
    if latitude is not None and longitude is not None:
        vals["x_telegram_latitude"] = latitude
        vals["x_telegram_longitude"] = longitude

    return _execute("crm.lead", "create", vals)


def create_ir_attachment(lead_id: int, filename: str, base64_data: str, mimetype: str) -> int | None:
    """"Buyurtma berish"da mijoz yuborgan rasm/video/ovozli xabarni yaratilgan crm.lead'ga
    HAQIQIY Odoo fayl biriktirmasi (ir.attachment) sifatida yozadi -- Odoo'ning o'z
    "Attachments"/chatter bo'limida ko'rinadi (2026-08-03, `message_attachment_count`
    orqali tasdiqlangan). `name`/`datas`(base64)/`res_model`/`res_id`/`mimetype` --
    standart, custom field emas (fields_get orqali tekshirilgan). Muhim: shu Odoo
    instansiyasidagi API foydalanuvchisi ir.attachment'ni O'CHIRISH huquqiga EGA EMAS
    (haqiqiy create/unlink orqali tasdiqlangan, "Sizda ushbu hujjat faylini o'chirish
    huquqi yo'q" xatosi) -- shu sabab bu funksiya faqat CREATE qiladi, hech qachon keyin
    o'chirishga urinmaydi; xato bo'lgan biriktirma bo'lsa Odoo administratori qo'lda
    o'chirishi kerak. Muvaffaqiyatsiz bo'lsa None qaytaradi -- chaqiruvchi tomon buni
    faqat log qilishi kerak, lead allaqachon yaratilgan bo'lgani uchun butun so'rovni
    muvaffaqiyatsiz deb belgilamaslik kerak."""
    vals = {
        "name": filename,
        "datas": base64_data,
        "res_model": "crm.lead",
        "res_id": lead_id,
        "mimetype": mimetype,
    }
    return _execute("ir.attachment", "create", vals)


def update_object_contact(
    object_id: int,
    phone: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> bool:
    """Bir obyektning (res.partner, delivery-turi child) telefon raqami va/yoki GPS
    lokatsiyasini yangilaydi -- mijoz botning "Ma'lumotlarni tahrirlash" oqimidan
    o'zgartirganda ishlatiladi (2026-08-05). `phone` yolg'iz, yoki `latitude`+`longitude`
    juftligi yolg'iz, yoki ikkalasi birga berilishi mumkin -- chaqiruvchi tomon (FastAPI)
    faqat kerakli maydon(lar)ni uzatadi. Live-verified (2026-08-05, haqiqiy test obyektida
    yozib-o'qib-qaytarib tekshirilgan): API foydalanuvchisi res.partner'ga YOZISH huquqiga
    ega (avvalgi ir.attachment-bilan solishtirganda -- u yerda faqat CREATE bor edi, bu
    yerda esa to'liq WRITE ishlaydi)."""
    vals: dict = {}
    if phone is not None:
        vals["phone"] = phone
    if latitude is not None and longitude is not None:
        vals["partner_latitude"] = latitude
        vals["partner_longitude"] = longitude

    if not vals:
        return False

    result = _execute("res.partner", "write", [object_id], vals)
    return bool(result)


def post_object_edit_chatter_message(object_id: int, message_body: str) -> bool:
    """Obyektning res.partner kartochkasiga (Odoo'ning o'z "chatter"/xabarlar tarixiga)
    bitta xabar yozadi -- Telegram orqali qilingan o'zgarishni Odoo xodimi shu obyektni
    ochganda DARHOL ko'rishi uchun (2026-08-06, mijoz aniq so'ragan: "bu odooga habar
    bo'lib borishi kerak"). Standart `mail.thread` metodi (`message_post`) orqali --
    live-verified: real xabar yozildi, haqiqiy mail.message ID qaytdi. Muhim chaqiruv
    tafsiloti: `body` KWARG sifatida uzatilishi SHART (`_execute(..., body=...)`), positional
    dict sifatida EMAS -- aks holda "message_post() takes 1 positional argument but 2
    were given" xatosi chiqadi (bu funksiya yaratilayotganda live tekshirilib topilgan)."""
    result = _execute("res.partner", "message_post", [object_id], body=message_body)
    return bool(result)


def get_partner_manager(partner_id: int) -> dict | None:
    """Mijozga Odoo'da biriktirilgan mas'ul xodim (Sotuvchi/Menejer -- standart
    res.partner.user_id maydoni, custom emas) ma'lumotini qaytaradi -- "Yordam"
    bo'limida mijozga o'z menejeri bilan bog'lanish uchun ko'rsatiladi (2026-08-06).
    `user_id` o'rnatilmagan (False) BO'LSA, yoki biriktirilgan foydalanuvchi (res.users)
    o'zi topilmasa, None qaytaradi -- chaqiruvchi tomon bu holatda umumiy (standart)
    support xabarini ko'rsatishi kerak. Live-verified: res.partner.user_id -> res.users
    (id=2, "Hasanxo'jayev Sardor G'anisher o'g'li") -> phone to'g'ri qaytdi."""
    partner = _execute("res.partner", "read", [partner_id], ["user_id"])
    if not partner or not partner[0].get("user_id"):
        return None

    user_id = partner[0]["user_id"][0]
    user = _execute("res.users", "read", [user_id], ["name", "phone", "mobile"])
    if not user:
        return None

    return {"name": user[0].get("name"), "phone": user[0].get("phone") or user[0].get("mobile")}


def check_odoo_connection() -> bool:
    """Odoo API kaliti (yoki umuman ulanish) ishlab turibdimi -- majburiy qayta
    autentifikatsiya qilib tekshiradi (keshlangan `uid`ga ishonmaydi, aks holda eskirgan
    kalit ham "ishlayapti" deb ko'rinishi mumkin edi). API kaliti muddati tugab qolganda
    (odatda 90 kunda) buni aniqlash uchun ishlatiladi -- qarang: `odoo_health_check_loop`
    (`app/utils.py`)."""
    return _authenticate(force=True) is not None
