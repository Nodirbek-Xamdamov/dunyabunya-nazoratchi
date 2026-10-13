"""app.bot.handlers.chat_events uchun testlar: bot guruhga qo'shilishi (my_chat_member)
va guruh a'zoligi o'zgarishi (chat_member) real-vaqtda to'g'ri saqlanishini tekshiradi."""

from datetime import datetime, timezone

import pytest
from aiogram.types import (
    Chat,
    ChatMemberAdministrator,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberRestricted,
    ChatMemberUpdated,
    User,
)
from sqlalchemy import select

from app.bot.handlers.chat_events import on_bot_membership_changed, on_group_member_changed
from app.database import async_session_maker
from app.models import BotGroup, GroupMembership, TelegramUser
from app.utils import upsert_telegram_user

GROUP_ID = -1001234567890
GROUP_TITLE = "Test Group"


def _admin_member(user: User) -> ChatMemberAdministrator:
    return ChatMemberAdministrator(
        status="administrator",
        user=user,
        can_be_edited=False,
        is_anonymous=False,
        can_manage_chat=True,
        can_delete_messages=True,
        can_manage_video_chats=True,
        can_restrict_members=True,
        can_promote_members=False,
        can_change_info=True,
        can_invite_users=True,
        can_pin_messages=True,
        can_manage_topics=False,
        can_post_stories=False,
        can_edit_stories=False,
        can_delete_stories=False,
    )


def _plain_member(user: User) -> ChatMemberMember:
    return ChatMemberMember(status="member", user=user)


def _left_member(user: User) -> ChatMemberLeft:
    return ChatMemberLeft(status="left", user=user)


def _restricted_member(user: User, is_member: bool) -> ChatMemberRestricted:
    return ChatMemberRestricted(
        status="restricted",
        user=user,
        is_member=is_member,
        can_send_messages=False,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_change_info=False,
        can_invite_users=False,
        can_pin_messages=False,
        can_manage_topics=False,
        until_date=datetime.now(timezone.utc),
    )


def _make_event(chat: Chat, actor: User, old_member, new_member) -> ChatMemberUpdated:
    return ChatMemberUpdated(
        chat=chat,
        from_user=actor,
        date=datetime.now(timezone.utc),
        old_chat_member=old_member,
        new_chat_member=new_member,
    )


@pytest.fixture
def group_chat() -> Chat:
    return Chat(id=GROUP_ID, type="supergroup", title=GROUP_TITLE)


@pytest.fixture
def bot_user() -> User:
    return User(id=999, is_bot=True, first_name="dunyabunya_assistant_bot")


async def test_bot_added_as_admin_creates_bot_group(group_chat, bot_user):
    event = _make_event(group_chat, bot_user, _left_member(bot_user), _admin_member(bot_user))

    await on_bot_membership_changed(event)

    async with async_session_maker() as session:
        row = await session.get(BotGroup, GROUP_ID)
        assert row is not None
        assert row.title == GROUP_TITLE
        assert row.is_admin is True


async def test_bot_added_as_plain_member_is_not_admin(group_chat, bot_user):
    event = _make_event(group_chat, bot_user, _left_member(bot_user), _plain_member(bot_user))

    await on_bot_membership_changed(event)

    async with async_session_maker() as session:
        row = await session.get(BotGroup, GROUP_ID)
        assert row is not None
        assert row.is_admin is False


async def test_bot_removed_deletes_bot_group(group_chat, bot_user):
    await on_bot_membership_changed(
        _make_event(group_chat, bot_user, _left_member(bot_user), _admin_member(bot_user))
    )
    await on_bot_membership_changed(
        _make_event(group_chat, bot_user, _admin_member(bot_user), _left_member(bot_user))
    )

    async with async_session_maker() as session:
        row = await session.get(BotGroup, GROUP_ID)
        assert row is None


async def test_bot_membership_change_in_private_chat_is_ignored(bot_user):
    private_chat = Chat(id=555, type="private")
    event = _make_event(private_chat, bot_user, _left_member(bot_user), _admin_member(bot_user))

    await on_bot_membership_changed(event)

    async with async_session_maker() as session:
        row = await session.get(BotGroup, 555)
        assert row is None


async def test_known_user_joining_group_creates_membership(group_chat):
    member_user = User(id=222, is_bot=False, first_name="Aziz", username="aziz")

    async with async_session_maker() as session:
        await upsert_telegram_user(session, chat_id=222, username="aziz")
        await session.commit()

    event = _make_event(group_chat, member_user, _left_member(member_user), _plain_member(member_user))
    await on_group_member_changed(event)

    async with async_session_maker() as session:
        result = await session.execute(
            select(GroupMembership).where(GroupMembership.chat_id == 222, GroupMembership.group_id == GROUP_ID)
        )
        membership = result.scalar_one_or_none()
        assert membership is not None
        assert membership.group_title == GROUP_TITLE


async def test_known_user_leaving_group_removes_membership(group_chat):
    member_user = User(id=222, is_bot=False, first_name="Aziz", username="aziz")

    async with async_session_maker() as session:
        await upsert_telegram_user(session, chat_id=222, username="aziz")
        session.add(GroupMembership(chat_id=222, group_id=GROUP_ID, group_title=GROUP_TITLE))
        await session.commit()

    event = _make_event(group_chat, member_user, _plain_member(member_user), _left_member(member_user))
    await on_group_member_changed(event)

    async with async_session_maker() as session:
        result = await session.execute(
            select(GroupMembership).where(GroupMembership.chat_id == 222, GroupMembership.group_id == GROUP_ID)
        )
        assert result.scalar_one_or_none() is None


async def test_restricted_but_still_member_keeps_membership(group_chat):
    member_user = User(id=222, is_bot=False, first_name="Aziz", username="aziz")

    async with async_session_maker() as session:
        await upsert_telegram_user(session, chat_id=222, username="aziz")
        await session.commit()

    event = _make_event(
        group_chat, member_user, _plain_member(member_user), _restricted_member(member_user, is_member=True)
    )
    await on_group_member_changed(event)

    async with async_session_maker() as session:
        result = await session.execute(
            select(GroupMembership).where(GroupMembership.chat_id == 222, GroupMembership.group_id == GROUP_ID)
        )
        assert result.scalar_one_or_none() is not None


async def test_unknown_user_joining_group_is_ignored(group_chat):
    stranger = User(id=333, is_bot=False, first_name="Stranger")
    event = _make_event(group_chat, stranger, _left_member(stranger), _plain_member(stranger))

    await on_group_member_changed(event)

    async with async_session_maker() as session:
        result = await session.execute(
            select(GroupMembership).where(GroupMembership.chat_id == 333)
        )
        assert result.scalar_one_or_none() is None


async def test_bot_account_joining_group_is_ignored(group_chat, bot_user):
    event = _make_event(group_chat, bot_user, _left_member(bot_user), _plain_member(bot_user))

    await on_group_member_changed(event)

    async with async_session_maker() as session:
        result = await session.execute(
            select(GroupMembership).where(GroupMembership.chat_id == bot_user.id)
        )
        assert result.scalar_one_or_none() is None
