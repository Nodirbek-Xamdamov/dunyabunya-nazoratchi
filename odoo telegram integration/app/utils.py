"""
Yordamchi funksiyalar: username normalizatsiya, PDF saqlash/o'chirish, webhook audit log, avto-tozalash.
"""

import asyncio
import base64
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def clean_text_for_odoo(raw_msg: str | None) -> str:
    """
    Odoo'dan kelgan matnni Telegram'ga yuborishdan oldin slash (/), asterisk (*),
    HTML taglar va ortiqcha bo'shliqlardan tozalaydi, paragraflar orasini saqlaydi.
    """
    if not raw_msg:
        return ""

    text = re.sub(r"<[^>]+>", "\n", str(raw_msg))
    text = text.replace("/", "").replace("*", "")
    # Slash/asterisk olib tashlangach qolgan ortiqcha bo'shliqlarni yig'ish
    text = re.sub(r"[ \t]{2,}", " ", text)
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    result = "\n\n".join(lines)
    # Menejer ismi "Hurmat bilan," dan keyin bo'sh qatorsiz, bevosita yangi qatorga tushsin
    result = re.sub(r"(Hurmat bilan,)\s*", r"\1\n", result)
    return result


def normalize_username(raw_username: str) -> str:
    """
    Odoo'da xodimlar tomonidan turlicha kiritilgan username'larni bir xil
    standartga keltiradi.
    """
    if not raw_username or not isinstance(raw_username, str):
        raise ValueError("Username bo'sh yoki noto'g'ri formatda kelgan")

    normalized = raw_username.strip()
    normalized = normalized.lstrip("@")
    normalized = normalized.strip()
    normalized = normalized.lower()

    if not normalized:
        raise ValueError("Normalizatsiyadan so'ng username bo'sh qoldi")

    return normalized


async def upsert_telegram_user(
    session: AsyncSession, chat_id: int, username: str | None, language: str | None = None
) -> None:
    """
    Botga /start bosgan (yoki chat_id'si boshqa yo'l bilan ma'lum bo'lgan) foydalanuvchini
    `telegram_users` jadvalida yozadi/yangilaydi. Guruh a'zoligini shu identifikatsiya
    orqali aniqlash uchun kerak (app.userbot.sync_group_memberships). `language` berilsa
    (foydalanuvchi til tanlaganda) saqlanadi, aks holda mavjud/standart qiymat qoladi.
    """
    from app.models import TelegramUser

    normalized = normalize_username(username) if username else None

    existing = await session.get(TelegramUser, chat_id)
    if existing:
        existing.username = username
        existing.normalized_username = normalized
        existing.last_seen_at = datetime.now(timezone.utc)
        if language:
            existing.language = language
    else:
        session.add(
            TelegramUser(
                chat_id=chat_id,
                username=username,
                normalized_username=normalized,
                language=language or "uz",
            )
        )


def normalize_uz_phone(phone: str | None) -> str | None:
    """Mijozga KO'RSATILADIGAN telefon raqamlarini bir xil, "bosilsa qo'ng'iroq
    qilinadigan" ko'rinishga keltiradi (2026-08-06, mijoz aniq so'ragan, barcha
    bo'limlarga tegishli qoida):
    1) bo'sh joy/tire kabi ajratkichlar olib tashlanadi (uzluksiz raqam);
    2) agar raqam O'ZBEKISTON kodisiz (998/+998) 9 ta raqamdan iborat bo'lsa
       (masalan "931234567"), "+998" prefiksi QO'SHILADI; kod allaqachon bor bo'lsa
       qayta qo'shilmaydi.
    Bo'sh/None qiymat O'ZGARTIRILMAYDI (None qaytadi) -- Odoo'da raqam umuman
    o'rnatilmagan bo'lsa, mavjud bo'lmagan raqam "to'qib chiqarilmaydi"."""
    if not phone:
        return None
    cleaned = re.sub(r"[^\d+]", "", str(phone))
    if not cleaned:
        return None
    digits = cleaned.lstrip("+")
    if digits.startswith("998"):
        return f"+{digits}"
    if len(digits) == 9:
        return f"+998{digits}"
    return cleaned


async def get_user_language(session: AsyncSession, chat_id: int | None) -> str:
    """Berilgan chat_id uchun saqlangan tilni qaytaradi, topilmasa "uz" (standart)."""
    if not chat_id:
        return "uz"

    from app.models import TelegramUser

    user = await session.get(TelegramUser, chat_id)
    return user.language if user and user.language else "uz"


async def cleanup_pdf_if_no_longer_needed(session: AsyncSession, pdf_path: str | None) -> None:
    """
    Bitta webhook chaqiruvida bir nechta Order (turli username'lar + guruh) bitta xil
    `pdf_path`ni bo'lishishi mumkin -- shu sabab fayl faqat SHU pdf_path'ga bog'liq
    HAMMA buyurtma terminal holatga yetgach o'chiriladi: confirmation_status endi
    PENDING emas (tasdiqlangan/rad etilgan/o'zgartirish so'ralgan) yoki status EXPIRED
    bo'lgan -- bu qoida individual va guruh buyurtmalari uchun bir xil. Hali /start
    bosmagan yoki hali qaror qilmagan mijoz uchun qayta yuborilishi mumkin bo'lgan fayl
    bu yerda o'chirilmaydi (handlers/start.py qayta yuborishga tayanadi).
    """
    if not pdf_path:
        return

    from app.models import ConfirmationStatus, Order, OrderStatus

    orders = (
        await session.execute(select(Order).where(Order.pdf_path == pdf_path))
    ).scalars().all()

    def _still_needs_file(order: Order) -> bool:
        if order.status == OrderStatus.EXPIRED.value:
            return False
        return order.confirmation_status == ConfirmationStatus.PENDING.value

    if not any(_still_needs_file(o) for o in orders):
        delete_file_safely(pdf_path)


def save_base64_pdf(base64_data: str, storage_dir: str) -> str:
    """
    Base64 formatidagi PDF ma'lumotini diskka faylga yozadi va uning yo'lini qaytaradi.
    """
    os.makedirs(storage_dir, exist_ok=True)

    try:
        if "," in base64_data and base64_data.strip().startswith("data:"):
            base64_data = base64_data.split(",", 1)[1]
        pdf_bytes = base64.b64decode(base64_data)
    except Exception as exc:
        logger.error("PDF base64 decode qilishda xatolik: %s", exc)
        raise ValueError("PDF fayl base64 formatida noto'g'ri kelgan") from exc

    filename = f"order_{uuid.uuid4().hex}.pdf"
    filepath = os.path.join(storage_dir, filename)

    try:
        with open(filepath, "wb") as f:
            f.write(pdf_bytes)
    except OSError as exc:
        logger.error("PDF faylni diskka yozishda xatolik: %s", exc)
        raise

    return filepath


def save_base64_photo(base64_data: str, storage_dir: str) -> str:
    """
    Base64 formatidagi rasm ma'lumotini diskka faylga yozadi va uning yo'lini qaytaradi
    (save_base64_pdf bilan bir xil naqsh, faqat taklif-botdan kelgan yetkazib berish
    rasmlari uchun -- .jpg kengaytma va boshqa fayl nomi prefiksi bilan).
    """
    os.makedirs(storage_dir, exist_ok=True)

    try:
        if "," in base64_data and base64_data.strip().startswith("data:"):
            base64_data = base64_data.split(",", 1)[1]
        photo_bytes = base64.b64decode(base64_data)
    except Exception as exc:
        logger.error("Rasm base64 decode qilishda xatolik: %s", exc)
        raise ValueError("Rasm fayl base64 formatida noto'g'ri kelgan") from exc

    filename = f"delivery_{uuid.uuid4().hex}.jpg"
    filepath = os.path.join(storage_dir, filename)

    try:
        with open(filepath, "wb") as f:
            f.write(photo_bytes)
    except OSError as exc:
        logger.error("Rasm faylni diskka yozishda xatolik: %s", exc)
        raise

    return filepath


def save_webhook_payload_log(payload: dict, log_dir: str) -> str | None:
    """
    Audit va debugging maqsadida kelgan xom webhook JSON payload'ini
    diskka saqlaydi. Format: webhook_YYYY-MM-DD_HH-MM-SS_mikrosekund.json
    """
    os.makedirs(log_dir, exist_ok=True)
    now = datetime.now()
    filename = f"webhook_{now.strftime('%Y-%m-%d_%H-%M-%S')}_{now.microsecond}.json"
    filepath = os.path.join(log_dir, filename)

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        return filepath
    except Exception as exc:
        logger.warning("Webhook audit logni saqlab bo'lmadi: %s", exc)
        return None


def delete_file_safely(filepath: str | None) -> None:
    """PDF fayl bot orqali yuborilgandan so'ng diskdan xavfsiz o'chiriladi."""
    if not filepath:
        return
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            logger.info("Fayl o'chirildi: %s", filepath)
    except OSError as exc:
        logger.warning("Faylni o'chirishda xatolik (%s): %s", filepath, exc)


def cleanup_old_webhook_logs(log_dir: str, days: int = 7) -> None:
    """Belgilangan kundan (default 7 kun) eski JSON webhook log fayllarini o'chiradi."""
    if not os.path.exists(log_dir):
        return

    now = time.time()
    cutoff_time = now - (days * 86400)
    deleted_count = 0

    try:
        for filename in os.listdir(log_dir):
            file_path = os.path.join(log_dir, filename)
            if os.path.isfile(file_path) and filename.endswith(".json"):
                if os.path.getmtime(file_path) < cutoff_time:
                    os.remove(file_path)
                    deleted_count += 1
        if deleted_count > 0:
            logger.info("%s kundan eski %s ta webhook log fayli o'chirildi.", days, deleted_count)
    except Exception as exc:
        logger.warning("Webhook loglarni tozalashda xatolik: %s", exc)


async def background_maintenance_loop() -> None:
    """Davriy tozalash vazifalari (buyurtma expiry, 30 kunlik eski yozuv/fayl tozalash,
    webhook log tozalash).

    2026-08-05, PRODUCTION tayyorlash/tezlashtirish ishlari doirasida `fastapi_app.py`
    ichidan (`lifespan`'dan) BU YERGA (`app/utils.py`) ko'chirildi -- FastAPI xizmati
    endi bir nechta uvicorn WORKER jarayoni bilan ishga tushiriladi (`docker-compose.yml`,
    ko'proq CPU yadrosidan foydalanish/tezlashtirish uchun), lekin har bir worker o'z
    ALOHIDA jarayoni (va o'z `lifespan`i) bo'lgani sabab, bu funksiya avvalgidek
    `fastapi_app.py`da qolganida edi -- HAR BIR worker uni MUSTAQIL/PARALLEL ishga
    tushirar edi (masalan 3 worker = soatiga 3 marta bir xil DELETE/UPDATE so'rovi,
    foydasiz va yengil lock-kontensiya xavfi). Yechim: bu davriy vazifa ENDI faqat
    `app/bot/__init__.py`'ning `main()`'ida (2026-08-05'da qo'shilgan
    `asyncio.create_task(...)`) ishga tushiriladi -- `bot` konteyneri (aiogram
    long-polling) allaqachon me'moriy jihatdan HAR DOIM BITTA nusxada ishlashi SHART
    bo'lgan yagona jarayon (bir nechta nusxa polling qilsa xabarlar takrorlanib
    qoladi), shu sabab bu yerga qo'shilgan yana bitta background task xavfsiz --
    hech qanday qo'shimcha konteyner/qulflash mexanizmi kerak emas."""
    from app.config import settings
    from app.database import async_session_maker
    from app.models import Order, OrderStatus

    while True:
        try:
            async with async_session_maker() as session:
                now_utc = datetime.now(timezone.utc)

                expiration_threshold = now_utc - timedelta(hours=settings.ORDER_EXPIRATION_HOURS)
                expiring_pdf_paths = (
                    await session.execute(
                        select(Order.pdf_path)
                        .distinct()
                        .where(
                            Order.status == OrderStatus.PENDING.value,
                            Order.created_at < expiration_threshold,
                            Order.pdf_path.is_not(None),
                        )
                    )
                ).scalars().all()

                result_expire = await session.execute(
                    update(Order)
                    .where(Order.status == OrderStatus.PENDING.value, Order.created_at < expiration_threshold)
                    .values(status=OrderStatus.EXPIRED.value)
                )

                db_cleanup_threshold = now_utc - timedelta(days=30)
                stale_paths = (
                    await session.execute(
                        select(Order.pdf_path)
                        .where(Order.synced == True, Order.created_at < db_cleanup_threshold)
                    )
                ).scalars().all()

                result_cleanup = await session.execute(
                    delete(Order)
                    .where(Order.synced == True, Order.created_at < db_cleanup_threshold)
                )

                await session.commit()

                for stale_path in stale_paths:
                    delete_file_safely(stale_path)

                for pdf_path in expiring_pdf_paths:
                    await cleanup_pdf_if_no_longer_needed(session, pdf_path)

                if result_expire.rowcount:
                    logger.info("%s ta buyurtma 'expired' holatiga o'tkazildi", result_expire.rowcount)
                if result_cleanup.rowcount:
                    logger.info("Bazadan %s ta eski sinxronlangan yozuv o'chirildi (30 kun+)", result_cleanup.rowcount)

            cleanup_old_webhook_logs(settings.WEBHOOK_LOG_DIR, days=7)

        except Exception:
            logger.exception("Fondagi tozalash vazifalarini bajarishda xatolik")

        await asyncio.sleep(settings.EXPIRATION_CHECK_INTERVAL_SECONDS)


_odoo_last_healthy = True


async def odoo_health_check_loop(bot_instance) -> None:
    """Odoo API kaliti odatda ~90 kunda muddati tugaydi -- buni "unutib qo'yish"ga
    qarshi (2026-08-06): davriy ravishda `GET /api/v1/odoo-health`ni (fastapi
    konteyneri, `app/odoo_client.py`'ning `check_odoo_connection()`i orqali majburiy
    qayta autentifikatsiya qiladi) so'raydi. Sog'lom holatdan sog'lom-emas holatga
    o'tganda GINA (takroriy spam emas) `settings.ODOO_ALERT_CHAT_ID`ga Telegram orqali
    ogohlantirish yuboradi; tiklanganda ham bitta xabar bilan xabar beradi. Holat
    xotirada (`_odoo_last_healthy`) saqlanadi -- bu funksiya faqat `app/bot/__init__.py`
    orqali (aiogram polling -- me'moriy jihatdan HAR DOIM yagona nusxada ishlaydigan
    yagona jarayon) ishga tushirilgani sabab, ko'p-worker orasidagi poyga xavfi yo'q
    (`background_maintenance_loop`ning yuqoridagi izohiga qarang -- xuddi shu sabab bilan
    shu yerga joylashtirilgan)."""
    global _odoo_last_healthy
    from app.bot.internal_api import get_internal_client
    from app.config import settings

    while True:
        try:
            client = get_internal_client()
            resp = await client.get(
                "/api/v1/odoo-health",
                headers={"X-API-Key": settings.API_KEY},
            )
            healthy = resp.status_code == 200 and resp.json().get("healthy") is True
        except Exception:
            logger.exception("Odoo health-check so'rovida xatolik")
            healthy = False

        if healthy != _odoo_last_healthy:
            try:
                if not healthy:
                    text = (
                        "🔴 DIQQAT: Odoo bilan bog'lanib bo'lmayapti.\n\n"
                        "Sabab ko'pincha ODOO_API_KEY muddati tugashi (odatda ~90 kunda) "
                        "bo'ladi -- yangi kalit generatsiya qilib, serverdagi .env "
                        "faylida yangilash kerak."
                    )
                else:
                    text = "✅ Odoo bilan bog'lanish tiklandi."
                await bot_instance.send_message(chat_id=settings.ODOO_ALERT_CHAT_ID, text=text)
            except Exception:
                logger.exception("Odoo health-check ogohlantirishini yuborishda xatolik")

        _odoo_last_healthy = healthy
        await asyncio.sleep(settings.ODOO_HEALTH_CHECK_INTERVAL_SECONDS)
