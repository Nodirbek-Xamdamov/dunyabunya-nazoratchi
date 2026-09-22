from __future__ import annotations

import select as sel

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Topic(Base):
    """Har bir CEO guruhi topiki (bo'lim) va unga bog'langan Odoo project.project ID'si."""

    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_topic_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    odoo_project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Manager(Base):
    """Bo'lim menejeri. Har biri O'Z ALOHIDA Odoo hisobiga (login+API key) ega — bot ular
    nomidan harakat qilganda shu hisobni ishlatadi, umumiy admin hisobini emas (2026-08-26,
    CEO talabi — Odoo'ning o'z jurnalida ham kim nima qilgani ko'rinishi uchun).

    CEO/admin oldindan /menejer_biriktir buyrug'i orqali Telegram USERNAME bo'yicha ro'yxatga
    oladi (`telegram_user_id` shu bosqichda hali noma'lum, NULL turadi). Menejer botga birinchi
    marta yozganda, `telegram_username` bo'yicha moslik topilib `telegram_user_id` avtomatik
    to'ldiriladi (`repo.activate_manager_by_username`) — shundan keyingina u faol hisoblanadi."""

    __tablename__ = "managers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_username: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    department: Mapped[str] = mapped_column(String, nullable=False)
    odoo_project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    odoo_login: Mapped[str] = mapped_column(String, nullable=False)
    odoo_api_key: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ConversationMessage(Base):
    """CEO assistant (`app/services/assistant.py`) uchun suhbat xotirasi — faqat YAKUNIY
    savol/javob juftliklari saqlanadi (oraliq tool-chaqiruvlar emas), shunda "shu ham
    ko'rsat" kabi keyingi savollar oldingi kontekstni tushunadi."""

    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())




class GroupMessage(Base):
    """Guruh xabarlari — 1 kunlik tarixni saqlash, AI kontekst uchun."""

    __tablename__ = "group_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    from_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    from_user_name: Mapped[str] = mapped_column(String, nullable=True)
    text: Mapped[str] = mapped_column(String, nullable=False)
    message_type: Mapped[str] = mapped_column(String, default="text")  # text, voice, task, etc
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class TaskLog(Base):
    """Yaratilgan har bir Odoo task haqida audit yozuvi (kim, qachon, qaysi bo'lim)."""

    __tablename__ = "task_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    odoo_task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    odoo_project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    telegram_topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    # Vazifani KIM yozdirgani (topikda asl xabarni yozgan foydalanuvchi) — menejer
    # faqat O'ZI yaratgan vazifani /otask bilan o'chira olishi uchun kerak. Eski
    # (bu ustun qo'shilishidan oldingi) yozuvlarda NULL — bunday holatda egalik
    # tasdiqlanmagan deb hisoblanadi (repo.py/handlers_manager.py'dagi tekshiruvga qarang).
    created_by_telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
