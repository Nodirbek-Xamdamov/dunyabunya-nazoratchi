from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings


def task_action_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Bajarildi", callback_data=f"taskdone:{task_id}:done"),
                InlineKeyboardButton(text="❌ Bekor qilindi", callback_data=f"taskdone:{task_id}:cancel"),
            ]
        ]
    )


def task_status_suffix(done: bool) -> str:
    return f"\n\n{'✅ Bajarildi deb belgilandi.' if done else '❌ Bekor qilindi deb belgilandi.'}"


def success_reply_text(title: str, department: str, assignee_name: str | None, deadline: str | None,
                       task_id: int | None = None) -> str:
    lines = [f"✅ Odooga yozildi — {department}", f"📌 {title}"]
    if assignee_name:
        lines.append(f"👤 Mas'ul: {assignee_name}")
    if deadline:
        lines.append(f"🗓 Muddat: {deadline}")
    if task_id:
        lines.append(f"🆔 #{task_id}")
    return "\n".join(lines)


def task_url(task_id: int) -> str:
    """Vazifaning Odoo'dagi sahifasi (eski ko'rinishdagi havola — barcha versiyalarda ochiladi)."""
    return f"{settings.ODOO_URL.rstrip('/')}/web#id={task_id}&model=project.task&view_type=form"


def created_task_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """Yaratilgan vazifa ostidagi tugmalar: Odoo'da ochish va (xato yozilgan bo'lsa) o'chirish."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔗 Odoo'da ochish", url=task_url(task_id)),
        InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"task_del:{task_id}"),
    ]])


def delete_confirm_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Ha, o'chirilsin", callback_data=f"task_delok:{task_id}"),
        InlineKeyboardButton(text="Yo'q, qolsin", callback_data=f"task_delno:{task_id}"),
    ]])


def assignee_not_found_text(name: str) -> str:
    return f"⚠️ \"{name}\" ismli xodim Odoo'da topilmadi — vazifa mas'ulsiz yaratildi."


def general_reminder_text(group_label: str, sender: str, original_text: str) -> str:
    return f"📌 Manba: {group_label} | {sender}: {original_text}"


def not_ceo_text() -> str:
    return "⛔ Bu buyruq faqat CEO uchun."


def link_manager_usage_text() -> str:
    return "Format: /menejer_biriktir <@username> <Bo'lim> <odoo_login> <odoo_api_key> <To'liq ism>"


def unlink_manager_usage_text() -> str:
    return "Format: /menejer_ochir <@username>"


def delete_task_usage_text() -> str:
    return "Format: /otask <task_id>"


def department_not_found_text(query: str, valid_names: list[str]) -> str:
    return f"⚠️ \"{query}\" degan bo'lim topilmadi. Mavjud bo'limlar: {', '.join(valid_names)}"


def manager_linked_text(full_name: str, department: str) -> str:
    return f"✅ {full_name} — {department} bo'limiga menejer sifatida biriktirildi."


def manager_unlinked_text() -> str:
    return "✅ Menejer ruxsati bekor qilindi."


def manager_not_found_text() -> str:
    return "⚠️ Bu username bo'yicha menejer topilmadi."


def not_a_manager_text() -> str:
    return "⛔ Siz menejer sifatida ulanmagansiz — CEO'dan /menejer_biriktir so'rang."


def task_not_found_text(task_id: int) -> str:
    return f"⚠️ #{task_id} vazifa Odoo'da topilmadi."


def task_not_yours_text(task_id: int) -> str:
    return f"⛔ #{task_id} vazifa sizning bo'limingizga tegishli emas — o'chira olmaysiz."


def task_not_created_by_you_text(task_id: int) -> str:
    return f"⛔ #{task_id} vazifani siz yaratmagansiz — faqat O'ZINGIZ yaratgan vazifani o'chira olasiz."


def task_deleted_text(task_id: int) -> str:
    return f"🗑 #{task_id} vazifa o'chirildi."


def manager_task_created_text(task_id: int, title: str, department: str) -> str:
    return f"✅ Odooga yozildi — {department}\n📌 {title}\n#{task_id}"
