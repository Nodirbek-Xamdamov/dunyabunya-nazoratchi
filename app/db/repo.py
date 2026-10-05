from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConversationMessage, Manager, TaskLog, Topic


async def get_topic_by_telegram_id(session: AsyncSession, telegram_topic_id: int) -> Topic | None:
    result = await session.execute(
        select(Topic).where(Topic.telegram_topic_id == telegram_topic_id, Topic.is_active.is_(True))
    )
    return result.scalar_one_or_none()


async def get_topic_by_name(session: AsyncSession, name: str) -> Topic | None:
    result = await session.execute(select(Topic).where(Topic.name == name, Topic.is_active.is_(True)))
    return result.scalar_one_or_none()


async def list_topics(session: AsyncSession) -> list[Topic]:
    result = await session.execute(select(Topic).where(Topic.is_active.is_(True)))
    return list(result.scalars())


async def get_manager_by_telegram_id(session: AsyncSession, telegram_user_id: int) -> Manager | None:
    result = await session.execute(
        select(Manager).where(Manager.telegram_user_id == telegram_user_id, Manager.is_active.is_(True))
    )
    return result.scalar_one_or_none()


async def get_manager_by_username(session: AsyncSession, telegram_username: str) -> Manager | None:
    result = await session.execute(
        select(Manager).where(
            Manager.telegram_username == telegram_username.lstrip("@").casefold(), Manager.is_active.is_(True)
        )
    )
    return result.scalar_one_or_none()


async def activate_manager_by_username(
    session: AsyncSession, telegram_username: str, telegram_user_id: int
) -> Manager | None:
    manager = await get_manager_by_username(session, telegram_username)
    if manager is None or manager.telegram_user_id is not None:
        return None
    manager.telegram_user_id = telegram_user_id
    await session.commit()
    return manager


async def upsert_manager(
    session: AsyncSession,
    *,
    telegram_username: str,
    full_name: str,
    department: str,
    odoo_project_id: int,
    odoo_login: str,
    odoo_api_key: str,
) -> Manager:
    username = telegram_username.lstrip("@").casefold()
    result = await session.execute(select(Manager).where(Manager.telegram_username == username))
    manager = result.scalar_one_or_none()
    if manager is None:
        manager = Manager(telegram_username=username)
        session.add(manager)
    manager.full_name = full_name
    manager.department = department
    manager.odoo_project_id = odoo_project_id
    manager.odoo_login = odoo_login
    manager.odoo_api_key = odoo_api_key
    manager.is_active = True
    await session.commit()
    return manager


async def deactivate_manager(session: AsyncSession, telegram_username: str) -> bool:
    manager = await get_manager_by_username(session, telegram_username)
    if manager is None:
        return False
    manager.is_active = False
    await session.commit()
    return True


async def get_recent_conversation(
    session: AsyncSession, telegram_user_id: int, limit: int = 12
) -> list[ConversationMessage]:
    result = await session.execute(
        select(ConversationMessage)
        .where(ConversationMessage.telegram_user_id == telegram_user_id)
        .order_by(ConversationMessage.id.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def add_conversation_message(session: AsyncSession, telegram_user_id: int, role: str, content: str) -> None:
    session.add(ConversationMessage(telegram_user_id=telegram_user_id, role=role, content=content))
    await session.commit()


async def save_task_log(
    session: AsyncSession,
    *,
    odoo_task_id: int,
    odoo_project_id: int,
    telegram_chat_id: int | None = None,
    telegram_topic_id: int | None = None,
    telegram_message_id: int | None = None,
    title: str,
    created_by_telegram_user_id: int | None = None,
) -> None:
    session.add(
        TaskLog(
            odoo_task_id=odoo_task_id,
            odoo_project_id=odoo_project_id,
            telegram_chat_id=telegram_chat_id,
            telegram_topic_id=telegram_topic_id,
            telegram_message_id=telegram_message_id,
            title=title,
            created_by_telegram_user_id=created_by_telegram_user_id,
        )
    )
    await session.commit()


async def get_task_log_by_odoo_task_id(session: AsyncSession, odoo_task_id: int) -> TaskLog | None:
    """Berilgan Odoo task ID uchun eng so'nggi audit yozuvini topadi (kim yaratganini
    bilish uchun — /otask'ning "faqat o'zi yaratganini o'chira oladi" tekshiruvida)."""
    result = await session.execute(
        select(TaskLog).where(TaskLog.odoo_task_id == odoo_task_id).order_by(TaskLog.id.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def get_recent_task_logs(session: AsyncSession, *, days: int = 90, limit: int = 1000) -> list[TaskLog]:
    """Bot orqali yaratilgan (task_log jurnalidagi) so'nggi vazifalar — P1-6
    (muddati o'tgan vazifa eslatmasi) shular orasidan Odoo'da hali ham
    "ochiq" bo'lganlarini filtrlaydi. `days` bilan chegaralanadi — jurnal
    cheksiz o'smasin va so'rov tez bo'lsin (juda eski, allaqachon unutilgan
    vazifalar uchun eslatma keraksiz)."""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await session.execute(
        select(TaskLog).where(TaskLog.created_at >= cutoff).order_by(TaskLog.id.desc()).limit(limit)
    )
    return list(result.scalars().all())


# ─── Employee funksiyalari `app/employee_store.py`ga ko'chirildi ───────────
# (fayl-asosli katalog — Postgres emas, tezroq va yengilroq qidiruv uchun)


# ─── Guruh xabarlari (1 kunlik tarix) ──────────────────────────────────────

async def save_group_message(session: AsyncSession, *, chat_id: int, topic_id: int | None,
                              user_id: int, user_name: str, text: str, msg_type: str = "text") -> None:
    """Guruh xabarini saqlash — AI kontekst uchun."""
    from app.db.models import GroupMessage
    session.add(GroupMessage(
        telegram_chat_id=chat_id, telegram_topic_id=topic_id,
        from_user_id=user_id, from_user_name=user_name,
        text=text, message_type=msg_type,
    ))
    await session.commit()


async def get_group_messages_last_24h(session: AsyncSession, chat_id: int, limit: int = 50) -> list:
    """Oxirgi 24 soatdagi guruh xabarlarini olish — AI kontekst uchun."""
    from app.db.models import GroupMessage
    from datetime import datetime, timedelta, timezone
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await session.execute(
        select(GroupMessage)
        .where(GroupMessage.telegram_chat_id == chat_id)
        .where(GroupMessage.created_at >= since)
        .order_by(GroupMessage.created_at.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def get_all_group_messages(session: AsyncSession, chat_id: int) -> list:
    """Shu chat uchun bazadagi BARCHA (hali xulosalanmagan) guruh xabarlari —
    kunlik xotira xulosasi (memory_digest.py) uchun."""
    from app.db.models import GroupMessage
    result = await session.execute(
        select(GroupMessage)
        .where(GroupMessage.telegram_chat_id == chat_id)
        .order_by(GroupMessage.created_at.asc())
    )
    return list(result.scalars().all())


async def delete_group_messages(session: AsyncSession, chat_id: int, *, up_to_id: int | None = None) -> int:
    """Shu chat uchun guruh xabarlarini o'chiradi — kunlik xulosa yozilgach xom xabarlar
    jadvali cheksiz o'smasligi uchun (memory_digest.py). `up_to_id` berilsa faqat shu
    id'gacha bo'lganlar (ya'ni xulosaga kirganlar) o'chiriladi."""
    from sqlalchemy import delete as sql_delete
    from app.db.models import GroupMessage
    statement = sql_delete(GroupMessage).where(GroupMessage.telegram_chat_id == chat_id)
    if up_to_id is not None:
        statement = statement.where(GroupMessage.id <= up_to_id)
    result = await session.execute(statement)
    await session.commit()
    return result.rowcount or 0

