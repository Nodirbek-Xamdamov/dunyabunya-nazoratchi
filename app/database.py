"""
Async SQLAlchemy v2 database konfiguratsiyasi.
PostgreSQL (asyncpg) yoki SQLite (aiosqlite) bilan ishlaydi -- DATABASE_URL orqali tanlanadi.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


# Postgres uchun pool o'lchamini aniq belgilaymiz (2026-08-06): 3 ta uvicorn worker
# (fastapi) + bot + userbot -- har biri o'z alohida engine/pool'iga ega, SQLAlchemy'ning
# standart qiymati (pool_size=5, max_overflow=10 = 15/protsess) 5 protsessda 75 tagacha
# ulanish ochishi mumkin edi (max_connections=100'ga juda yaqin). pool_pre_ping=True
# uzoq turgan/uzilgan ulanishlarni avtomatik yangilaydi (DB qayta ishga tushganda xato
# bermasligi uchun). SQLite (testlarda ishlatiladi) bu argumentlarni qo'llab-quvvatlamaydi,
# shuning uchun faqat Postgres uchun qo'llanadi.
_engine_kwargs = {"echo": False, "future": True}
if settings.DATABASE_URL.startswith("postgresql"):
    _engine_kwargs.update(pool_size=5, max_overflow=5, pool_pre_ping=True)

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """Jadvallarni (agar mavjud bo'lmasa) yaratadi."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Baza muvaffaqiyatli initsializatsiya qilindi.")
    except Exception:
        logger.exception("Bazani initsializatsiya qilishda xatolik yuz berdi")
        raise


async def get_session():
    """FastAPI dependency sifatida ishlatiladigan DB session generator."""
    async with async_session_maker() as session:
        yield session
