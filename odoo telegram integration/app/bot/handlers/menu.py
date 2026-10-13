"""
Asosiy menyu -- doimiy pastki (Reply) klaviatura sifatida: Buyurtma berish, Tijorat
takliflarim, Yordam, Malumotlarim, Statistika. Alohida "hamburger"/trigger tugma yo'q --
Telegram bu turdagi klaviaturani ochish/yopish uchun xabar yozish maydoni yonida
o'zining tabiiy (native) belgisini avtomatik ko'rsatadi. Tijorat takliflarim va
Malumotlarim FastAPI'dagi mos `/api/v1/bot/*` endpointi orqali Odoo'ga real vaqtli
so'rov yuboradi (Malumotlarim 2026-07-30'dan boshlab F.I.Sh/telefon/obyektlar bilan
birga umumiy balansni ham ko'rsatadi -- alohida "Balans" tugmasi shu sababli olib
tashlandi); Yordam va Buyurtma berish esa FastAPI'ga murojaat qilmasdan shu faylning
o'zida boshqariladi. Statistika (2026-08-04) -- FastAPI'dan faqat xom oylik ma'lumotni
oladi, PDF+grafikni esa shu yerda (bot konteynerida, app.bot.statistics_pdf orqali)
quradi -- FastAPI'ni faqat Odoo-ma'lumot-manbai sifatida engil saqlash qoidasiga rioya
qilib (taqdimot mantig'i har doim bot tomonida bo'lgani kabi, masalan format_info_message).
"""

import asyncio
import logging
import re
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.enums import ContentType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, LinkPreviewOptions, Message

from app.bot.internal_api import get_internal_client
from app.bot.keyboards import (
    get_cancel_keyboard,
    get_edit_info_button_keyboard,
    get_location_confirm_keyboard,
    get_location_request_keyboard,
    get_menu_reply_keyboard,
    get_order_new_customer_keyboard,
    get_order_object_list_keyboard,
    get_phone_request_keyboard,
)
from app.bot.states import OrderPlacementState
from app.bot.statistics_pdf import build_statistics_pdf
from app.config import settings
from app.database import async_session_maker
from app.i18n import LANGUAGES, t
from app.utils import get_user_language

logger = logging.getLogger("aiogram_bot")

router = Router(name="menu")

# bo'lim nomi -> FastAPI endpoint yo'li
MENU_ENDPOINTS = {
    "orders": "/api/v1/bot/orders",
    "quotation": "/api/v1/bot/quotation",
    "support": "/api/v1/bot/support",
    "info": "/api/v1/bot/info",
    "statistics": "/api/v1/bot/statistics",
}

# Har bir bo'limning barcha tildagi tugma matni -> bo'lim nomi (foydalanuvchi bot
# tilidan qat'i nazar, xuddi shu matnli tugmani bosishi kifoya)
SECTION_TEXT_TO_KEY = {t(f"menu_{section}", lang): section for section in MENU_ENDPOINTS for lang in LANGUAGES}

# "Rad etish sababi" / "O'zgartirish so'rovi" / "Buyurtma berish" kabi erkin matn
# kutayotgan FSM holatlari (orders.py va shu fayldagi handler_order_* funksiyalari)
# ushbu matnlardan birini haqiqiy javob sifatida QABUL QILMASLIGI kerak -- bular
# navigatsiya: foydalanuvchi javob yozish o'rniga boshqa menyu bo'limiga yoki bosh
# menyuga o'tib ketgan bo'ladi.
RESERVED_NAVIGATION_COMMANDS = {"/start", "/menu"}


def is_reserved_navigation_text(text: str | None) -> bool:
    """True agar matn menyu tugmasi yorlig'iga yoki /start, /menu buyruqlariga mos kelsa."""
    if not text:
        return False
    return text in SECTION_TEXT_TO_KEY or text in RESERVED_NAVIGATION_COMMANDS


# "Xaritadan tanlayman" tugmasining barcha tildagi matni -- get_location_request_keyboard
# (app/bot/keyboards.py) shu tugmani chiqaradi, handle_order_location_map_choice shu
# to'plam bo'yicha ushlaydi.
MAP_CHOICE_BUTTON_TEXTS = {t("btn_choose_from_map", lang) for lang in LANGUAGES}

# "Bekor qilish" tugmasining barcha tildagi matni (2026-08-03) -- ism/telefon so'ralgandan
# keyingi bosqichlarda (manzil/lokatsiya/buyurtma tafsiloti) get_cancel_keyboard yoki
# get_location_request_keyboard orqali ko'rsatiladi. Har bir shu bosqichdagi handler
# (handle_order_address_text, handle_order_location_invalid, handle_order_placement) shu
# to'plam bo'yicha tekshiradi va FSM holatini tozalab oqimdan chiqaradi.
CANCEL_BUTTON_TEXTS = {t("btn_cancel_order", lang) for lang in LANGUAGES}


async def fetch_menu_section(
    section: str,
    chat_id: int,
    username: str | None,
    lang: str,
    scope: str | None = None,
    period: str | None = None,
    object_name: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Berilgan bo'lim uchun FastAPI'dan javob matnini oladi (xatoda placeholder qaytaradi)."""
    endpoint = MENU_ENDPOINTS[section]
    text = t("menu_placeholder", lang)
    payload = {
        "chat_id": chat_id,
        "username": username,
        "lang": lang,
        "scope": scope,
        "period": period,
        "object_name": object_name,
        "start_date": start_date,
        "end_date": end_date,
    }
    try:
        client = get_internal_client()
        resp = await client.post(
            endpoint,
            json=payload,
            headers={"X-API-Key": settings.API_KEY},
        )
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("message") or text
    except Exception:
        logger.exception("Menyu endpointiga so'rov yuborishda xatolik: %s", endpoint)
    return text


async def fetch_order_customer_context(chat_id: int) -> dict:
    """"Buyurtma berish" uchun: mijozni Odoo'dan (chat_id bo'yicha) qidiradi -- topilsa
    ism/telefon/obyektlarini, topilmasa yoki xato yuz bersa found=False qaytaradi."""
    try:
        client = get_internal_client()
        resp = await client.get(
            "/api/v1/bot/order-customer-lookup",
            params={"chat_id": chat_id},
            headers={"X-API-Key": settings.API_KEY},
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        logger.exception("Buyurtma uchun mijozni Odoo'dan qidirishda xatolik")
    return {"found": False, "partner_name": None, "partner_phone": None, "objects": []}


async def fetch_statistics_data(chat_id: int, username: str | None, lang: str) -> dict:
    """"Statistika" tugmasi uchun: joriy yilning to'liq (Yanvar->Dekabr) buyurtmalar/
    bekor qilinganlar va obyektlar bo'yicha guruhlangan ma'lumotni FastAPI'dan oladi.
    `found=False` -- mijoz Odoo'da topilmadi; `months=None` -- Odoo so'rovi xato bergan
    (tarmoq/autentifikatsiya) -- ikkalasi ham chaqiruvchi tomonda alohida xabar bilan
    ko'rsatiladi (0 xarajatdan farqlanishi kerak)."""
    try:
        client = get_internal_client()
        resp = await client.post(
            "/api/v1/bot/statistics",
            json={"chat_id": chat_id, "username": username, "lang": lang},
            headers={"X-API-Key": settings.API_KEY},
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        logger.exception("Statistika ma'lumotini FastAPI'dan olishda xatolik")
    return {"found": False, "months": None}


async def submit_object_location(
    partner_id: int,
    object_name: str,
    chat_id: int,
    latitude: float,
    longitude: float,
    object_id: int | None = None,
    is_new_object: bool = False,
    username: str | None = None,
    partner_name: str | None = None,
    partner_phone: str | None = None,
) -> bool:
    """Obyekt (mavjud tanlangan yoki yangi yaratilgan) uchun GPS lokatsiyasini FastAPI'ga
    yozadi -- Odoo GET /api/v1/object-locations orqali keyinroq o'zi so'rab oladi. Xato
    bo'lsa faqat log qilinadi, buyurtma oqimini to'xtatmaydi (lokatsiya sinxronizatsiyasi
    muvaffaqiyatsiz bo'lsa ham buyurtmaning o'zi baribir davom etishi kerak). partner_name/
    partner_phone -- ular Odoo'da partner_id orqali baribir topiladi, lekin yangi obyekt
    (is_new_object=True, object_id yo'q) uchun Odoo xodimi obyektni QO'LDA yaratishi kerak
    (2026-08-02'dagi izohga qarang), shu safar kim uchun ekanini partner_id'ni qayta
    qidirmasdan bir qarashda ko'rishi uchun qo'shildi."""
    payload = {
        "partner_id": partner_id,
        "object_id": object_id,
        "object_name": object_name,
        "is_new_object": is_new_object,
        "chat_id": chat_id,
        "latitude": latitude,
        "longitude": longitude,
        "username": username,
        "partner_name": partner_name,
        "partner_phone": partner_phone,
    }
    try:
        client = get_internal_client()
        resp = await client.post(
            "/api/v1/bot/object-location-submit",
            json=payload,
            headers={"X-API-Key": settings.API_KEY},
        )
        if resp.status_code == 200:
            return True
        logger.warning("object-location-submit kutilmagan status qaytardi: %s", resp.status_code)
    except Exception:
        logger.exception("Obyekt lokatsiyasini FastAPI'ga yozishda xatolik")
    return False


async def submit_order_to_odoo(
    partner_id: int | None,
    partner_name: str,
    chat_id: int,
    order_details: str,
    object_name: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    partner_phone: str | None = None,
    photo_path: str | None = None,
    video_path: str | None = None,
    audio_path: str | None = None,
    document_path: str | None = None,
) -> int | None:
    """Buyurtma so'rovini Odoo'ga (crm.lead) yozadi -- Odoo'da MAVJUD (partner_id berilgan)
    va butunlay yangi (partner_id=None, partner_phone bilan) mijozlar uchun bir xil.
    `photo_path`/`video_path`/`audio_path`/`document_path` -- `_download_order_media()`
    allaqachon ORDER_MEDIA_DIR'ga yuklab olgan faylning nomi (2026-08-03); FastAPI shu
    faylni o'qib Odoo'ga HAQIQIY ir.attachment sifatida biriktiradi. Muvaffaqiyatsiz
    bo'lsa (Odoo xatosi yoki tarmoq xatosi) None qaytaradi -- chaqiruvchi tomon bu holda
    mijozga xatolik xabarini ko'rsatishi kerak (call-center guruhiga forward qilish
    2026-08-03'da olib tashlandi)."""
    payload = {
        "partner_id": partner_id,
        "partner_name": partner_name,
        "partner_phone": partner_phone,
        "chat_id": chat_id,
        "order_details": order_details,
        "object_name": object_name,
        "latitude": latitude,
        "longitude": longitude,
        "photo_path": photo_path,
        "video_path": video_path,
        "audio_path": audio_path,
        "document_path": document_path,
    }
    try:
        client = get_internal_client()
        resp = await client.post(
            "/api/v1/bot/order-submit",
            json=payload,
            headers={"X-API-Key": settings.API_KEY},
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                return data.get("lead_id")
    except Exception:
        logger.exception("Buyurtmani Odoo'ga (crm.lead) yozishda xatolik")
    return None


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    """/menu komandasi -- doimiy menyu klaviaturasini (qayta) biriktiradi va foydalanuvchini
    yarim qolgan FSM oqimidan (masalan rad etish sababi, buyurtma joylashtirish) chiqaradi."""
    await state.clear()
    async with async_session_maker() as session:
        lang = await get_user_language(session, message.chat.id)
    await message.answer(t("menu_title", lang), reply_markup=get_menu_reply_keyboard(lang))


@router.message(F.text.in_(SECTION_TEXT_TO_KEY.keys()))
async def handle_menu_section_button(message: Message, state: FSMContext):
    """Menyudagi bo'lim tugmalaridan biri bosilganda mos FastAPI endpointiga so'rov yuboradi.

    Avval joriy FSM holatini tozalaydi -- foydalanuvchi biror yarim qolgan oqim (masalan
    yangi mijoz ismini/telefonini/lokatsiyasini kutish) o'rtasida menyuga o'tib ketgan
    bo'lishi mumkin; "Buyurtma berish" qayta bosilsa quyida holat qaytadan o'rnatiladi.
    """
    await state.clear()
    section = SECTION_TEXT_TO_KEY[message.text]
    chat_id = message.chat.id

    async with async_session_maker() as session:
        lang = await get_user_language(session, chat_id)

    if section == "support":
        # Yordam -- 2026-08-06: endi Odoo'dan mijozga biriktirilgan menejerni so'raydi
        # (FastAPI /api/v1/bot/support orqali) -- topilsa uning ismi/raqami, topilmasa
        # (menejer yo'q yoki mijoz Odoo'da yo'q) umumiy support_contact_message
        # ko'rsatiladi. Boshqa bo'limlar bilan bir xil fetch_menu_section yo'li.
        username = message.from_user.username if message.from_user else None
        text = await fetch_menu_section(section, chat_id, username, lang)
        await message.answer(text)
        return

    if section == "info":
        # Malumotlarim -- format_info_message (FastAPI/app.balance) natijasida obyekt
        # lokatsiyasi bor bo'lsa qisqa "Bor" HTML havolasi bo'lishi mumkin (2026-08-04,
        # mijoz aniq so'ragan: uzun URL o'rniga) -- shu sabab bu bo'lim uchun ALOHIDA,
        # parse_mode="HTML" bilan yuboriladi (boshqa bo'limlar -- masalan Tijorat
        # takliflarim -- umumiy pastdagi yo'l bilan, HTML'siz yuboriladi).
        # `link_preview_options=is_disabled` -- aks holda Telegram xabar oxiriga
        # havola uchun avtomatik xarita ko'rinishidagi preview karta qo'shib qo'yadi
        # (2026-08-04, mijoz aniq so'ragan: shu karta chiqmasin).
        username = message.from_user.username if message.from_user else None
        text = await fetch_menu_section(section, chat_id, username, lang)
        await message.answer(
            text,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            reply_markup=get_edit_info_button_keyboard(lang),
        )
        return

    if section == "statistics":
        # Statistika -- joriy yilning to'liq (Yanvar->Dekabr) buyurtmalar/bekor
        # qilinganlar ma'lumotini FastAPI'dan (faqat xom ma'lumot, arxitektura
        # qoidasiga ko'ra) oladi, PDF+jadval+grafikni shu yerda (bot konteynerida)
        # quradi va hujjat sifatida yuboradi. Mijoz Odoo'da topilmasa yoki Odoo
        # so'rovi xato bersa (months=None), mos xabar ko'rsatiladi -- 0 xarajat
        # bilan adashtirilmasligi kerak.
        username = message.from_user.username if message.from_user else None
        data = await fetch_statistics_data(chat_id, username, lang)

        if not data.get("found"):
            await message.answer(t("balance_no_partner", lang))
            return
        months = data.get("months")
        if not months:
            await message.answer(t("generic_error", lang))
            return

        try:
            # Tezlik (2026-08-05): matplotlib+fpdf2 render ~300-430ms CPU-bloklovchi ish --
            # to_thread'siz bu bot'ning YAGONA event loop'ini shu vaqtga to'liq to'xtatib
            # qo'yardi, ya'ni statistikani so'ragan mijoz kutayotgan paytda BOSHQA HECH
            # QANDAY foydalanuvchining xabari/tugma bosishi qabul qilinmasdi.
            pdf_bytes = await asyncio.to_thread(
                build_statistics_pdf,
                months,
                data.get("partner_name") or "-",
                lang,
            )
        except Exception:
            logger.exception("Statistika PDF'ini yaratishda xatolik (chat_id=%s)", chat_id)
            await message.answer(t("generic_error", lang))
            return

        await message.answer_document(
            BufferedInputFile(pdf_bytes, filename="Buyurtmalar statistikasi.pdf"),
        )
        return

    if section == "orders":
        # Buyurtma berish -- avval mijozni Odoo'dan (chat_id bo'yicha) qidiramiz: obyekti
        # bo'lsa obyekt tanlash (+ yangi obyekt yaratish tugmasi) ro'yxatiga, obyekti
        # bo'lmasa to'g'ridan-to'g'ri yangi obyekt yaratish (qabul qiluvchi ism/telefon)
        # qadamiga, Odoo'da umuman topilmasa "yangi mijoz" oqimiga yo'naltiramiz.
        context = await fetch_order_customer_context(chat_id)

        if not context.get("found"):
            await state.update_data(lang=lang, is_new=True)
            await message.answer(
                t("order_no_partner_found", lang), reply_markup=get_order_new_customer_keyboard(lang)
            )
            return

        base_data = {
            "lang": lang,
            "is_new": False,
            "partner_id": context.get("partner_id"),
            "partner_name": context.get("partner_name"),
            "partner_phone": context.get("partner_phone"),
        }
        objects = context.get("objects") or []

        if not objects:
            # Mijozning birinchi obyekti -- 2026-08-05, mijoz aniq so'ragan: ism/telefon
            # qayta so'ralmaydi (2026-08-03'da qo'shilgan qadam olib tashlandi), to'g'ridan-
            # to'g'ri obyekt nomi (manzil matni) so'raladi -- asosiy mijozning o'z
            # partner_name/partner_phone'i (base_data) ishlatiladi.
            await state.update_data(**base_data, is_new_object=True, object_id=None)
            await state.set_state(OrderPlacementState.waiting_for_address_text)
            await message.answer(t("order_ask_address_text", lang), reply_markup=get_cancel_keyboard(lang))
            return

        # Obyekt soni 1 yoki ko'p bo'lsin -- endi har doim ro'yxat ko'rsatiladi (avtomatik
        # tanlanmaydi), chunki tanlangan mavjud obyekt uchun ham lokatsiya so'raladi va
        # ro'yxat ostida "Yangi obyekt yaratish" tugmasi ham bor.
        await state.update_data(**base_data, objects=objects)
        await message.answer(
            t("balance_choose_object", lang), reply_markup=get_order_object_list_keyboard(lang, objects)
        )
        return

    username = message.from_user.username if message.from_user else None
    text = await fetch_menu_section(section, chat_id, username, lang)
    await message.answer(text)


async def _download_order_media(
    message: Message, bot: Bot
) -> tuple[str | None, str | None, str | None, str | None]:
    """Buyurtma tafsiloti sifatida kelgan rasm/video/ovozli xabar/fayl(hujjat)ni (agar
    bo'lsa) ORDER_MEDIA_DIR'ga yuklab oladi va (photo_filename, video_filename,
    audio_filename, document_filename) qaytaradi -- shu to'rt qiymatdan faqat bittasi
    to'ldiriladi, chunki bitta Telegram xabarida bir vaqtning o'zida faqat bitta media
    turi bo'lishi mumkin. Fayl nomi RELATIVE (to'liq yo'l emas) -- FastAPI shu nomdan
    ORDER_MEDIA_DIR'dagi faylni topib Odoo'ga ir.attachment sifatida yuklaydi. Fayl
    (document) uchun asl kengaytma (masalan ".pdf") saqlab qolinadi -- mimetype'ni
    FastAPI shundan aniqlaydi (2026-08-03); asl fayl NOMI esa saqlanmaydi (xavfsizlik
    uchun -- faqat kengaytma path.suffix orqali olinadi, yo'l qismi hisobga olinmaydi).
    Yuklab olishda xato bo'lsa (masalan tarmoq) shu media uchun None qaytadi --
    buyurtmaning matn qismi baribir Odoo'ga yozilishi kerak, faqat media biriktirilmay
    qoladi."""
    media_dir = Path(settings.ORDER_MEDIA_DIR)
    media_dir.mkdir(parents=True, exist_ok=True)

    try:
        if message.content_type == ContentType.PHOTO:
            filename = f"{message.chat.id}_{message.message_id}.jpg"
            await bot.download(message.photo[-1].file_id, destination=media_dir / filename)
            return filename, None, None, None
        if message.content_type == ContentType.VIDEO:
            filename = f"{message.chat.id}_{message.message_id}.mp4"
            await bot.download(message.video.file_id, destination=media_dir / filename)
            return None, filename, None, None
        if message.content_type == ContentType.VOICE:
            filename = f"{message.chat.id}_{message.message_id}.ogg"
            await bot.download(message.voice.file_id, destination=media_dir / filename)
            return None, None, filename, None
        if message.content_type == ContentType.DOCUMENT:
            suffix = Path(message.document.file_name or "").suffix
            filename = f"{message.chat.id}_{message.message_id}{suffix}"
            await bot.download(message.document.file_id, destination=media_dir / filename)
            return None, None, None, filename
    except Exception:
        logger.exception("Buyurtma mediasini yuklab olishda xatolik (chat_id=%s)", message.chat.id)

    return None, None, None, None


@router.callback_query(F.data.startswith("order_objsel_"))
async def handle_order_object_selected(callback: CallbackQuery, state: FSMContext):
    """Buyurtma berish -- mavjud obyektlar ro'yxatidan biri tanlangach: agar shu obyekt
    uchun Odoo'da lokatsiya ALLAQACHON bo'lsa, endi qayta so'RALMAYDI (2026-08-03,
    ikkinchi marta qayta ko'rib chiqilgan -- avval "har doim qayta so'ralsin" deyilgan
    edi, endi mijoz buni "har safar qayta yuborish shart emas, ko'rsatib tasdiqlatish
    kifoya" deb aniqlashtirdi) -- buning o'rniga saqlangan lokatsiya (native Telegram
    location xabari sifatida) ko'rsatiladi va "✅ Buyurtma berish"/"📍 Lokatsiyani
    o'zgartirish" tugmalari chiqadi (waiting_for_location_confirmation, handle_order_
    location_keep/_change'ga qarang). Lokatsiya umuman bo'lmasa (masalan hali hech kim
    kiritmagan) hamon to'g'ridan-to'g'ri yangi lokatsiya so'raladi -- ReplyKeyboardMarkup
    talab qilgani uchun (callback xabarini edit qilib bo'lmaydi) alohida YANGI xabar
    yuboriladi."""
    try:
        object_id = int(callback.data.removeprefix("order_objsel_"))
    except ValueError:
        await callback.answer()
        return

    data = await state.get_data()
    lang = data.get("lang", "uz")
    objects = data.get("objects") or []
    selected = next((o for o in objects if o.get("id") == object_id), None)

    if not selected:
        await callback.message.edit_text(t("balance_no_objects", lang))
        await callback.answer()
        return

    object_name = selected["name"]
    latitude, longitude = selected.get("latitude"), selected.get("longitude")

    await callback.message.edit_text(f"🏢 {object_name}")

    if latitude is not None and longitude is not None:
        await state.set_state(OrderPlacementState.waiting_for_location_confirmation)
        await state.update_data(
            object_id=object_id, object_name=object_name, is_new_object=False,
            latitude=latitude, longitude=longitude,
        )
        await callback.message.answer_location(latitude=latitude, longitude=longitude)
        await callback.message.answer(
            t("order_confirm_location", lang), reply_markup=get_location_confirm_keyboard(lang)
        )
        await callback.answer()
        return

    await state.set_state(OrderPlacementState.waiting_for_location)
    await state.update_data(object_id=object_id, object_name=object_name, is_new_object=False)
    await callback.message.answer(t("order_ask_location", lang), reply_markup=get_location_request_keyboard(lang))
    await callback.answer()


@router.callback_query(OrderPlacementState.waiting_for_location_confirmation, F.data == "order_loc_keep")
async def handle_order_location_keep(callback: CallbackQuery, state: FSMContext):
    """Saqlangan lokatsiya to'g'ri deb tasdiqlangach -- qayta FastAPI'ga yozib
    o'tirmasdan (Odoo'da bu lokatsiya allaqachon bor) to'g'ridan-to'g'ri buyurtma
    tafsilotiga o'tadi. latitude/longitude state'da allaqachon bor (obyekt tanlanganda
    yozilgan)."""
    data = await state.get_data()
    lang = data.get("lang", "uz")

    await state.set_state(OrderPlacementState.waiting_for_order)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("order_placement_prompt", lang), reply_markup=get_cancel_keyboard(lang))
    await callback.answer()


@router.callback_query(OrderPlacementState.waiting_for_location_confirmation, F.data == "order_loc_change")
async def handle_order_location_change(callback: CallbackQuery, state: FSMContext):
    """Saqlangan lokatsiya noto'g'ri deb topilgach -- yangi GPS lokatsiyasini so'raydi
    (handle_order_location qabul qiladi, xuddi yangi obyekt oqimidagi bilan bir xil)."""
    data = await state.get_data()
    lang = data.get("lang", "uz")

    await state.set_state(OrderPlacementState.waiting_for_location)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("order_ask_location", lang), reply_markup=get_location_request_keyboard(lang))
    await callback.answer()


@router.callback_query(F.data == "order_obj_new")
async def handle_order_new_object(callback: CallbackQuery, state: FSMContext):
    """Buyurtma berish -- mavjud obyektlar ro'yxati ostidagi "Yangi obyekt yaratish"
    tugmasi bosilganda: mijoz Odoo'da MA'LUM (partner_id bor), shu sabab to'g'ridan-
    to'g'ri obyekt nomi (manzil matni) so'raladi -- asosiy mijozning o'z partner_name/
    partner_phone'i ishlatiladi (2026-08-05, mijoz aniq so'ragan: qabul qiluvchi ismini
    qayta so'rash 2026-08-03'da qo'shilgan edi, endi olib tashlandi)."""
    data = await state.get_data()
    lang = data.get("lang", "uz")

    await state.update_data(is_new_object=True, object_id=None)
    await state.set_state(OrderPlacementState.waiting_for_address_text)
    await callback.message.edit_text(t("btn_create_new_object", lang))
    await callback.message.answer(t("order_ask_address_text", lang), reply_markup=get_cancel_keyboard(lang))
    await callback.answer()


@router.callback_query(F.data == "order_new_customer")
async def handle_order_new_customer(callback: CallbackQuery, state: FSMContext):
    """Buyurtma berish -- mijoz Odoo'da topilmagach "Yangi mijoz" tugmasi bosilganda, ismini so'raydi."""
    data = await state.get_data()
    lang = data.get("lang", "uz")
    await state.set_state(OrderPlacementState.waiting_for_new_name)
    await callback.message.edit_text(t("order_ask_new_name", lang))
    await callback.answer()


@router.message(OrderPlacementState.waiting_for_new_name)
async def handle_order_new_name(message: Message, state: FSMContext):
    """Buyurtma berish -- ismni qabul qiladi va telefon raqamini so'raydi. Ikki holatda
    ishlaydi (2026-08-03): (1) butunlay yangi mijoz (partner_id yo'q) -- bu ism/telefon
    keyinroq crm.lead'ning o'z contact_name/phone maydonlariga yoziladi; (2) Odoo'da
    MA'LUM mijoz yangi obyekt yaratayotganda (partner_id state'da allaqachon bor) -- bu
    holda ism/telefon o'sha YANGI obyekt uchun buyurtma qabul qiluvchining ma'lumoti
    bo'lib, mavjud partner_name/partner_phone'ni state'da ALMASHTIRADI (partner_id
    o'zgarmay qoladi, hech qachon bu yerda yozilmaydi)."""
    data = await state.get_data()
    lang = data.get("lang", "uz")
    name = (message.text or "").strip()
    if not name:
        await message.answer(t("order_ask_new_name", lang))
        return

    await state.update_data(partner_name=name)
    await state.set_state(OrderPlacementState.waiting_for_new_phone)
    await message.answer(t("order_ask_new_phone", lang), reply_markup=get_phone_request_keyboard(lang))


def _is_valid_phone_format(phone: str) -> bool:
    """998 kodi bilan (12 ta raqam) yoki kodsiz (9 ta raqam) -- 2026-08-05, mijoz
    aniq so'ragan: 1 ta raqam ko'p yoki kam bo'lsa ham noto'g'ri deb hisoblanadi."""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("998"):
        return len(digits) == 12
    return len(digits) == 9


@router.message(OrderPlacementState.waiting_for_new_phone)
async def handle_order_new_phone(message: Message, state: FSMContext):
    """Buyurtma berish -- telefon raqamini qabul qiladi (qo'lda yozilgan matn SIFATIDA,
    yoki Telegram'ning "kontakt ulashish" tugmasi orqali `message.contact` sifatida --
    2026-08-05) va manzilni (obyekt nomi, matn) so'raydi. handle_order_new_name'dagi
    izohga qarang -- ikkala oqim (yangi mijoz VA mavjud mijozning yangi obyekti) uchun
    ham ishlaydi.

    Qo'lda kiritilgan raqam FORMAT bo'yicha tekshiriladi (`_is_valid_phone_format`) --
    noto'g'ri bo'lsa (1 ta raqam ko'p/kam) xatolik xabari ko'rsatiladi va bosqich
    o'zgarmaydi, to'g'ri bo'lsa hech qanday qo'shimcha xabarsiz keyingi qadamga
    o'tiladi. Kontakt orqali kelgan raqam Telegram profilidan olingani uchun QAYTA
    tekshirilmaydi (ishonchli manba)."""
    data = await state.get_data()
    lang = data.get("lang", "uz")

    if message.contact:
        phone = message.contact.phone_number
    else:
        phone = (message.text or "").strip()
        if not phone:
            await message.answer(t("order_ask_new_phone", lang))
            return
        if not _is_valid_phone_format(phone):
            await message.answer(t("order_phone_invalid_format", lang))
            return

    await state.update_data(partner_phone=phone)
    await state.set_state(OrderPlacementState.waiting_for_address_text)
    await message.answer(t("order_ask_address_text", lang), reply_markup=get_cancel_keyboard(lang))


@router.message(OrderPlacementState.waiting_for_address_text)
async def handle_order_address_text(message: Message, state: FSMContext):
    """Buyurtma berish -- mijoz manzilni matn bilan kiritgach (bu keyinchalik "object_name"
    sifatida saqlanadi va Odoo'ga xuddi haqiqiy obyekt nomidek ko'rsatiladi), aniq GPS
    lokatsiyasini (xaritadan belgilashni) so'raydi. "Bekor qilish" bosilsa (2026-08-03)
    oqim shu yerdayoq to'xtatiladi -- manzil sifatida SAQLANMAYDI."""
    data = await state.get_data()
    lang = data.get("lang", "uz")

    if message.text in CANCEL_BUTTON_TEXTS:
        await state.clear()
        await message.answer(t("order_cancelled", lang), reply_markup=get_menu_reply_keyboard(lang))
        return

    address = (message.text or "").strip()
    if not address:
        await message.answer(t("order_ask_address_text", lang), reply_markup=get_cancel_keyboard(lang))
        return

    await state.update_data(object_name=address)
    await state.set_state(OrderPlacementState.waiting_for_location)
    await message.answer(t("order_ask_location", lang), reply_markup=get_location_request_keyboard(lang))


@router.message(OrderPlacementState.waiting_for_location, F.location)
async def handle_order_location(message: Message, state: FSMContext):
    """Buyurtma berish -- mijoz joylashuvini yuborgach (lat/long): agar mijoz Odoo'da
    MA'LUM bo'lsa (partner_id bor -- butunlay yangi mijoz oqimida bo'lmaydi), shu
    obyektning (mavjud yoki yangi yaratilayotgan) lokatsiyasi FastAPI'ga yoziladi, Odoo
    keyinroq GET orqali o'zi so'rab oladi. Sinxronizatsiya muvaffaqiyatsiz bo'lsa ham
    (submit_object_location o'zi log qiladi) buyurtma oqimi to'xtamaydi."""
    data = await state.get_data()
    lang = data.get("lang", "uz")
    latitude, longitude = message.location.latitude, message.location.longitude
    await state.update_data(latitude=latitude, longitude=longitude)

    if data.get("partner_id"):
        await submit_object_location(
            partner_id=data["partner_id"],
            object_id=data.get("object_id"),
            object_name=data.get("object_name") or "-",
            is_new_object=data.get("is_new_object", True),
            chat_id=message.chat.id,
            latitude=latitude,
            longitude=longitude,
            username=message.from_user.username if message.from_user else None,
            partner_name=data.get("partner_name"),
            partner_phone=data.get("partner_phone"),
        )

    await state.set_state(OrderPlacementState.waiting_for_order)
    await message.answer(t("order_placement_prompt", lang), reply_markup=get_cancel_keyboard(lang))


@router.message(OrderPlacementState.waiting_for_location, F.text.in_(MAP_CHOICE_BUTTON_TEXTS))
async def handle_order_location_map_choice(message: Message, state: FSMContext):
    """Buyurtma berish -- "Xaritadan tanlayman" tugmasi bosilganda. Bot API'da bitta tugma
    bosilishi bilan to'g'ridan-to'g'ri xarita-tanlash ekranini majburan ochib bo'lmaydi (faqat
    joriy joylashuvni so'rash mumkin), shu sabab Telegram'ning O'ZIDA mavjud xarita interfeysidan
    (📎 → Location → xaritadan nuqta belgilash) qanday foydalanishni tushuntiramiz. Holat
    o'zgarmaydi -- shu yo'l bilan kelgan location ham handle_order_location tomonidan xuddi
    tugma orqali kelgandek qabul qilinadi (F.location manba bo'yicha farqlamaydi)."""
    data = await state.get_data()
    lang = data.get("lang", "uz")
    await message.answer(t("order_map_instructions", lang))


@router.message(OrderPlacementState.waiting_for_location)
async def handle_order_location_invalid(message: Message, state: FSMContext):
    """Buyurtma berish -- lokatsiya kutilayotganda boshqa turdagi xabar kelsa, qayta so'raydi.
    "Bekor qilish" bosilsa (2026-08-03) oqim shu yerda to'xtatiladi."""
    data = await state.get_data()
    lang = data.get("lang", "uz")

    if message.text in CANCEL_BUTTON_TEXTS:
        await state.clear()
        await message.answer(t("order_cancelled", lang), reply_markup=get_menu_reply_keyboard(lang))
        return

    await message.answer(t("order_invalid_location", lang), reply_markup=get_location_request_keyboard(lang))


@router.message(OrderPlacementState.waiting_for_order)
async def handle_order_placement(message: Message, state: FSMContext, bot: Bot):
    """Mijoz buyurtma tafsilotlarini yuborgach: buyurtma har doim to'g'ridan-to'g'ri Odoo'ga
    (crm.lead) yoziladi -- Odoo'da MA'LUM mijoz uchun partner_id orqali, butunlay yangi
    mijoz (Odoo'da topilmagan, ism/telefonni "Yangi mijoz" oqimida o'zi kiritgan) uchun
    o'sha ism/telefon bilan. Agar xabar rasm/video/ovozli xabar/fayl(hujjat) bo'lsa, avval
    ORDER_MEDIA_DIR'ga yuklab olinadi va Odoo'ga HAQIQIY ir.attachment sifatida
    biriktiriladi (2026-08-03). Call-center guruhiga forward qilish BUTUNLAY olib
    tashlandi (2026-08-03) -- Odoo'ga yozish muvaffaqiyatsiz bo'lsa endi shunchaki
    mijozga xatolik ko'rsatiladi. "Bekor qilish" bosilsa (2026-08-03) hech narsa Odoo'ga
    yozilmaydi -- oqim shu yerda to'xtatiladi."""
    data = await state.get_data()
    lang = data.get("lang", "uz")

    if message.text in CANCEL_BUTTON_TEXTS:
        await state.clear()
        await message.answer(t("order_cancelled", lang), reply_markup=get_menu_reply_keyboard(lang))
        return

    order_details = message.text or message.caption or "(mijoz matn yozmadi, rasm/fayl yubordi)"
    photo_path, video_path, audio_path, document_path = await _download_order_media(message, bot)

    lead_id = await submit_order_to_odoo(
        partner_id=data.get("partner_id"),
        partner_name=data.get("partner_name") or "-",
        partner_phone=data.get("partner_phone"),
        chat_id=message.chat.id,
        order_details=order_details,
        object_name=data.get("object_name"),
        latitude=data.get("latitude"),
        longitude=data.get("longitude"),
        photo_path=photo_path,
        video_path=video_path,
        audio_path=audio_path,
        document_path=document_path,
    )

    if lead_id is None:
        logger.error("Buyurtmani Odoo'ga yozib bo'lmadi (chat_id=%s)", message.chat.id)
        await message.answer(t("generic_error", lang), reply_markup=get_menu_reply_keyboard(lang))
        await state.clear()
        return

    await message.answer(t("order_placement_accepted", lang), reply_markup=get_menu_reply_keyboard(lang))
    await state.clear()
