"""Odoo'dan (real vaqtli, app.odoo_client orqali) olingan balans va mijoz ma'lumotini
Telegram uchun o'qiladigan matnga formatlaydi. MUHIM: x_uzs_balance/x_usd_balance
maydonlaridagi belgi (musbat/manfiy) Odoo'da mijozga ko'rsatilishi kerak bo'lgan ma'noga
TESKARI saqlanadi -- shu sabab _format_amount har doim belgini invert qiladi (pastga
qarang)."""

from app.i18n import t
from app.utils import normalize_uz_phone

CURRENCY_ICONS = {"UZS": "💰", "USD": "💵"}


def invert_balance_sign(value: float | int | None) -> float:
    """Odoo'dagi x_uzs_balance/x_usd_balance belgisini (musbat/manfiy) mijozga
    ko'rsatilishi/hisoblanishi kerak bo'lgan ma'noga o'giradi -- TESKARI, yuqoridagi
    modul docstringiga qarang (2026-08-02, mijoz tomonidan aniq tasdiqlangan qoida).
    Statistika PDF'idagi oylik "Balans" ustuni (statistics_pdf.py) ham shu funksiyadan
    olingan qiymatdan boshlanadi -- Malumotlarim bilan bir xil balans ko'rsatilishi
    kerak (2026-08-04, mijoz aniq so'ragan)."""
    return -(value or 0.0)


def _format_amount(value: float | int | None) -> str:
    """Odoo'dagi x_uzs_balance/x_usd_balance belgisi (musbat/manfiy) mijozga ko'rsatilishi
    kerak bo'lgan ma'noga TESKARI -- shu sabab mijozga chiqarishdan oldin belgi har doim
    invert qilinadi (2026-08-02, mijoz tomonidan aniq tasdiqlangan qoida: Odoo'da "-"
    bo'lsa mijozga "-"siz, Odoo'da "-" bo'lmasa mijozga "-" bilan ko'rsatiladi)."""
    inverted = invert_balance_sign(value)
    if inverted == 0:
        inverted = 0  # -0.0 float manfiy nolni "-0" ko'rinishida chiqarib yubormasligi uchun
    return f"{inverted:,.2f}".rstrip("0").rstrip(".")


def _format_balance_lines(balances: dict, label_key: str, lang: str) -> list[str]:
    """Har bir valyuta (UZS, USD) uchun, FAQAT nolmas bo'lsa, bitta qator qaytaradi --
    2026-08-03, mijoz aniq so'ragan: umumiy (mijozning bosh) balansida ham xuddi obyekt
    balanslari (_format_object_balance_lines) kabi -- so'mda bo'lsa faqat so'm, $ bo'lsa
    faqat $, ikkalasi ham 0 bo'lsa hech narsa ko'rsatilmaydi (avval umumiy balans 0
    bo'lsa ham har doim ko'rsatilar edi)."""
    lines = []
    label = t(label_key, lang)
    for currency, icon in CURRENCY_ICONS.items():
        value = balances.get(currency)
        if not value:
            continue
        lines.append(f"{icon} {label} ({currency}): {_format_amount(value)}")
    return lines


def _format_object_balance_lines(obj: dict, lang: str) -> list[str]:
    """Bitta obyektning UZS/USD balansini qatorlarga formatlaydi -- FAQAT nolmas
    qiymatlar ko'rsatiladi (2026-08-03, mijoz aniq so'ragan: obyekt balansi 0 bo'lsa
    umuman ko'rsatilmaydi, bekorga joy band qilinmaydi)."""
    label = t("info_balance_label", lang)
    lines = []
    for currency, icon in CURRENCY_ICONS.items():
        value = obj.get(f"x_{currency.lower()}_balance")
        if not value:
            continue
        lines.append(f"  {icon} {label} ({currency}): {_format_amount(value)}")
    return lines


def _format_object_location_line(obj: dict, lang: str) -> str:
    """Obyektning GPS lokatsiyasi (partner_latitude/partner_longitude) qatorini
    qaytaradi -- HAR DOIM (avval lokatsiya bo'lmasa qator umuman chiqmas edi, 2026-08-04,
    mijoz aniq so'ragan: bo'lmasa ham "Lokatsiya: yo'q" ko'rinsin). 0.0/0.0 Odoo'da
    "lokatsiya kiritilmagan" degani (get_customer_objects'dagi izohga qarang).

    Lokatsiya bor bo'lsa, uzun `https://maps.google.com/?q=lat,lon` URL'ini to'g'ridan-
    to'g'ri ko'rsatish o'rniga qisqa "Bor"/"Есть" matnli HTML havola sifatida
    beriladi (2026-08-04, mijoz aniq so'ragan: xabar uzun bo'lib chiqmasin) -- shu sabab
    bu xabar chaqiruvchi tomonidan albatta `parse_mode="HTML"` bilan yuborilishi kerak
    (app/bot/handlers/menu.py'dagi "info" bo'limiga qarang)."""
    label = t("info_location_label", lang)
    lat, lon = obj.get("partner_latitude"), obj.get("partner_longitude")
    if not lat or not lon:
        return f"  📍 {label}: {t('balance_none_value', lang)}"
    link_text = t("info_location_link_text", lang)
    return f'  📍 {label}: <a href="https://maps.google.com/?q={lat},{lon}">{link_text}</a>'


def format_info_message(partner: dict, objects: list[dict], lang: str) -> str:
    """Mijozning Odoo'dagi F.I.Sh, telefon raqamini va umumiy balansini (FAQAT nolmas
    valyutalar) shu tartibda (F.I.Sh -> telefon -> balans, 2026-08-05, mijoz aniq
    so'ragan) bir marta ko'rsatadi, so'ngra har bir obyektni o'ZINING alohida blokida:
    nomi (sarlavha), shu obyektga qaraydigan (mas'ul) shaxsning telefon raqami (bor
    bo'lsa -- 2026-08-03, live tekshirilgan: ko'p obyektda asosiy mijoz raqamidan
    FARQLI), GPS lokatsiyasi (bor bo'lsa qisqa "Bor" havolasi, bo'lmasa "yo'q" --
    2026-08-04) va o'z UZS/USD balansi (faqat nolmas bo'lsa) bilan ko'rsatadi -- shu
    tartibda (telefon -> lokatsiya -> balans, 2026-08-05, mijoz aniq so'ragan). Har
    ikkala joydagi telefon raqami ham `normalize_uz_phone()` orqali ajratkichlardan
    tozalanadi VA kod (+998) qo'shiladi (agar 9 xonali kodsiz raqam bo'lsa) --
    2026-08-05/06, mijoz aniq so'ragan. Standart manzil maydonlari (street/city) to'ldirilmagani uchun obyekt
    nomi lokatsiya (nom sifatida) sifatida ishlatiladi (odoo_client.py'dagi izohga
    qarang) -- GPS lokatsiyasi shu nomga QO'SHIMCHA, uni almashtirmaydi.

    MUHIM: natija HTML belgilarni (<a href=...>) o'z ichiga olishi mumkin -- chaqiruvchi
    tomon (app/bot/handlers/menu.py) buni albatta `parse_mode="HTML"` bilan yuborishi
    kerak, aks holda havola matn sifatida ko'rinadi."""
    none_value = t("balance_none_value", lang)
    name = partner.get("name") or none_value
    raw_phone = partner.get("phone") or partner.get("mobile")
    # Odoo'da ba'zan telefon maydonida raqamsiz axlat qiymat ("q" kabi) uchraydi --
    # normalize_uz_phone bunday holatda None qaytaradi, shu sabab none_value'ga
    # tushiriladi (bo'sh "Telefon: " qatori chiqmasligi uchun).
    phone = normalize_uz_phone(raw_phone) or none_value
    balances = {"UZS": partner.get("x_uzs_balance"), "USD": partner.get("x_usd_balance")}

    lines = [
        f"👤 {t('info_name_label', lang)}: {name}",
        f"📞 {t('info_phone_label', lang)}: {phone}",
        *_format_balance_lines(balances, "info_balance_label", lang),
    ]

    if not objects:
        lines.append(f"🏢 {t('info_objects_label', lang)}: {none_value}")
        return "\n".join(lines)

    for obj in objects:
        lines.append("")
        lines.append(f"🏢 {obj['name']}")
        obj_phone = normalize_uz_phone(obj.get("phone") or obj.get("mobile"))
        if obj_phone:
            lines.append(f"  📞 {obj_phone}")
        lines.append(_format_object_location_line(obj, lang))
        lines.extend(_format_object_balance_lines(obj, lang))

    return "\n".join(lines)
