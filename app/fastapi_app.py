"""
FastAPI ilovasi -- Odoo bilan integratsiya uchun webhook va API endpointlari.
"""

import asyncio
import base64
import json
import logging
import mimetypes
import re
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import BufferedInputFile
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.staticfiles import StaticFiles
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.balance import format_info_message, invert_balance_sign
from app.bot.keyboards import get_order_inline_keyboard  # to'g'ridan-to'g'ri keyboards.py'dan (2026-08-05, tezlik: app.bot import qilinsa matplotlib/aiogram Dispatcher ham 3ta uvicorn worker jarayoniga ortiqcha yuklanardi)
from app.config import settings
from app.database import get_session, init_db
from app.i18n import t
from app.models import (
    ConfirmationStatus,
    DeliveryConfirmation,
    GroupInquiry,
    GroupMembership,
    ObjectEditLog,
    ObjectLocation,
    OdooBalanceSnapshot,
    Order,
    OrderMediaAttachment,
    OrderStatus,
    StatusUpdateLog,
    TelegramUser,
)
from app.odoo_client import (
    check_odoo_connection,
    create_ir_attachment,
    create_telegram_order_lead,
    find_partner_by_chat_id,
    get_customer_objects,
    get_monthly_statistics,
    get_partner_manager,
    get_quotations,
    post_object_edit_chatter_message,
    set_object_location,
    update_object_contact,
)
from app.schemas import (
    BalanceObjectResponse,
    BotMenuRequest,
    BotMenuResponse,
    BotStatisticsResponse,
    ChatIdResponse,
    DeliveryConfirmationPayload,
    DeliveryConfirmationResponse,
    GroupInquiryResponse,
    GroupInquirySubmitRequest,
    GroupInquirySubmitResponse,
    GroupInquirySyncRequest,
    GroupMembershipResponse,
    GroupMembershipSyncRequest,
    GroupOrderResponse,
    ObjectContactUpdateRequest,
    ObjectContactUpdateResponse,
    ObjectEditLogResponse,
    ObjectEditLogSyncRequest,
    ObjectLocationResponse,
    ObjectLocationSubmitRequest,
    OdooBalanceSyncRequest,
    OdooWebhookPayload,
    OrderCustomerLookupResponse,
    OrderSubmitRequest,
    OrderSubmitResponse,
    SyncUpdateRequest,
)
from app.translate import translate_text
from app.utils import (
    clean_text_for_odoo,
    get_user_language,
    normalize_uz_phone,
    normalize_username,
    save_base64_pdf,
    save_base64_photo,
    save_webhook_payload_log,
    upsert_telegram_user,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("fastapi_app")

FEEDBACK_DIR = Path("data/feedback_files")
FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)

DELIVERY_PHOTOS_DIR = Path("data/delivery_photos")
DELIVERY_PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

ORDER_MEDIA_DIR = Path("data/order_media")
ORDER_MEDIA_DIR.mkdir(parents=True, exist_ok=True)

bot = Bot(token=settings.BOT_TOKEN)


async def send_pdf_via_bot(
    chat_id: int,
    pdf_path: str,
    order_id: int,
    order_name: str | None = None,
    manager_name: str | None = None,
    custom_message: str | None = None,
    doc_id: str | None = None,
    lang: str = "uz",
) -> bool:
    """
    Returns True agar hujjat yuborilgan bo'lsa. Agar chat_id yaroqsiz bo'lsa (foydalanuvchi
    hali botga /start bosmagan yoki uni bloklagan -- "chat not found"/"bot was blocked")
    TelegramBadRequest/TelegramForbiddenError qayta ko'tariladi, chaqiruvchi shu holatda
    userbot orqali username bo'yicha qayta murojaat qilish uchun chat_id'ni tozalay oladi.
    `lang` -- qabul qiluvchi botda tanlagan til ("uz" bo'lsa Odoo matni tarjima qilinmaydi).
    """
    try:
        formatted_msg = clean_text_for_odoo(custom_message)

        if formatted_msg:
            caption = await translate_text(formatted_msg, lang)
        else:
            doc_display = doc_id if doc_id else f"#{order_id}"
            caption = t("pdf_caption_fallback", lang, doc_name=doc_display)

        custom_filename = f"{order_name}.pdf" if order_name else f"Buyurtma_#{order_id}.pdf"

        # Tezlik uchun (2026-08-03): diskdan o'qish bloklovchi (sync) amal -- to_thread'siz
        # to'g'ridan-to'g'ri chaqirilsa, bitta uvicorn worker jarayonida shu payt boshqa
        # BARCHA parallel so'rovlar ham to'xtab qoladi.
        file_bytes = await asyncio.to_thread(Path(pdf_path).read_bytes)
        document = BufferedInputFile(file_bytes, filename=custom_filename)

        await bot.send_document(
            chat_id=chat_id,
            document=document,
            caption=caption,
            parse_mode="HTML",
            reply_markup=get_order_inline_keyboard(order_id, lang),
        )
        return True
    except (TelegramBadRequest, TelegramForbiddenError):
        raise
    except Exception:
        logger.exception("Bot orqali PDF yuborishda xatolik (chat_id=%s)", chat_id)
        return False


def _pdf_display_name(subject: str | None, doc_id: str | None, order_id: int) -> str:
    """PDF fayl nomini tanlaydi -- doc_id (haqiqiy buyurtma/taklif raqami, masalan
    "S56628") mavjud bo'lsa har doim UNI ishlatadi. `subject` faqat doc_id yo'q bo'lgan
    holatda zaxira sifatida ishlatiladi -- Odoo ba'zi so'rovlarda (masalan holat
    yangilanishi push'larida) subject'ni umumiy ("Holat yangilanishi") qiymat bilan
    yuborishi mumkin, bu haqiqiy hujjat nomi emas, shuning uchun doc_id'dan past
    ustuvorlikda turadi (2026-07-30'da tuzatildi)."""
    if doc_id:
        return f"Tijorat taklifi - {doc_id}"
    if subject and str(subject).strip():
        subject = str(subject).strip()
        return subject if subject.startswith("Tijorat taklifi") else f"Tijorat taklifi - {subject}"
    return f"Tijorat_taklifi_#{order_id}"


RESPONSIBLE_PERSON_NOT_FOUND_TEXT = (
    "⚠️ Tasdiqlash uchun mas'ul shaxs topilmadi.\n\n"
    "Tasdiqlash yoki bekor qilish uchun qo'llab-quvvatlash bo'limimizga aloqaga chiqing: "
    "@{support_username}"
).format(support_username=settings.SUPPORT_USERNAME.lstrip("@"))


async def _resolve_known_chat_id(
    session: AsyncSession, normalized_username: str, provided_chat_id: int | None
) -> int | None:
    """
    Foydalanuvchining chat_id'sini aniqlaydi: avval to'g'ridan-to'g'ri berilgan qiymatga
    qaraydi, bo'lmasa shu normalized_username bilan oldin yaratilgan (chat_id ma'lum
    bo'lgan) so'nggi buyurtmadan oladi.
    """
    if provided_chat_id:
        return provided_chat_id
    if normalized_username == "no_username":
        return None

    existing_order = await session.execute(
        select(Order.chat_id)
        .where(Order.normalized_username == normalized_username, Order.chat_id.is_not(None))
        .order_by(Order.id.desc())
        .limit(1)
    )
    return existing_order.scalar_one_or_none()


async def _is_target_user_reachable_in_group(group_id: int, target_chat_id: int) -> bool:
    """
    Berilgan foydalanuvchi hozir ham shu guruhda ekanini va akkaunti o'chirilmaganini
    (Bot API orqali aniqlanadigan darajada) tekshiradi.
    """
    try:
        member = await bot.get_chat_member(chat_id=group_id, user_id=target_chat_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        return False
    except Exception:
        logger.exception(
            "Guruh a'zoligini tekshirishda xatolik (group_id=%s, chat_id=%s)", group_id, target_chat_id
        )
        return False

    if member.status in ("left", "kicked"):
        return False
    # Telegram o'chirilgan akkauntlarni shu nom bilan ko'rsatadi (username'siz)
    if member.user.first_name == "Deleted Account" and not member.user.username:
        return False
    return True


async def _resolve_group_known_chat_id(
    session: AsyncSession, group_id: int
) -> tuple[int | None, str | None]:
    """
    Odoo group_id bilan buyurtma yuborsa-yu, LEKIN mas'ul shaxsni (user/username)
    ko'rsatmasa (2026-08-06, mijoz aniq so'ragan -- "Odoo'dan username qo'shib
    jo'natilmaydi, o'zimizni bazamizdan qaysi usernamega tasdiqlash/rad etish/
    o'zgartrishni bering") -- shu guruhda BIZGA (group_memberships, faqat botga
    /start bosgan ma'lum foydalanuvchilar) ma'lum bo'lgan a'zolar orasidan hali ham
    guruhda faol (haqiqiy vaqtda tekshirilgan, chiqib ketmagan/chiqarilmagan) birinchi
    shaxsni tasdiqlash/rad etish uchun mas'ul deb tanlaydi. Bir nechta ma'lum a'zo
    bo'lsa -- eng oxirgi aniqlangani (`discovered_at DESC`) ustuvor, lekin haqiqatan
    hali guruhda ekani tasdiqlanmaguncha keyingisiga o'tiladi. Hech kim topilmasa
    (None, None) -- chaqiruvchi tomon eski "mas'ul shaxs topilmadi" xabariga tushadi."""
    result = await session.execute(
        select(GroupMembership.chat_id, TelegramUser.username)
        .join(TelegramUser, GroupMembership.chat_id == TelegramUser.chat_id)
        .where(GroupMembership.group_id == group_id)
        .order_by(GroupMembership.discovered_at.desc())
    )
    for candidate_chat_id, candidate_username in result.all():
        if await _is_target_user_reachable_in_group(group_id, candidate_chat_id):
            return candidate_chat_id, candidate_username
    return None, None


async def handle_group_order(
    session: AsyncSession,
    group_id: int,
    extracted_doc_id: str | None,
    manager_name: str | None,
    order_data: dict,
    pdf_path: str,
    message: str | None,
    subject: str | None,
    target_username: str | None,
    target_chat_id: int | None,
) -> dict:
    """
    Buyurtmani Telegram guruhiga yuboradi. Guruhda ushbu buyurtma uchun mas'ul
    (target_chat_id) shaxs hali guruh a'zosi va akkaunti faol bo'lsa -- PDF + Tasdiqlash/
    Rad etish/O'zgartirish tugmalari yuboriladi (faqat o'sha shaxs bosa oladi, tekshiruv
    app/bot/handlers/orders.py'da). Aks holda (mas'ul shaxs berilmagan, guruhdan chiqib
    ketgan yoki akkaunti o'chirilgan bo'lsa) qo'llab-quvvatlashga murojaat matni
    yuboriladi. Individual oqimdan (handle_individual_orders) mustaqil ishlaydi.
    """
    normalized_target = normalize_username(target_username) if target_username else "no_username"

    if target_chat_id:
        await upsert_telegram_user(session, chat_id=target_chat_id, username=target_username)

    order = Order(
        doc_id=extracted_doc_id,
        raw_username=target_username or "no_username",
        normalized_username=normalized_target,
        chat_id=target_chat_id,
        group_id=group_id,
        manager_name=manager_name,
        custom_message=message,
        order_details=json.dumps(order_data, ensure_ascii=False),
        pdf_path=pdf_path,
        status=OrderStatus.PENDING.value,
        confirmation_status=ConfirmationStatus.PENDING.value,
        notified=False,
        synced=False,
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)

    responsible_person_reachable = bool(target_chat_id) and await _is_target_user_reachable_in_group(
        group_id, target_chat_id
    )

    if responsible_person_reachable:
        pdf_filename = _pdf_display_name(subject, extracted_doc_id, order.id)
        lang = await get_user_language(session, target_chat_id)
        try:
            sent = await send_pdf_via_bot(
                chat_id=group_id,
                pdf_path=pdf_path,
                order_id=order.id,
                order_name=pdf_filename,
                manager_name=manager_name,
                custom_message=message,
                doc_id=order.doc_id,
                lang=lang,
            )
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning("Guruhga PDF yuborib bo'lmadi (group_id=%s): %s", group_id, exc)
            sent = False

        if sent:
            order.notified = True
    else:
        try:
            await bot.send_message(chat_id=group_id, text=RESPONSIBLE_PERSON_NOT_FOUND_TEXT)
            order.notified = True
        except Exception:
            logger.exception("Guruhga xabar yuborishda xatolik (group_id=%s)", group_id)

    await session.commit()

    return {
        "success": True,
        "created_orders_count": 1,
        "doc_id": extracted_doc_id,
        "group_id": group_id,
        "order_id": order.id,
        "chat_id": order.chat_id,
        "notified": order.notified,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 2026-08-05, PRODUCTION tayyorlash/tezlashtirish: bu yerda avval davriy
    # tozalash vazifasi (`background_maintenance_loop`) ham ishga tushirilar edi --
    # FastAPI endi bir nechta uvicorn WORKER bilan ishga tushirilgani sabab (har bir
    # worker o'z ALOHIDA `lifespan`iga ega) bu funksiya HAR bir workerda mustaqil/
    # parallel ishlab ketardi (masalan 3 worker = bir xil DB tozalash so'rovi soatiga
    # 3 marta). Funksiya endi `app/utils.py`ga ko'chirilgan va faqat `app/bot/
    # __init__.py`ning `main()`ida (yagona, HAR DOIM bitta nusxada ishlaydigan
    # aiogram polling jarayoni) ishga tushiriladi -- `utils.py`dagi
    # `background_maintenance_loop` docstringiga qarang.
    await init_db()
    logger.info("FastAPI ilovasi ishga tushdi.")
    try:
        yield
    finally:
        await bot.session.close()
        logger.info("FastAPI ilovasi to'xtatildi.")


app = FastAPI(
    title="Odoo - Telegram Integration API",
    description="Odoo webhook orqali kelgan buyurtmalarni Telegram Bot bilan bog'lovchi API",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/feedback_files", StaticFiles(directory="data/feedback_files"), name="feedback_files")
app.mount("/delivery_photos", StaticFiles(directory="data/delivery_photos"), name="delivery_photos")


# 2026-08-20, CEO talabi: dunyabunya_agent'ning (BOSHQA, mustaqil loyiha, shu serverda
# xuddi shu host tarmog'ida ishlaydi) HTML hisobot sahifalari uchun proxy. Sabab: bu
# loyihaning 8000-porti allaqachon serverdan tashqariga (Uztelecom firewall darajasida)
# ochiq, dunyabunya_agent'ning 8001-porti esa ochilmagan va buni ichkaridan (bu kod bilan)
# tuzatib bo'lmaydi (VPS provayder darajasidagi sozlama). Shu sababli dunyabunya_agent
# o'zining allaqachon ochiq bo'lgan qo'shni portidan "o'tib" havolalarni ko'rsatadi —
# Uztelecom'ga murojaat qilmasdan darhol ishlaydi. Yo'l qat'iy ravishda
# `/dunyabunya-reports/reports/{filename}` (ikki marta "reports" emas — birinchisi bu
# proxy'ning o'z prefiksi, ikkinchisi dunyabunya_agent'ning o'z StaticFiles mount nomi,
# `PUBLIC_BASE_URL=http://<ip>:8000/dunyabunya-reports` + o'sha loyihaning ichki
# `/reports/{filename}` havola qurish mantig'i bilan mos kelishi uchun ATAYLAB shunday).
# Fayl nomi UUID hex + ".html" shaklida ekani oldindan ma'lum (dunyabunya_agent uni shunday
# generatsiya qiladi) — shu shaklga qat'iy mos kelmasa so'rov hech qayerga yuborilmasdan
# rad etiladi (path-traversal himoyasi).
_DUNYABUNYA_REPORT_FILENAME_RE = re.compile(r"^[a-f0-9]{32}\.html$")
_DUNYABUNYA_REPORTS_UPSTREAM = "http://localhost:8001/reports"


@app.get("/dunyabunya-reports/reports/{filename}")
async def proxy_dunyabunya_report(filename: str) -> Response:
    if not _DUNYABUNYA_REPORT_FILENAME_RE.match(filename):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Noto'g'ri fayl nomi")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            upstream = await client.get(f"{_DUNYABUNYA_REPORTS_UPSTREAM}/{filename}")
    except httpx.RequestError as exc:
        logger.warning("dunyabunya_reports_proxy.upstream_unreachable: %s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Hisobot xizmati hozir ishlamayapti"
        ) from exc

    if upstream.status_code == status.HTTP_404_NOT_FOUND:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hisobot topilmadi")

    return Response(content=upstream.content, media_type="text/html", status_code=upstream.status_code)


async def verify_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> None:
    if x_api_key != settings.API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API Key noto'g'ri yoki topilmadi")


async def handle_individual_orders(
    session: AsyncSession,
    payload: OdooWebhookPayload,
    extracted_doc_id: str | None,
    manager_name: str | None,
    order_data: dict,
    pdf_path: str,
    subject: str | None,
) -> dict:
    """
    Odoo'dan kelgan username(lar)ga alohida-alohida Order yaratadi va chat_id ma'lum
    bo'lganlarga darhol PDF yuboradi. Guruh yo'nalishidan (handle_group_order) butunlay
    mustaqil -- ikkalasi ham bitta webhook chaqiruvida birga ishlashi mumkin.
    """
    # Username'larni ajratib olish (List yoki vergul bilan yozilgan string bo'lishi mumkin)
    raw_usernames_input = payload.user.username if payload.user else None

    usernames_list = []
    if isinstance(raw_usernames_input, list):
        usernames_list = [str(u).strip() for u in raw_usernames_input if str(u).strip()]
    elif raw_usernames_input:
        usernames_list = [u.strip() for u in re.split(r'[,;\s]+', str(raw_usernames_input)) if u.strip()]

    if not usernames_list:
        usernames_list = ["no_username"]

    created_orders = []
    for raw_u in usernames_list:
        normalized = normalize_username(raw_u) if raw_u != "no_username" else "no_username"

        # Chat ID ni payload yoki bazadan qidirib topish
        provided_chat_id = payload.user.chat_id if payload.user and payload.user.chat_id else None
        user_chat_id = await _resolve_known_chat_id(session, normalized, provided_chat_id)

        if user_chat_id:
            await upsert_telegram_user(session, chat_id=user_chat_id, username=raw_u)

        order = Order(
            doc_id=extracted_doc_id,
            raw_username=raw_u,
            normalized_username=normalized,
            chat_id=user_chat_id,
            manager_name=manager_name,
            custom_message=payload.message,
            order_details=json.dumps(order_data, ensure_ascii=False),
            pdf_path=pdf_path,
            status=OrderStatus.PENDING.value,
            confirmation_status=ConfirmationStatus.PENDING.value,
            notified=False,
            synced=False,
        )
        session.add(order)
        created_orders.append(order)

    await session.commit()

    for order in created_orders:
        await session.refresh(order)

    # Agar chat_id mavjud bo'lsa (oldin start bosgan bo'lsa), PDF ni darhol yuborish
    for order in created_orders:
        if order.chat_id:
            pdf_filename = _pdf_display_name(subject, extracted_doc_id, order.id)
            lang = await get_user_language(session, order.chat_id)

            try:
                sent_success = await send_pdf_via_bot(
                    chat_id=order.chat_id,
                    pdf_path=pdf_path,
                    order_id=order.id,
                    order_name=pdf_filename,
                    manager_name=manager_name,
                    custom_message=payload.message,
                    doc_id=order.doc_id,
                    lang=lang,
                )
                if sent_success:
                    order.notified = True
            except (TelegramBadRequest, TelegramForbiddenError) as exc:
                # Bot bu chat_id'ga yoza olmadi (foydalanuvchi hali /start bosmagan yoki
                # botni bloklagan) -- chat_id'ni tozalaymiz, shunda userbot uni
                # keyingi tsiklda username orqali qayta ushlab, botga kirishni so'raydi
                logger.warning(
                    "chat_id=%s ga yozib bo'lmadi (%s), userbot username orqali qayta murojaat qiladi: @%s",
                    order.chat_id, exc, order.normalized_username,
                )
                order.chat_id = None

    await session.commit()

    return {
        "created_orders_count": len(created_orders),
        "orders": [
            {
                "order_id": o.id,
                "normalized_username": o.normalized_username,
                "chat_id": o.chat_id,
                "notified": o.notified,
            } for o in created_orders
        ],
    }


# Odoo bu aniq literal subject bilan, buyurtma menejer tomonidan ODOO'NING O'ZIDA
# tasdiqlangan/bekor qilingan bo'lganda push yuboradi (mijozning Telegram tugmasi orqali
# tasdiqlashidan MUSTAQIL, teskari yo'nalish -- real webhook loglarida (2026-08-02)
# tasdiqlangan namuna: subject="Holat yangilanishi", message="Buyurtmangiz <menejer ismi>
# tomonidan tasdiqlandi/bekor qilindi." -- matn Odoo tarafida ALLAQACHON tayyor holda
# keladi). Bunday push'larda PDF/tugma shart emas -- shunchaki oddiy xabar yuboriladi.
STATUS_UPDATE_SUBJECT = "Holat yangilanishi"


async def _is_duplicate_status_update(
    session: AsyncSession,
    doc_id: str | None,
    message: str,
    *,
    chat_id: int | None = None,
    group_id: int | None = None,
) -> bool:
    """True qaytaradi agar aynan shu (doc_id, message, target) uchtaligi ILGARI ham
    yozilgan bo'lsa (2026-08-03, live tekshirilgan: Odoo bitta o'zgarish uchun bir xil
    "Holat yangilanishi" xabarini bir necha marta yuborishi mumkin -- masalan S56653
    uchun "bekor qilindi" matni 3 marta ketma-ket, ~1 daqiqa ichida). False bo'lsa --
    bu BIRINCHI marta, shu yerning o'zida StatusUpdateLog'ga yozib qo'yiladi (keyingi
    tekshiruvlar buni topadi) va chaqiruvchi tomon xabarni haqiqatan yuborishi kerak."""
    existing = (
        await session.execute(
            select(StatusUpdateLog).where(
                StatusUpdateLog.doc_id == (doc_id or ""),
                StatusUpdateLog.message == message,
                StatusUpdateLog.target_chat_id == chat_id,
                StatusUpdateLog.target_group_id == group_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return True

    session.add(
        StatusUpdateLog(doc_id=doc_id or "", message=message, target_chat_id=chat_id, target_group_id=group_id)
    )
    await session.flush()
    return False


async def handle_status_update_notification(
    session: AsyncSession, payload: OdooWebhookPayload, doc_id: str | None
) -> dict:
    """STATUS_UPDATE_SUBJECT bilan kelgan push -- payload.message'da ALLAQACHON tayyor,
    o'qiladigan xabar matni bor (masalan "Buyurtmangiz ... tomonidan tasdiqlandi.").
    Shu sabab bu yerda YANGI Order yaratilmaydi, PDF/tugma umuman ishlatilmaydi --
    shunchaki tayyor xabar matni (individual qabul qiluvchi uchun uning tanlagan tiliga
    tarjima qilingan holda; guruh doim o'z tilida -- pastga qarang) oddiy xabar sifatida
    yuboriladi. (2026-08-03: PDF'ni ham biriktirish bir muddat sinab ko'rilgan edi, lekin
    mijoz bu holatda PDF shart emasligini aniq bildirgach qaytarib olib tashlandi --
    status o'zgarganda hujjatning o'zi o'zgarmaydi, qayta yuborish shart emas.) group_id
    va/yoki user asosiy oqim bilan bir xil qoidada, bir-biridan mustaqil ravishda ishlov
    beriladi (handle_group_order / handle_individual_orders'dagi username-parsing bilan
    bir xil naqsh). Har bir yo'nalish (guruh/individual) yuborishdan OLDIN
    `_is_duplicate_status_update` orqali tekshiriladi -- Odoo aynan bir xil xabarni bir
    necha marta yuborsa (live tasdiqlangan: S56653 uchun "bekor qilindi" 3 marta ketma-
    ket), faqat BIRINCHISI mijozga/guruhga real Telegram xabari sifatida yetib boradi
    (2026-08-03)."""
    text = clean_text_for_odoo(payload.message)
    if not text:
        text = t("pdf_caption_fallback", "uz", doc_name=doc_id or "-")

    targets: list[dict] = []

    def _extract_target_username(raw_username_input) -> str | None:
        if isinstance(raw_username_input, list):
            return str(raw_username_input[0]).strip() if raw_username_input else None
        if raw_username_input:
            parts = re.split(r"[,;\s]+", str(raw_username_input).strip())
            return parts[0] or None
        return None

    if payload.group_id:
        if await _is_duplicate_status_update(session, doc_id, text, group_id=payload.group_id):
            logger.info(
                "Holat yangilanishi takroriy (guruh) -- o'tkazib yuborildi (doc_id=%s, group_id=%s)",
                doc_id, payload.group_id,
            )
            targets.append({"group_id": payload.group_id, "sent": False, "duplicate": True})
        else:
            # Guruhda kim tasdiqlashga vakolatli bo'lsa, shu odamning tili ishlatiladi --
            # handle_group_order bilan bir xil resolution qoidasi.
            raw_target_username = _extract_target_username(payload.user.username) if payload.user else None
            target_normalized = normalize_username(raw_target_username) if raw_target_username else "no_username"
            target_provided_chat_id = payload.user.chat_id if payload.user and payload.user.chat_id else None
            target_chat_id = await _resolve_known_chat_id(session, target_normalized, target_provided_chat_id)

            group_lang = await get_user_language(session, target_chat_id) if target_chat_id else "uz"
            group_text = await translate_text(text, group_lang)
            try:
                await bot.send_message(chat_id=payload.group_id, text=group_text)
                targets.append({"group_id": payload.group_id, "sent": True})
            except Exception:
                logger.exception(
                    "Holat yangilanishi xabarini guruhga yuborishda xatolik (group_id=%s)", payload.group_id
                )
                targets.append({"group_id": payload.group_id, "sent": False})

    if payload.user is not None:
        raw_username_input = payload.user.username
        usernames_list = []
        if isinstance(raw_username_input, list):
            usernames_list = [str(u).strip() for u in raw_username_input if str(u).strip()]
        elif raw_username_input:
            usernames_list = [u.strip() for u in re.split(r"[,;\s]+", str(raw_username_input)) if u.strip()]
        if not usernames_list:
            usernames_list = ["no_username"]

        for raw_u in usernames_list:
            normalized = normalize_username(raw_u) if raw_u != "no_username" else "no_username"
            provided_chat_id = payload.user.chat_id
            chat_id = await _resolve_known_chat_id(session, normalized, provided_chat_id)

            if not chat_id:
                logger.warning(
                    "Holat yangilanishi: chat_id topilmadi, xabar yuborilmadi (username=@%s, doc_id=%s)",
                    raw_u, doc_id,
                )
                targets.append({"username": raw_u, "chat_id": None, "sent": False})
                continue

            if await _is_duplicate_status_update(session, doc_id, text, chat_id=chat_id):
                logger.info(
                    "Holat yangilanishi takroriy -- o'tkazib yuborildi (doc_id=%s, chat_id=%s)", doc_id, chat_id
                )
                targets.append({"username": raw_u, "chat_id": chat_id, "sent": False, "duplicate": True})
                continue

            if raw_u != "no_username":
                await upsert_telegram_user(session, chat_id=chat_id, username=raw_u)

            lang = await get_user_language(session, chat_id)
            translated = await translate_text(text, lang)
            try:
                await bot.send_message(chat_id=chat_id, text=translated)
                targets.append({"username": raw_u, "chat_id": chat_id, "sent": True})
            except (TelegramBadRequest, TelegramForbiddenError) as exc:
                logger.warning("Holat yangilanishi xabarini yuborib bo'lmadi (chat_id=%s): %s", chat_id, exc)
                targets.append({"username": raw_u, "chat_id": chat_id, "sent": False})

    await session.commit()
    return {"doc_id": doc_id, "targets": targets}


@app.post(
    "/api/v1/webhook/odoo",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_api_key)],
    summary="Odoo'dan yangi buyurtma qabul qilish",
)
async def receive_odoo_webhook(payload: OdooWebhookPayload, session: AsyncSession = Depends(get_session)):
    try:
        payload_dict = payload.model_dump()
        save_webhook_payload_log(payload_dict, settings.WEBHOOK_LOG_DIR)

        order_data = payload_dict.get("order") or {}
        extracted_doc_id = (
            payload.doc_id
            or order_data.get("buyurtma_raqami")
            or order_data.get("name")
            or order_data.get("doc_id")
        )
        raw_subject = payload_dict.get("subject")

        if raw_subject == STATUS_UPDATE_SUBJECT:
            # Menejer buyurtmani Odoo'ning O'ZIDA tasdiqlagan/bekor qilgan -- PDF/tugma
            # shart emas (status o'zgarganda hujjatning o'zi o'zgarmaydi), shunchaki
            # tayyor xabar matni yuboriladi -- pdf_base64 ATAYLAB o'qilmaydi/saqlanmaydi
            # (2026-08-03, mijoz aniq bildirgach: PDF qayta yuborilishi shart emas edi,
            # bir muddat sinab ko'rilgan biriktirish qaytarib olib tashlandi).
            status_result = await handle_status_update_notification(
                session=session, payload=payload, doc_id=extracted_doc_id
            )
            return {"success": True, "doc_id": extracted_doc_id, "status_update": status_result}

        # base64 decode + diskka yozish bloklovchi amal -- to_thread orqali (2026-08-03, boshqa
        # bloklovchi fayl I/O tuzatishlari bilan bir xil sabab).
        pdf_path = await asyncio.to_thread(save_base64_pdf, payload.pdf_base64, settings.PDF_STORAGE_DIR)
        manager_name = payload.manager.name if payload.manager else None

        # Odoo group_id va/yoki user (username/chat_id) ni mustaqil ravishda tanlaydi --
        # ikkalasi berilsa ikkalasiga ham yuboriladi, faqat biri berilsa faqat o'shanga.
        group_result = None
        if payload.group_id:
            # Guruhdagi tasdiqlashga vakolatli yagona shaxs -- payload.user'dan olinadi
            # (ro'yxat/vergul bilan kelsa ham faqat birinchisi olinadi, chunki guruh
            # buyurtmasi bitta mas'ul shaxsga bog'lanadi).
            raw_target_username = None
            if payload.user and payload.user.username:
                raw_username_input = payload.user.username
                if isinstance(raw_username_input, list):
                    raw_target_username = str(raw_username_input[0]).strip() if raw_username_input else None
                else:
                    raw_target_username = re.split(r"[,;\s]+", str(raw_username_input).strip())[0] or None

            target_normalized = normalize_username(raw_target_username) if raw_target_username else "no_username"
            target_provided_chat_id = payload.user.chat_id if payload.user and payload.user.chat_id else None
            target_chat_id = await _resolve_known_chat_id(session, target_normalized, target_provided_chat_id)

            if target_chat_id is None and raw_target_username is None:
                # Odoo bu safar hech kimni ko'rsatmadi -- o'z bazamizdan (group_memberships)
                # shu guruhda ma'lum va hali faol a'zoni mas'ul shaxs sifatida olamiz.
                resolved_chat_id, resolved_username = await _resolve_group_known_chat_id(
                    session, payload.group_id
                )
                if resolved_chat_id:
                    target_chat_id = resolved_chat_id
                    raw_target_username = resolved_username

            group_result = await handle_group_order(
                session=session,
                group_id=payload.group_id,
                extracted_doc_id=extracted_doc_id,
                manager_name=manager_name,
                order_data=order_data,
                pdf_path=pdf_path,
                message=payload.message,
                subject=raw_subject,
                target_username=raw_target_username,
                target_chat_id=target_chat_id,
            )

        individual_result = None
        # `user` berilgan bo'lsa -- albatta individual yo'nalish ham ishlaydi. Ikkalasi ham
        # berilmagan bo'lsa (na group_id, na user) -- eski xatti-harakatni saqlab qolamiz:
        # "no_username" bilan bitta buyurtma yaratiladi (orqaga moslik uchun).
        if payload.user is not None or payload.group_id is None:
            individual_result = await handle_individual_orders(
                session=session,
                payload=payload,
                extracted_doc_id=extracted_doc_id,
                manager_name=manager_name,
                order_data=order_data,
                pdf_path=pdf_path,
                subject=raw_subject,
            )

        return {
            "success": True,
            "doc_id": extracted_doc_id,
            "group": group_result,
            "individual": individual_result,
        }

    except ValueError as exc:
        logger.warning("Webhook validatsiya xatoligi: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Webhook qayta ishlashda kutilmagan xatolik: %s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Ichki server xatoligi: {str(exc)}"
        ) from exc


@app.get(
    "/api/v1/chat-ids",
    response_model=list[ChatIdResponse],
    dependencies=[Depends(verify_api_key)],
    summary="Odoo uchun: Mijozlar javoblari va chat_id larni olish",
)
async def get_chat_ids(synced: bool | None = None, session: AsyncSession = Depends(get_session)):
    try:
        query = select(Order).where(Order.chat_id.is_not(None))
        if synced is not None:
            query = query.where(Order.synced == synced)

        result = await session.execute(query.order_by(Order.created_at.desc()))
        orders = result.scalars().all()

        response_list = []
        for o in orders:
            raw_status = getattr(o.status, "value", o.status)
            raw_conf_status = getattr(o.confirmation_status, "value", o.confirmation_status)
            final_msg = o.feedback_text if o.feedback_text else None

            response_list.append(
                ChatIdResponse(
                    doc_id=str(o.doc_id) if o.doc_id else str(o.id),
                    username=o.normalized_username or "no_username",
                    chat_id=o.chat_id,
                    status=str(raw_status).upper() if raw_status else "PENDING",
                    confirmation_status=str(raw_conf_status).upper() if raw_conf_status else "PENDING",
                    message=final_msg,
                    feedback_files=o.feedback_files or [],
                    synced=bool(o.synced),
                )
            )

        return response_list

    except Exception as exc:
        logger.exception("chat-ids endpointida xatolik: %s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Ichki server xatoligi: {str(exc)}"
        ) from exc


@app.post(
    "/api/v1/chat-ids/mark-synced",
    dependencies=[Depends(verify_api_key)],
    summary="Odoo uchun: Olingan ma'lumotlarni sinxronlangan deb belgilash",
)
async def mark_synced(payload: SyncUpdateRequest, session: AsyncSession = Depends(get_session)):
    try:
        conditions = []
        if payload.doc_ids:
            conditions.append(Order.doc_id.in_(payload.doc_ids))
        if payload.ids:
            conditions.append(Order.id.in_(payload.ids))

        if not conditions:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="doc_ids yoki ids ro'yxati yuborilishi shart")

        stmt = update(Order).where(or_(*conditions)).values(synced=True)
        result = await session.execute(stmt)
        await session.commit()

        return {
            "success": True, 
            "updated_count": result.rowcount, 
            "updated_doc_ids": payload.doc_ids,
            "updated_ids": payload.ids
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("mark-synced endpointida xatolik")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ichki server xatoligi"
        ) from exc


@app.get(
    "/api/v1/group-memberships",
    response_model=list[GroupMembershipResponse],
    dependencies=[Depends(verify_api_key)],
    summary="Odoo uchun: foydalanuvchilarning qaysi guruh(lar)da ekanini olish",
)
async def get_group_memberships(synced: bool | None = None, session: AsyncSession = Depends(get_session)):
    try:
        query = select(GroupMembership, TelegramUser.username).join(
            TelegramUser, GroupMembership.chat_id == TelegramUser.chat_id
        )
        if synced is not None:
            query = query.where(GroupMembership.synced == synced)

        result = await session.execute(query.order_by(GroupMembership.discovered_at.desc()))

        return [
            GroupMembershipResponse(
                id=membership.id,
                chat_id=membership.chat_id,
                username=username,
                group_id=membership.group_id,
                group_title=membership.group_title,
            )
            for membership, username in result.all()
        ]

    except Exception as exc:
        logger.exception("group-memberships endpointida xatolik: %s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Ichki server xatoligi: {str(exc)}"
        ) from exc


@app.post(
    "/api/v1/group-memberships/mark-synced",
    dependencies=[Depends(verify_api_key)],
    summary="Odoo uchun: guruh a'zoligi yozuvlarini sinxronlangan deb belgilash",
)
async def mark_group_memberships_synced(
    payload: GroupMembershipSyncRequest, session: AsyncSession = Depends(get_session)
):
    try:
        stmt = update(GroupMembership).where(GroupMembership.id.in_(payload.ids)).values(synced=True)
        result = await session.execute(stmt)
        await session.commit()

        return {"success": True, "updated_count": result.rowcount, "updated_ids": payload.ids}
    except Exception as exc:
        logger.exception("group-memberships mark-synced endpointida xatolik")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ichki server xatoligi"
        ) from exc


@app.get(
    "/api/v1/group-orders",
    response_model=list[GroupOrderResponse],
    dependencies=[Depends(verify_api_key)],
    summary="Odoo uchun: guruhga yuborilgan buyurtmalarning tasdiqlash holati",
)
async def get_group_orders(session: AsyncSession = Depends(get_session)):
    try:
        result = await session.execute(
            select(Order).where(Order.group_id.is_not(None)).order_by(Order.created_at.desc())
        )
        orders = result.scalars().all()

        return [
            GroupOrderResponse(
                order_id=o.id,
                doc_id=str(o.doc_id) if o.doc_id else str(o.id),
                group_id=o.group_id,
                chat_id=o.chat_id,
                confirmation_status=o.confirmation_status,
                feedback_text=o.feedback_text,
            )
            for o in orders
        ]

    except Exception as exc:
        logger.exception("group-orders endpointida xatolik: %s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Ichki server xatoligi: {str(exc)}"
        ) from exc


# --- Bot menyusi (Orders / Quotation / Support / Info) --------------------------------
# Quotation va Info -- ikkalasi ham Odoo'ga XML-RPC orqali real vaqtli so'rov yuboradi
# (app/odoo_client.py). Info 2026-07-30'dan boshlab F.I.Sh/telefon/obyektlar bilan birga
# umumiy balansni ham o'z ichiga oladi -- shu sababli alohida "Balance" endpointi
# (POST /api/v1/bot/balance) va uning obyekt-ro'yxati endpointi (GET
# /api/v1/bot/balance-objects) shu kuni butunlay olib tashlandi (menyudagi "Balans"
# tugmasi bilan birga). Eski OdooBalanceSnapshot push/kesh mexanizmi (quyida
# /api/v1/odoo/balance/sync) ODOO XML-RPC ulanishi vaqtincha ishlamay qolgan davrda
# ishlatilgan -- endi o'qish yo'lida ishlatilmaydi, lekin Odoo tomonidan hali chaqirilib
# turishi mumkinligi uchun (orqaga mos bo'lishi uchun) endpoint o'zi olib tashlanmagan.
# Orders/Support hozircha placeholder javob qaytaradi (Support/Orders bot tarafida
# boshqacha boshqariladi -- app/bot/handlers/menu.py).


@app.post(
    "/api/v1/odoo/balance/sync",
    dependencies=[Depends(verify_api_key)],
    summary="Odoo -> FastAPI: mijozning balans va obyektlarini sync qilish (yaratadi yoki yangilaydi)",
)
async def sync_odoo_balance(payload: OdooBalanceSyncRequest, session: AsyncSession = Depends(get_session)):
    try:
        result = await session.execute(
            select(OdooBalanceSnapshot).where(OdooBalanceSnapshot.chat_id == payload.chat_id)
        )
        snapshot = result.scalar_one_or_none()
        if snapshot is None:
            snapshot = OdooBalanceSnapshot(chat_id=payload.chat_id)
            session.add(snapshot)

        snapshot.partner_name = payload.partner_name
        snapshot.total_balance_uzs = payload.total_balance_uzs
        snapshot.total_balance_usd = payload.total_balance_usd
        snapshot.objects = [o.model_dump() for o in payload.objects]

        await session.commit()
        return {"success": True, "chat_id": payload.chat_id}
    except Exception as exc:
        logger.exception("odoo/balance/sync endpointida xatolik")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ichki server xatoligi"
        ) from exc


@app.get(
    "/api/v1/bot/order-customer-lookup",
    response_model=OrderCustomerLookupResponse,
    dependencies=[Depends(verify_api_key)],
    summary="Bot: 'Buyurtma berish' bosilganda mijozni Odoo'dan qidirish (obyekt/lokatsiya/yangi-mijoz oqimini tanlash uchun)",
)
async def bot_order_customer_lookup(chat_id: int):
    partner = await asyncio.to_thread(find_partner_by_chat_id, chat_id)
    if not partner:
        return OrderCustomerLookupResponse(found=False)
    objects = await asyncio.to_thread(get_customer_objects, partner["id"]) or []
    return OrderCustomerLookupResponse(
        found=True,
        partner_id=partner["id"],
        partner_name=partner.get("name"),
        partner_phone=partner.get("phone") or partner.get("mobile"),
        objects=[
            BalanceObjectResponse(
                id=o["id"],
                name=o["name"],
                # Odoo 0.0/0.0'ni "lokatsiya kiritilmagan" sifatida ishlatadi (default) --
                # shu sabab None'ga aylantiramiz (bot qayta lokatsiya so'rash kerakligini shundan bilaydi).
                latitude=o.get("partner_latitude") or None,
                longitude=o.get("partner_longitude") or None,
            )
            for o in objects
        ],
    )


@app.post(
    "/api/v1/bot/object-update",
    response_model=ObjectContactUpdateResponse,
    dependencies=[Depends(verify_api_key)],
    summary="Bot: 'Ma'lumotlarni tahrirlash' oqimidan obyektning telefon raqami/lokatsiyasini Odoo'ga yozish",
)
async def bot_object_update(payload: ObjectContactUpdateRequest, session: AsyncSession = Depends(get_session)):
    success = await asyncio.to_thread(
        update_object_contact,
        payload.object_id,
        phone=payload.phone,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    # Odoo'ning o'zi bu yozuvni DARHOL ko'radi (to'g'ridan-to'g'ri write) -- shu log
    # FAQAT Odoo xodimlari "qaysi obyekt qachon o'zgardi" so'rasa GET /api/v1/object-edits
    # orqali javob topishi uchun (2026-08-05, mijoz aniq so'ragan).
    if success:
        field = "phone" if payload.phone is not None else "location"
        session.add(
            ObjectEditLog(
                object_id=payload.object_id,
                object_name=payload.object_name,
                partner_id=payload.partner_id,
                field=field,
                new_phone=payload.phone,
                new_latitude=payload.latitude,
                new_longitude=payload.longitude,
                chat_id=payload.chat_id,
                changed_by_username=payload.username,
            )
        )
        await session.commit()

        # 2026-08-06, mijoz aniq so'ragan: shu obyektning o'z Odoo kartochkasiga
        # ("chatter"/xabarlar tarixiga) ham xabar yoziladi -- xodim o'sha obyektni
        # ochganda o'zgarishni DARHOL ko'radi, GET /api/v1/object-edits'ni alohida
        # so'rashi shart emas. Log yozuvidan farqli (audit-trail, poll orqali), bu
        # ODDIY VA DARHOL ko'rinadigan eslatma -- muvaffaqiyatsiz bo'lsa ham (masalan
        # chatter yozish huquqi bo'lmasa) asosiy yozuv (write) allaqachon muvaffaqiyatli
        # bo'lgani uchun butun so'rov muvaffaqiyatsiz deb belgilanmaydi, faqat log qilinadi.
        if field == "phone":
            chatter_text = f"📱 Telegram bot orqali telefon raqami o'zgartirildi: {payload.phone}"
        else:
            chatter_text = (
                f"📍 Telegram bot orqali lokatsiya o'zgartirildi: "
                f"{payload.latitude}, {payload.longitude}"
            )
        if payload.username:
            chatter_text += f" (@{payload.username})"

        try:
            await asyncio.to_thread(post_object_edit_chatter_message, payload.object_id, chatter_text)
        except Exception:
            logger.exception("Obyekt chatter xabarini yozishda xatolik (object_id=%s)", payload.object_id)

    return ObjectContactUpdateResponse(success=success)


@app.get(
    "/api/v1/object-edits",
    response_model=list[ObjectEditLogResponse],
    dependencies=[Depends(verify_api_key)],
    summary="Odoo uchun: mijozlar 'Ma'lumotlarni tahrirlash' orqali o'zgartirgan obyektlar logi",
)
async def get_object_edits(synced: bool | None = None, session: AsyncSession = Depends(get_session)):
    try:
        query = select(ObjectEditLog)
        if synced is not None:
            query = query.where(ObjectEditLog.synced == synced)
        result = await session.execute(query.order_by(ObjectEditLog.changed_at.desc()))

        return [
            ObjectEditLogResponse(
                id=row.id,
                object_id=row.object_id,
                object_name=row.object_name,
                partner_id=row.partner_id,
                field=row.field,
                new_phone=row.new_phone,
                new_latitude=row.new_latitude,
                new_longitude=row.new_longitude,
                chat_id=row.chat_id,
                changed_by_username=row.changed_by_username,
                changed_at=row.changed_at,
                synced=row.synced,
            )
            for row in result.scalars().all()
        ]
    except Exception as exc:
        logger.exception("object-edits endpointida xatolik: %s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Ichki server xatoligi: {str(exc)}"
        ) from exc


@app.post(
    "/api/v1/object-edits/mark-synced",
    dependencies=[Depends(verify_api_key)],
    summary="Odoo uchun: obyekt tahrirlash logini sinxronlangan (ko'rib chiqilgan) deb belgilash",
)
async def mark_object_edits_synced(payload: ObjectEditLogSyncRequest, session: AsyncSession = Depends(get_session)):
    try:
        stmt = update(ObjectEditLog).where(ObjectEditLog.id.in_(payload.ids)).values(synced=True)
        result = await session.execute(stmt)
        await session.commit()

        return {"success": True, "updated_count": result.rowcount, "updated_ids": payload.ids}
    except Exception as exc:
        logger.exception("object-edits mark-synced endpointida xatolik")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ichki server xatoligi"
        ) from exc


def _read_and_create_ir_attachment(file_path: Path, lead_id: int, filename: str, mimetype: str) -> int | None:
    """Diskdan o'qish va base64 kodlash -- ikkalasi ham bloklovchi (disk/CPU) amal,
    shu sabab `create_ir_attachment`ning o'zi bilan birga BITTA `asyncio.to_thread`
    chaqiruvida bajariladi (2026-08-03, tezlik uchun -- avval fayl o'qish/kodlash
    to'g'ridan-to'g'ri async funksiya ichida, event loop'ni bloklab turgan holda
    bajarilardi, ayniqsa katta video/fayllar uchun sezilarli)."""
    file_bytes = file_path.read_bytes()
    base64_data = base64.b64encode(file_bytes).decode()
    return create_ir_attachment(lead_id, filename, base64_data, mimetype)


@app.post(
    "/api/v1/bot/order-submit",
    response_model=OrderSubmitResponse,
    dependencies=[Depends(verify_api_key)],
    summary="Bot: buyurtma so'rovini Odoo'ga (crm.lead) yozish -- Odoo'da MAVJUD (partner_id "
    "bilan) va butunlay yangi (partner_id=None, partner_phone bilan) mijozlar uchun",
)
async def bot_order_submit(payload: OrderSubmitRequest, session: AsyncSession = Depends(get_session)):
    lead_id = await asyncio.to_thread(
        create_telegram_order_lead,
        partner_id=payload.partner_id,
        partner_name=payload.partner_name,
        chat_id=payload.chat_id,
        order_details=payload.order_details,
        object_name=payload.object_name,
        latitude=payload.latitude,
        longitude=payload.longitude,
        phone=payload.partner_phone,
    )
    if lead_id is None:
        logger.error(
            "Buyurtmani Odoo'ga (crm.lead) yozib bo'lmadi (chat_id=%s, partner_id=%s)",
            payload.chat_id,
            payload.partner_id,
        )
    else:
        for media_path, mimetype in (
            (payload.photo_path, "image/jpeg"),
            (payload.video_path, "video/mp4"),
            (payload.audio_path, "audio/ogg"),
            (
                payload.document_path,
                mimetypes.guess_type(payload.document_path)[0] if payload.document_path else None,
            ),
        ):
            if not media_path:
                continue
            mimetype = mimetype or "application/octet-stream"
            try:
                attachment_id = await asyncio.to_thread(
                    _read_and_create_ir_attachment, ORDER_MEDIA_DIR / media_path, lead_id, media_path, mimetype
                )
            except Exception:
                logger.exception(
                    "Buyurtma mediasini o'qishda xatolik (lead_id=%s, media_path=%s)", lead_id, media_path
                )
                continue
            if attachment_id is None:
                logger.error(
                    "Buyurtma mediasini Odoo'ga (ir.attachment) biriktirib bo'lmadi "
                    "(lead_id=%s, media_path=%s) -- lead o'zi baribir yaratilgan, "
                    "mahalliy fayl OrderMediaAttachment orqali saqlanib qoladi",
                    lead_id,
                    media_path,
                )

    if payload.photo_path or payload.video_path or payload.audio_path or payload.document_path:
        session.add(
            OrderMediaAttachment(
                chat_id=payload.chat_id,
                lead_id=lead_id,
                photo_path=payload.photo_path,
                video_path=payload.video_path,
                audio_path=payload.audio_path,
                document_path=payload.document_path,
            )
        )
        await session.commit()

    return OrderSubmitResponse(success=lead_id is not None, lead_id=lead_id)


@app.post(
    "/api/v1/bot/object-location-submit",
    dependencies=[Depends(verify_api_key)],
    summary="Bot: 'Buyurtma berish' oqimida obyekt (mavjud yoki yangi) uchun GPS lokatsiyasini yozish",
)
async def bot_object_location_submit(
    payload: ObjectLocationSubmitRequest, session: AsyncSession = Depends(get_session)
):
    """Har doim mahalliy `object_locations` jadvaliga yoziladi (audit/zaxira -- Odoo GET
    orqali ham so'rab olishi mumkin, xuddi chat-ids/delivery-confirmations bilan bir xil
    poll+mark-synced naqsh). Bundan tashqari, agar `object_id` berilgan bo'lsa (MAVJUD
    obyekt -- yangi emas), Odoo'ning o'z standart geolokatsiya maydonlariga
    (partner_latitude/partner_longitude, 2026-08-02'da tekshirilgan) TO'G'RIDAN-TO'G'RI
    ham yoziladi -- shunda keyingi safar shu mijoz shu obyektni tanlaganda lokatsiya
    qayta so'ralmaydi. Yangi obyekt (`object_id=None`) uchun Odoo'da hali yozadigan
    joy yo'q -- Odoo tarafi ko'rib chiqib qo'lda yaratishi kerak."""
    row = ObjectLocation(
        partner_id=payload.partner_id,
        object_id=payload.object_id,
        object_name=payload.object_name,
        is_new_object=payload.is_new_object,
        latitude=payload.latitude,
        longitude=payload.longitude,
        chat_id=payload.chat_id,
        created_by_username=payload.username,
        partner_name=payload.partner_name,
        partner_phone=payload.partner_phone,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    odoo_synced = False
    if payload.object_id is not None:
        odoo_synced = await asyncio.to_thread(
            set_object_location, payload.object_id, payload.latitude, payload.longitude
        )
        if odoo_synced:
            row.odoo_synced = True
            await session.commit()
        else:
            logger.error(
                "Obyekt lokatsiyasini Odoo'ga yozib bo'lmadi (object_id=%s) -- "
                "mahalliy yozuv (id=%s) GET/mark-synced orqali zaxira sifatida qoladi",
                payload.object_id, row.id,
            )

    return {"success": True, "id": row.id, "odoo_synced": odoo_synced}


@app.post(
    "/api/v1/bot/orders",
    response_model=BotMenuResponse,
    dependencies=[Depends(verify_api_key)],
    summary="Bot menyu: Orders tugmasi",
)
async def bot_menu_orders(payload: BotMenuRequest):
    logger.info("Bot menyu so'rovi: orders (chat_id=%s, username=%s)", payload.chat_id, payload.username)
    return BotMenuResponse(message=t("menu_placeholder", payload.lang))


@app.post(
    "/api/v1/bot/quotation",
    response_model=BotMenuResponse,
    dependencies=[Depends(verify_api_key)],
    summary="Bot menyu: Tijorat takliflarim tugmasi (Odoo'ga real vaqtli so'rov)",
)
async def bot_menu_quotation(payload: BotMenuRequest):
    logger.info("Bot menyu so'rovi: quotation (chat_id=%s)", payload.chat_id)

    partner = await asyncio.to_thread(find_partner_by_chat_id, payload.chat_id)
    if not partner:
        return BotMenuResponse(message=t("balance_no_partner", payload.lang))

    objects = await asyncio.to_thread(get_customer_objects, partner["id"])
    partner_ids = [partner["id"]] + [o["id"] for o in objects]

    quotations = await asyncio.to_thread(get_quotations, partner_ids)
    if quotations is None:
        return BotMenuResponse(message=t("menu_placeholder", payload.lang))
    if not quotations:
        return BotMenuResponse(message=t("quotation_none_found", payload.lang))

    # Har bir taklifni tegishli obyekti (yoki asosiy mijozning o'zi, aniq yetkazib berish
    # manzili tanlanmagan bo'lsa) bo'yicha guruhlaydi -- `partner_shipping_id` ("Yetkazib
    # berish manzili") orqali, chunki `partner_id` shu Odoo instansiyasida har doim asosiy
    # mijozning o'zi (2026-08-03, avval partner_id ishlatilgan edi, keyinroq shu haqiqiy
    # maydonga o'tkazildi -- odoo_client.py'dagi get_quotations izohiga qarang). Guruhlar
    # ro'yxatda birinchi (eng yangi) taklifi qaysi guruhda bo'lsa o'sha tartibda chiqadi.
    groups: dict[int, list[dict]] = {}
    group_names: dict[int, str] = {}
    for q in quotations:
        q_partner_id, q_partner_name = q.get("partner_shipping_id") or (partner["id"], partner.get("name"))
        groups.setdefault(q_partner_id, []).append(q)
        group_names.setdefault(q_partner_id, q_partner_name)

    lines = [t("quotation_list_header", payload.lang)]
    for group_partner_id, group_quotations in groups.items():
        lines.append("")
        header = (
            t("quotation_general_label", payload.lang)
            if group_partner_id == partner["id"]
            else group_names[group_partner_id]
        )
        lines.append(f"🏢 {header}")
        for q in group_quotations:
            date_str = (q.get("date_order") or "")[:10]
            lines.append(f"  📄 {q['name']} — {q['amount_total']:,.0f} UZS ({date_str})")

    return BotMenuResponse(message="\n".join(lines))


@app.get(
    "/api/v1/odoo-health",
    dependencies=[Depends(verify_api_key)],
    summary="Odoo API kaliti/ulanishi ishlayaptimi -- 90 kunlik muddat tugashini kuzatish uchun",
)
async def odoo_health() -> dict:
    """Majburiy qayta autentifikatsiya qilib Odoo bilan ulanishni tekshiradi (keshlangan
    `uid`ga ishonmaydi -- aks holda eskirgan kalit ham "sog'lom" ko'rinib qolardi).
    `app/bot/__init__.py`ning `odoo_health_check_loop`i (bot konteyneri, yagona nusxa)
    buni davriy chaqirib, muvaffaqiyatsiz bo'lsa `ODOO_ALERT_CHAT_ID`ga ogohlantirish
    yuboradi (2026-08-06, API kaliti ~90 kunda tugashini "unutib qo'yish"ga qarshi)."""
    healthy = await asyncio.to_thread(check_odoo_connection)
    return {"healthy": healthy}


@app.post(
    "/api/v1/bot/support",
    response_model=BotMenuResponse,
    dependencies=[Depends(verify_api_key)],
    summary="Bot menyu: Support tugmasi",
)
async def bot_menu_support(payload: BotMenuRequest):
    """Umumiy support_contact_message HAR DOIM ko'rsatiladi; agar mijoz Odoo'da
    topilsa VA unga biriktirilgan menejerning raqami bo'lsa, shu umumiy xabar
    ORTIDAN menejer bilan bog'lanish qatori QO'SHIB yuboriladi (2026-08-06, mijoz
    aniq so'ragan -- avval menejer topilsa umumiy xabar butunlay ALMASHTIRILAR
    edi, endi ikkalasi BIRGA ko'rsatiladi). Mijoz topilmasa, yoki menejer
    biriktirilmagan/raqami yo'q bo'lsa -- faqat umumiy xabar (o'zgarmagan)."""
    logger.info("Bot menyu so'rovi: support (chat_id=%s, username=%s)", payload.chat_id, payload.username)

    message = t("support_contact_message", payload.lang)

    partner = await asyncio.to_thread(find_partner_by_chat_id, payload.chat_id)
    if partner:
        manager = await asyncio.to_thread(get_partner_manager, partner["id"])
        manager_phone = normalize_uz_phone(manager["phone"]) if manager else None
        if manager and manager_phone:
            message = f"{message}\n\n{t('support_manager_line', payload.lang, phone=manager_phone)}"

    return BotMenuResponse(message=message)


@app.post(
    "/api/v1/bot/info",
    response_model=BotMenuResponse,
    dependencies=[Depends(verify_api_key)],
    summary="Bot menyu: Info tugmasi (Odoo'ga real vaqtli so'rov)",
)
async def bot_menu_info(payload: BotMenuRequest):
    logger.info("Bot menyu so'rovi: info (chat_id=%s, username=%s)", payload.chat_id, payload.username)

    partner = await asyncio.to_thread(find_partner_by_chat_id, payload.chat_id)
    if not partner:
        return BotMenuResponse(message=t("balance_no_partner", payload.lang))

    objects = await asyncio.to_thread(get_customer_objects, partner["id"]) or []
    return BotMenuResponse(message=format_info_message(partner, objects, payload.lang))


@app.post(
    "/api/v1/bot/statistics",
    response_model=BotStatisticsResponse,
    dependencies=[Depends(verify_api_key)],
    summary="Bot menyu: Statistika tugmasi (oxirgi 12 oy, Odoo'ga real vaqtli so'rov)",
)
async def bot_menu_statistics(payload: BotMenuRequest):
    """Ma'lumotni faqat qaytaradi -- PDF/grafik generatsiyasi bot konteynerida bo'ladi
    (menu.py), FastAPI'ni engil/faqat-Odoo-ma'lumot-manbai sifatida saqlash uchun,
    xuddi Quotation/Info bilan bir xil arxitektura qoidasiga rioya qilib."""
    logger.info("Bot menyu so'rovi: statistics (chat_id=%s, username=%s)", payload.chat_id, payload.username)

    partner = await asyncio.to_thread(find_partner_by_chat_id, payload.chat_id)
    if not partner:
        return BotStatisticsResponse(found=False, months=None, by_object=None)

    data = await asyncio.to_thread(get_monthly_statistics, partner["id"])
    if data is None:
        return BotStatisticsResponse(found=True, months=None, by_object=None, partner_name=partner.get("name"))

    # `object_id == partner["id"]` -- hech qanday aniq obyekt tanlanmagan (asosiy
    # mijozning o'ziga bog'langan) buyurtma/to'lovlar -- 2026-08-04, mijoz aniq
    # so'ragan: bu "Umumiy" qatori BUTUNLAY olib tashlandi (avval Tijorat
    # takliflaridagi "Umumiy" naqshi bilan bir xil ko'rsatilardi, lekin bu jadvalda
    # chalkashtirib yuborgan -- haqiqiy nomlangan obyekt emasligi uchun). Bu
    # ma'lumot yo'qolmaydi -- yuqoridagi OYLIK jadvalda baribir hisobga olingan,
    # faqat obyektlar bo'yicha breakdown'da endi faqat HAQIQIY nomlangan obyektlar
    # ko'rinadi.
    by_object = [
        {
            "object_id": obj["object_id"],
            "object_name": obj["object_name"] or "-",
            "spent": obj["spent"],
            "returned": obj["returned"],
            "paid": obj["paid"],
        }
        for obj in data["by_object"]
        if obj["object_id"] != partner["id"]
    ]
    return BotStatisticsResponse(
        found=True,
        months=data["months"],
        by_object=by_object,
        partner_name=partner.get("name"),
        # Malumotlarim bilan BIR XIL balans (invert_balance_sign) -- PDF'dagi oylik
        # "Balans" ustuni shu qiymatdan orqaga hisoblab quriladi (2026-08-04, mijoz
        # aniq so'ragan: ikkalasi bir xil ko'rsatilishi kerak).
        current_balance=invert_balance_sign(partner.get("x_uzs_balance")),
    )


# --- Yetkazib berish tasdiqlash (2026-08-02): alohida "taklif-bot" loyihasi (~/demo/
# taklif-bot) haydovchi tijorat taklifi hujjatining yetkazib berilgan rasmini yuborganda
# shu API'ga POST qiladi. Odoo'ga to'g'ridan-to'g'ri yozilmaydi -- Odoo GET orqali o'zi
# so'rab oladi, xuddi chat-ids/group-memberships bilan bir xil poll+mark-synced naqsh. ---


@app.post(
    "/api/v1/delivery-confirmations",
    dependencies=[Depends(verify_api_key)],
    summary="taklif-bot: Haydovchi yuborgan yetkazib berish rasmini qabul qilish",
)
async def receive_delivery_confirmation(
    payload: DeliveryConfirmationPayload, session: AsyncSession = Depends(get_session)
):
    """
    Rasm diskka saqlanadi, shu doc_id bo'yicha (Order jadvalidan, eng so'nggi ma'lum
    chat_id) mijoz topilib "buyurtmangiz yetib bordi" xabari darhol yuboriladi -- mijoz
    topilmasa yoki xabar yuborib bo'lmasa ham yozuv baribir saqlanadi (Odoo GET orqali
    baribir ko'radi, hech narsa yo'qolmaydi).
    """
    try:
        photo_path = await asyncio.to_thread(save_base64_photo, payload.photo_base64, settings.DELIVERY_PHOTOS_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    customer_chat_id = (
        await session.execute(
            select(Order.chat_id)
            .where(Order.doc_id == payload.doc_id, Order.chat_id.is_not(None))
            .order_by(Order.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    row = DeliveryConfirmation(
        doc_id=payload.doc_id,
        manager_name=payload.manager_name,
        photo_path=photo_path,
        customer_chat_id=customer_chat_id,
        uploaded_by_name=payload.uploaded_by_name,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    if customer_chat_id:
        lang = await get_user_language(session, customer_chat_id)
        try:
            await bot.send_message(
                chat_id=customer_chat_id, text=t("delivery_confirmed", lang, doc_name=payload.doc_id)
            )
            row.customer_notified = True
            await session.commit()
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning(
                "Yetkazib berish xabarini yuborib bo'lmadi (chat_id=%s, doc_id=%s): %s",
                customer_chat_id, payload.doc_id, exc,
            )
        except Exception:
            logger.exception("Yetkazib berish xabarini yuborishda kutilmagan xatolik (doc_id=%s)", payload.doc_id)
    else:
        logger.info("Yetkazib berish: doc_id=%s uchun mijoz chat_id topilmadi", payload.doc_id)

    return {
        "success": True,
        "id": row.id,
        "customer_chat_id": row.customer_chat_id,
        "customer_notified": row.customer_notified,
    }


@app.get(
    "/api/v1/delivery-confirmations",
    response_model=list[DeliveryConfirmationResponse],
    dependencies=[Depends(verify_api_key)],
    summary="Odoo uchun: yetkazib berish rasmlarini olish",
)
async def get_delivery_confirmations(synced: bool | None = None, session: AsyncSession = Depends(get_session)):
    query = select(DeliveryConfirmation)
    if synced is not None:
        query = query.where(DeliveryConfirmation.synced == synced)

    rows = (await session.execute(query.order_by(DeliveryConfirmation.created_at.desc()))).scalars().all()

    return [
        DeliveryConfirmationResponse(
            id=row.id,
            doc_id=row.doc_id,
            manager_name=row.manager_name,
            photo_url=f"/delivery_photos/{Path(row.photo_path).name}",
            synced=row.synced,
        )
        for row in rows
    ]


@app.post(
    "/api/v1/delivery-confirmations/mark-synced",
    dependencies=[Depends(verify_api_key)],
    summary="Odoo uchun: yetkazib berish yozuvlarini sinxronlangan deb belgilash",
)
async def mark_delivery_confirmations_synced(
    payload: SyncUpdateRequest, session: AsyncSession = Depends(get_session)
):
    if not payload.ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ids ro'yxati yuborilishi shart")

    result = await session.execute(
        update(DeliveryConfirmation).where(DeliveryConfirmation.id.in_(payload.ids)).values(synced=True)
    )
    await session.commit()

    return {"success": True, "updated_count": result.rowcount, "updated_ids": payload.ids}


# --- Obyekt lokatsiyalari (2026-08-02): "Buyurtma berish" oqimida mijoz obyekt tanlaganda
# yozilgan GPS lokatsiyalar. MAVJUD obyekt (object_id bor) uchun lokatsiya endi
# to'g'ridan-to'g'ri Odoo'ning o'z partner_latitude/partner_longitude maydoniga yoziladi
# (bot_object_location_submit) -- shu sabab bu GET faqat Odoo'da HALI YO'Q lokatsiyalarni
# qaytaradi: yangi obyektlar (object_id yo'q) VA to'g'ridan-to'g'ri yozish muvaffaqiyatsiz
# bo'lgan mavjud obyektlar (odoo_synced=False) -- ma'lumot hech qachon yo'qolmasligi uchun.
# Odoo'da lokatsiyasi allaqachon bor obyektlar (odoo_synced=True) bu javobda ko'rsatilmaydi.
# Odoo GET orqali o'zi so'rab oladi va /mark-synced bilan belgilaydi, xuddi boshqa
# resurslar bilan bir xil naqsh. ---


@app.get(
    "/api/v1/object-locations",
    response_model=list[ObjectLocationResponse],
    dependencies=[Depends(verify_api_key)],
    summary="Odoo uchun: Odoo'da hali mavjud bo'lmagan obyekt lokatsiyalarini olish",
)
async def get_object_locations(synced: bool | None = None, session: AsyncSession = Depends(get_session)):
    query = select(ObjectLocation).where(ObjectLocation.odoo_synced.is_(False))
    if synced is not None:
        query = query.where(ObjectLocation.synced == synced)

    rows = (await session.execute(query.order_by(ObjectLocation.created_at.desc()))).scalars().all()

    return [
        ObjectLocationResponse(
            id=row.id,
            partner_id=row.partner_id,
            object_id=row.object_id,
            object_name=row.object_name,
            is_new_object=row.is_new_object,
            latitude=row.latitude,
            longitude=row.longitude,
            chat_id=row.chat_id,
            created_by_username=row.created_by_username,
            partner_name=row.partner_name,
            partner_phone=row.partner_phone,
            created_at=row.created_at,
            synced=row.synced,
        )
        for row in rows
    ]


@app.post(
    "/api/v1/object-locations/mark-synced",
    dependencies=[Depends(verify_api_key)],
    summary="Odoo uchun: obyekt lokatsiyasi yozuvlarini sinxronlangan deb belgilash",
)
async def mark_object_locations_synced(payload: SyncUpdateRequest, session: AsyncSession = Depends(get_session)):
    if not payload.ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ids ro'yxati yuborilishi shart")

    result = await session.execute(
        update(ObjectLocation).where(ObjectLocation.id.in_(payload.ids)).values(synced=True)
    )
    await session.commit()

    return {"success": True, "updated_count": result.rowcount, "updated_ids": payload.ids}


# --- Guruh mention'lari (2026-08-03): bot admin qilib qo'shilgan guruhda kimdir botni
# (@BOT_USERNAME) mention qilib yozganda, app.bot.handlers.group_mentions shu endpointga
# POST qiladi. Yuboruvchi Odoo'da (x_telegram_id bo'yicha) TOPILSA ham, TOPILMASA ham
# HAQIQIY crm.lead yoziladi (2026-08-03, ikkinchi pivot -- avval faqat topilgan holatda
# passiv GroupInquiry yozuvi saqlanardi/poll qilinardi; topilgan holatda bu hamon
# QO'SHIMCHA ravishda saqlanadi -- audit nusxa, Odoo GET orqali ham ko'rishi mumkin). ---
@app.post(
    "/api/v1/bot/group-inquiry",
    response_model=GroupInquirySubmitResponse,
    dependencies=[Depends(verify_api_key)],
    summary="Bot: guruhda mention qilingan xabarni Odoo'ga (crm.lead) yozish",
)
async def bot_group_inquiry(payload: GroupInquirySubmitRequest, session: AsyncSession = Depends(get_session)):
    partner = await asyncio.to_thread(find_partner_by_chat_id, payload.chat_id)
    contact_name = (partner.get("name") if partner else None) or payload.full_name or payload.username or "Noma'lum"
    group_label = payload.group_title or str(payload.group_id)

    lead_id = await asyncio.to_thread(
        create_telegram_order_lead,
        partner_id=partner["id"] if partner else None,
        partner_name=contact_name,
        chat_id=payload.chat_id,
        order_details=f"[Guruhdan: {group_label}] {payload.message_text}",
    )

    if not partner:
        return GroupInquirySubmitResponse(found=False, lead_created=lead_id is not None)

    session.add(
        GroupInquiry(
            chat_id=payload.chat_id,
            username=payload.username,
            full_name=payload.full_name,
            group_id=payload.group_id,
            group_title=payload.group_title,
            partner_id=partner["id"],
            partner_name=partner.get("name"),
            message_text=payload.message_text,
        )
    )
    await session.commit()

    return GroupInquirySubmitResponse(found=True, partner_id=partner["id"], lead_created=lead_id is not None)


@app.get(
    "/api/v1/group-inquiries",
    response_model=list[GroupInquiryResponse],
    dependencies=[Depends(verify_api_key)],
    summary="Odoo uchun: guruhda mention qilingan (Odoo mijoziga tegishli) xabarlarni olish",
)
async def get_group_inquiries(synced: bool | None = None, session: AsyncSession = Depends(get_session)):
    query = select(GroupInquiry)
    if synced is not None:
        query = query.where(GroupInquiry.synced == synced)

    rows = (await session.execute(query.order_by(GroupInquiry.created_at.desc()))).scalars().all()

    return [
        GroupInquiryResponse(
            id=row.id,
            chat_id=row.chat_id,
            username=row.username,
            full_name=row.full_name,
            group_id=row.group_id,
            group_title=row.group_title,
            partner_id=row.partner_id,
            partner_name=row.partner_name,
            message_text=row.message_text,
            created_at=row.created_at,
            synced=row.synced,
        )
        for row in rows
    ]


@app.post(
    "/api/v1/group-inquiries/mark-synced",
    dependencies=[Depends(verify_api_key)],
    summary="Odoo uchun: guruh mention yozuvlarini sinxronlangan deb belgilash",
)
async def mark_group_inquiries_synced(payload: GroupInquirySyncRequest, session: AsyncSession = Depends(get_session)):
    if not payload.ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ids ro'yxati yuborilishi shart")

    result = await session.execute(update(GroupInquiry).where(GroupInquiry.id.in_(payload.ids)).values(synced=True))
    await session.commit()

    return {"success": True, "updated_count": result.rowcount, "updated_ids": payload.ids}


@app.get("/health", summary="Health check")
async def health():
    return {"status": "ok"}
