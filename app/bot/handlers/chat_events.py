"""
Guruh bilan bog'liq real-vaqt eventlar:
- my_chat_member -- bot o'zi guruhga qo'shilganda/admin qilinganda/chiqarilganda
- chat_member    -- botga tanish (avval /start bosgan) foydalanuvchi guruhga
                     qo'shilganda/chiqib ketganda

Bu handlerlar Telethon userbotning har 15 daqiqalik to'liq skanerlashi
(app.userbot.sync_group_memberships) bilan PARALLEL ishlaydi -- bir-birini
almashtirmaydi, faqat guruh_memberships jadvalini tezroq (real-vaqtda) yangilaydi.
"""

import logging

from aiogram import Router
from aiogram.types import ChatMemberUpdated
from sqlalchemy import select

from app import monitor_db, monitor_sla
from app.bot.handlers.group_monitor import excluded_chat_ids
from app.config import settings
from app.database import async_session_maker
from app.models import BotGroup, GroupMembership, TelegramUser

logger = logging.getLogger("aiogram_bot")

router = Router(name="chat_events")

_GROUP_CHAT_TYPES = ("group", "supergroup")
_ADMIN_STATUSES = ("creator", "administrator")


def _is_monitored(chat_id: int) -> bool:
    return settings.MONITOR_ENABLED and chat_id not in excluded_chat_ids()


def _is_present(member) -> bool:
    """ChatMember holatidan kelib chiqib, u hali ham chat a'zosimi yo'qmi aniqlaydi."""
    if member.status in ("creator", "administrator", "member"):
        return True
    if member.status == "restricted":
        return bool(member.is_member)
    return False  # "left" | "kicked"


@router.my_chat_member()
async def on_bot_membership_changed(event: ChatMemberUpdated) -> None:
    """Bot guruhga qo'shilganda/chiqarilganda `bot_groups`ni darhol yangilaydi."""
    if event.chat.type not in _GROUP_CHAT_TYPES:
        return

    group_id = event.chat.id
    title = event.chat.title
    present = _is_present(event.new_chat_member)
    is_admin = event.new_chat_member.status == "administrator"

    async with async_session_maker() as session:
        existing = await session.get(BotGroup, group_id)

        if present:
            if existing:
                existing.title = title
                existing.is_admin = is_admin
            else:
                session.add(BotGroup(group_id=group_id, title=title, is_admin=is_admin))
            logger.info("Bot guruhda: '%s' (group_id=%s, admin=%s)", title, group_id, is_admin)
        elif existing:
            await session.delete(existing)
            logger.info("Bot guruhdan chiqarildi: '%s' (group_id=%s)", title, group_id)

        await session.commit()

    # Yangi guruh monitoring bazasiga ham darhol yoziladi (birinchi xabarini kutmasdan); bot
    # chiqarilgan guruh esa keyingi hisobotlarda alohida varaq olmaydi (xabarlari bazada qoladi)
    if _is_monitored(group_id):
        try:
            if present:
                await monitor_db.upsert_group(group_id, title)
            else:
                await monitor_db.set_group_present(group_id, False)
        except Exception:
            logger.exception("Guruhni monitoring bazasiga yozishda xatolik (group_id=%s)", group_id)


@router.chat_member()
async def on_group_member_changed(event: ChatMemberUpdated) -> None:
    """Botga tanish foydalanuvchining guruh a'zoligi o'zgarsa `group_memberships`ni
    real-vaqtda yangilaydi. Botga hali /start bosmagan foydalanuvchilar e'tiborga
    olinmaydi -- ularning chat_id'si bizga noma'lum."""
    if event.chat.type not in _GROUP_CHAT_TYPES:
        return

    user = event.new_chat_member.user
    if user.is_bot:
        return

    chat_id = user.id
    group_id = event.chat.id

    # Admin qo'shildi/olindi yoki unvoni o'zgardi -- guruhning adminlari va menejeri qayta olinadi
    if _is_monitored(group_id) and (
        event.old_chat_member.status in _ADMIN_STATUSES or event.new_chat_member.status in _ADMIN_STATUSES
    ):
        try:
            await monitor_sla.admins_changed(group_id)
        except Exception:
            logger.exception("Guruh adminlarini yangilashga belgilashda xatolik (group_id=%s)", group_id)

    async with async_session_maker() as session:
        known_user = await session.get(TelegramUser, chat_id)
        if not known_user:
            return

        existing = (
            await session.execute(
                select(GroupMembership).where(
                    GroupMembership.chat_id == chat_id, GroupMembership.group_id == group_id
                )
            )
        ).scalar_one_or_none()

        if _is_present(event.new_chat_member):
            if not existing:
                session.add(GroupMembership(chat_id=chat_id, group_id=group_id, group_title=event.chat.title))
                logger.info("Guruh a'zoligi qo'shildi: chat_id=%s, group_id=%s", chat_id, group_id)
        elif existing:
            await session.delete(existing)
            logger.info("Guruh a'zoligi olib tashlandi: chat_id=%s, group_id=%s", chat_id, group_id)

        await session.commit()
