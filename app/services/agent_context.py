"""Joriy so'rov egasi — tool'lar (vazifa berish, eslab qolish, eslatma qo'yish) kim nomidan va
qaysi chat uchun ishlayotganini shu yerdan biladi. contextvars: parallel so'rovlar
bir-biriga aralashmaydi, tool funksiyalariga esa qo'shimcha parametr uzatish shart emas.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class Actor:
    user_key: int                       # suhbat kaliti: shaxsiy chatda Telegram ID, guruhda guruh ID
    role: str                           # "ceo", "moliya", ...
    telegram_user_id: int | None = None  # yozgan odamning o'zi (guruhda ham)
    name: str = ""
    chat_id: int | None = None          # javob va eslatmalar yuboriladigan chat
    thread_id: int | None = None

    @property
    def memory_owner(self) -> str:
        """Eslab qolingan qaydlar egasi: CEO'lar uchun umumiy, menejerlar uchun — har kimniki o'ziga."""
        return "ceo" if self.role == "ceo" else f"user-{self.user_key}"


current: ContextVar[Actor | None] = ContextVar("agent_actor", default=None)


def actor() -> Actor:
    value = current.get()
    if value is None:
        raise RuntimeError("so'rov egasi aniqlanmagan (agent_context.current o'rnatilmagan)")
    return value
