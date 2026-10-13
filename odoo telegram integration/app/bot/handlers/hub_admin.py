"""
Ichki (xodimlar) forum-guruhidagi boshqaruv buyruqlari (2026-10-12). Faqat ichki guruhda
ishlaydi -- u yerdagi hamma a'zo xodim hisoblanadi; mijoz guruhlarida va shaxsiy chatda bu
buyruqlarga bot javob bermaydi.

    /hisobot [bugun | kecha | YYYY-MM-DD]   -- statistika hisobotini (HTML) shu yerga yuboradi
    /guruhlar [qidiruv]                     -- kuzatilayotgan guruhlar ro'yxati
    /guruh_ochir <guruh>                    -- guruhni kuzatuvdan chiqarish
    /guruh_yoq <guruh>                      -- kuzatuvga qaytarish
    /menejerlar [guruh]                     -- guruh(lar)ga biriktirilgan menejerlar
    /menejer_qosh <guruh> @username [ism]   -- menejerni qo'lda biriktirish
    /menejer_ochir <guruh> @username        -- qo'lda biriktirilgan menejerni olib tashlash

<guruh> -- guruhning ID'si (masalan -1001234567890) yoki nomining bir qismi.
"""

import html
import logging
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app import daily_report, group_registry, group_stats
from app.bot.handlers import group_monitor
from app.config import settings
from app.models import Group

logger = logging.getLogger("aiogram_bot")

router = Router(name="hub_admin")
router.message.filter(F.chat.func(lambda chat: chat.id == settings.FEEDBACK_HUB_GROUP_ID))

_LIST_LIMIT = 60


def _group_line(group: Group) -> str:
    state = "" if group.is_active else "  ⏸ kuzatilmaydi"
    return f"• {html.escape(group.title or '(nomsiz)')} <code>{group.group_id}</code>{state}"


async def _resolve_group(query: str) -> tuple[Group | None, str]:
    """Bitta guruhni topadi. Topilmasa yoki bir nechta bo'lsa -- (None, foydalanuvchiga javob)."""
    query = query.strip()
    if not query:
        return None, "Guruhni ko'rsating: ID'si yoki nomining bir qismi."
    groups = await group_registry.find_groups(query)
    if not groups:
        return None, f"«{html.escape(query)}» bo'yicha guruh topilmadi. Ro'yxat: /guruhlar"
    if len(groups) > 1:
        lines = "\n".join(_group_line(group) for group in groups[:10])
        return None, f"Bir nechta guruh mos keldi — ID'si bilan yozing:\n{lines}"
    return groups[0], ""


def parse_report_day(argument: str | None) -> date | None:
    """`/hisobot` argumenti -> sana. Bo'sh yoki "bugun" -- bugun, "kecha" -- kecha. Xato bo'lsa None."""
    argument = (argument or "").strip().lower()
    today = group_stats.local_day()
    if argument in ("", "bugun"):
        return today
    if argument == "kecha":
        return today - timedelta(days=1)
    try:
        return date.fromisoformat(argument)
    except ValueError:
        return None


@router.message(Command("hisobot"))
async def handle_report(message: Message, command: CommandObject) -> None:
    day = parse_report_day(command.args)
    if day is None:
        await message.reply("Sana noto'g'ri. Masalan: /hisobot, /hisobot kecha, /hisobot 2026-10-11")
        return
    path, data = await daily_report.generate_report(day)
    thread_id = message.message_thread_id if message.is_topic_message else None
    if not await daily_report.send_report(message.bot, path, data, chat_id=message.chat.id, thread_id=thread_id):
        await message.reply("Hisobot tuzildi, lekin faylni yuborib bo'lmadi.")


async def groups_text(query: str = "") -> str:
    groups = await group_registry.find_groups(query)
    if not groups:
        return "Guruh topilmadi." if query else "Hali birorta guruh ro'yxatga olinmagan."
    inactive = sum(not group.is_active for group in groups)
    lines = [f"<b>Guruhlar: {len(groups)} ta</b>" + (f" (kuzatilmaydi: {inactive})" if inactive else "")]
    lines += [_group_line(group) for group in groups[:_LIST_LIMIT]]
    if len(groups) > _LIST_LIMIT:
        lines.append(f"… yana {len(groups) - _LIST_LIMIT} ta. Qidirish: /guruhlar nomi")
    return "\n".join(lines)


@router.message(Command("guruhlar"))
async def handle_groups(message: Message, command: CommandObject) -> None:
    await message.reply(await groups_text(command.args or ""), parse_mode="HTML")


async def set_group_active_text(query: str, is_active: bool) -> str:
    group, problem = await _resolve_group(query)
    if group is None:
        return problem
    await group_registry.set_group_active(group.group_id, is_active)
    title = html.escape(group.title or str(group.group_id))
    return f"«{title}» kuzatuvga qaytarildi." if is_active else f"«{title}» endi kuzatilmaydi."


@router.message(Command("guruh_ochir"))
async def handle_group_off(message: Message, command: CommandObject) -> None:
    await message.reply(await set_group_active_text(command.args or "", False), parse_mode="HTML")


@router.message(Command("guruh_yoq"))
async def handle_group_on(message: Message, command: CommandObject) -> None:
    await message.reply(await set_group_active_text(command.args or "", True), parse_mode="HTML")


def _manager_label(manager: group_registry.ManagerInfo) -> str:
    name = f"@{manager.username}" if manager.username else html.escape(manager.full_name or str(manager.user_id))
    if manager.username and manager.full_name:
        name += f" ({html.escape(manager.full_name)})"
    return name + (" — unvon bo'yicha" if manager.source == "title" else " — qo'lda")


async def managers_text(query: str = "") -> str:
    if query.strip():
        group, problem = await _resolve_group(query)
        if group is None:
            return problem
        managers = await group_registry.list_managers(group.group_id)
        title = html.escape(group.title or str(group.group_id))
        if not managers:
            return f"«{title}» guruhiga menejer biriktirilmagan."
        return f"<b>{title}</b>\n" + "\n".join(f"• {_manager_label(manager)}" for manager in managers)

    by_group = await group_registry.all_managers()
    if not by_group:
        return (
            "Hech bir guruhga menejer biriktirilmagan.\n"
            "Biriktirish: guruhda xodimni admin qilib unvoniga «Menejer» deb yozing "
            "yoki /menejer_qosh &lt;guruh&gt; @username"
        )
    titles = await group_registry.group_titles()
    lines: list[str] = []
    for group_id, managers in list(by_group.items())[:_LIST_LIMIT]:
        names = ", ".join(_manager_label(manager) for manager in managers)
        lines.append(f"• <b>{html.escape(titles.get(group_id, str(group_id)))}</b>: {names}")
    if len(by_group) > _LIST_LIMIT:
        lines.append(f"… yana {len(by_group) - _LIST_LIMIT} ta guruh. Bitta guruh: /menejerlar nomi")
    return "\n".join(lines)


@router.message(Command("menejerlar"))
async def handle_managers(message: Message, command: CommandObject) -> None:
    await message.reply(await managers_text(command.args or ""), parse_mode="HTML")


def split_manager_args(arguments: str) -> tuple[str, str, str]:
    """"<guruh> @username [ism]" -> (guruh, username, ism). @username topilmasa username bo'sh."""
    words = arguments.split()
    for index, word in enumerate(words):
        if word.startswith("@") and len(word) > 1:
            return " ".join(words[:index]), word, " ".join(words[index + 1:])
    return arguments.strip(), "", ""


async def add_manager_text(arguments: str) -> str:
    query, username, full_name = split_manager_args(arguments)
    if not username:
        return "Yozilishi: /menejer_qosh &lt;guruh&gt; @username [ism]"
    group, problem = await _resolve_group(query)
    if group is None:
        return problem
    title = html.escape(group.title or str(group.group_id))
    if not await group_registry.add_manager(group.group_id, username, full_name or None):
        return f"{html.escape(username)} «{title}» guruhiga allaqachon biriktirilgan."
    group_monitor.forget_group_managers(group.group_id)
    return f"{html.escape(username)} «{title}» guruhiga menejer qilib biriktirildi."


@router.message(Command("menejer_qosh"))
async def handle_add_manager(message: Message, command: CommandObject) -> None:
    await message.reply(await add_manager_text(command.args or ""), parse_mode="HTML")


async def remove_manager_text(arguments: str) -> str:
    query, username, _name = split_manager_args(arguments)
    if not username:
        return "Yozilishi: /menejer_ochir &lt;guruh&gt; @username"
    group, problem = await _resolve_group(query)
    if group is None:
        return problem
    title = html.escape(group.title or str(group.group_id))
    if not await group_registry.remove_manager(group.group_id, username):
        return f"{html.escape(username)} «{title}» guruhida menejer emas."
    group_monitor.forget_group_managers(group.group_id)
    return f"{html.escape(username)} «{title}» guruhi menejerlaridan olib tashlandi."


@router.message(Command("menejer_ochir"))
async def handle_remove_manager(message: Message, command: CommandObject) -> None:
    await message.reply(await remove_manager_text(command.args or ""), parse_mode="HTML")
