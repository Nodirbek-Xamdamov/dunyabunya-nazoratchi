"""Monitoring testlari uchun umumiy yordamchilar: bazaga guruh, xabar va tasnif qo'shish."""

from datetime import datetime, timedelta, timezone

from app import monitor_db
from app.monitor_db import ClassificationResult

CHAT = -1001000000001
OTHER_CHAT = -1001000000002
MANAGER_ID, ADMIN_ID, CUSTOMER_ID = 101, 102, 200
# 2026-10-13 10:00 Toshkent vaqti
T0 = datetime(2026, 10, 13, 5, 0, tzinfo=timezone.utc)
DAY = T0.astimezone(timezone(timedelta(hours=5))).date()


async def add_group(chat_id=CHAT, title="Chilonzor obyekti", *, admins=(ADMIN_ID, MANAGER_ID), managers=((MANAGER_ID, "menejer_bek", "Bek"),)):
    await monitor_db.upsert_group(chat_id, title)
    return await monitor_db.save_group_admins(chat_id, set(admins), list(managers))


async def add_message(
    message_id, text="Sement qanchadan?", *, chat_id=CHAT, sender_id=CUSTOMER_ID, name="Vali Mijoz", username="vali",
    is_staff=False, minutes=0.0, media_type="text", reply_to=None,
) -> int:
    """Xabar yozadi va uning bazadagi ID'sini qaytaradi."""
    await monitor_db.save_message(
        chat_id=chat_id, message_id=message_id, sender_id=sender_id, sender_username=username, sender_name=name,
        is_staff=is_staff, text=text, media_type=media_type, file_id=None, reply_to_message_id=reply_to,
        sent_at=T0 + timedelta(minutes=minutes),
    )
    return (await get_message(chat_id, message_id)).id


async def get_message(chat_id, message_id):
    from sqlalchemy import select

    async with monitor_db.session_maker() as session:
        return (
            await session.execute(
                select(monitor_db.Message).where(
                    monitor_db.Message.chat_id == chat_id, monitor_db.Message.message_id == message_id
                )
            )
        ).scalar_one_or_none()


async def classify(
    message_pk, category="Narx va To'lovlar", sentiment="Neytral", *, related=True, needs_manager=False,
    summary="Mijoz narx so'radi",
):
    result = ClassificationResult(
        message_pk, related, category if related else None, sentiment if related else None, summary,
        needs_manager and related,
    )
    await monitor_db.save_classifications([result], "test")


async def all_rows(model):
    from sqlalchemy import select

    async with monitor_db.session_maker() as session:
        return list((await session.execute(select(model))).scalars())
