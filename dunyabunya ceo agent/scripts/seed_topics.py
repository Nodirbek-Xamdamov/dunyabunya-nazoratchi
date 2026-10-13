"""Bir martalik skript: dunyabunya_agent'da allaqachon aniqlangan 8 bo'lim/topik/Odoo-loyiha
bog'lanishini shu loyihaning o'z bazasiga ko'chiradi (bir xil CEO guruhi, bir xil Telegram
topiklar) — Odoo instansiyasi almashganda (masalan obuna/hosting o'zgarganda) department
loyihalari YANGIDAN yaratilishi va bu ro'yxat shu yangi ID'lar bilan yangilanishi shart,
aks holda bot eski (endi mavjud bo'lmagan) project_id'larga task yozishga urinadi.

Ishlatish: `docker compose exec -T app python3 scripts/seed_topics.py` (yangi, bo'sh bazada)
YOKI mavjud "topics" qatorlarini UPDATE qilish uchun to'g'ridan-to'g'ri SQL (qayta ishga
tushirishda INSERT unique constraint'ga urilib ketadi — 2026-09-22'dagi migratsiyada shunday
qilingan, pastdagi SEED_TOPICS shunchaki HUJJAT sifatida saqlanadi).

Odoo almashgandan keyin moslikni tekshirish va tuzatish uchun: `python scripts/check_topics.py`
(`--fix` bilan — topiklarni o'z nomidagi Odoo loyihasiga qayta ulaydi). Bot ham har ishga
tushganda shu moslikni tekshirib, xato bo'lsa logga ERROR yozadi.

Tarix: 2026-08-26 — hasanxojayev-odoo-prod-35427738 (ID'lar 33-40).
       2026-09-22 — CEO PostgreSQL'ni qayta o'rnatib (v16→v17), Odoo'ni ham yangi
       instansiyaga (jonibekodoo-dunyabunya-prod-prod-26530779) ko'chirdi. Eski baza
       backup'dan tiklandi (eski Odoo project_id'lar bilan), keyin 8 ta loyiha YANGI
       Odoo'da qaytadan yaratildi va "topics" jadvali shu yangi ID'larga UPDATE qilindi.
"""

import asyncio

from app.db.models import Topic
from app.db.session import SessionLocal, init_db

# (telegram_topic_id, name, odoo_project_id) — joriy holat (2026-09-22,
# jonibekodoo-dunyabunya-prod-prod-26530779'da qayta yaratilgan loyihalarga mos).
# General (telegram_topic_id=1) uchun alohida Topic qatori yo'q — General'da
# hech qachon Odoo'ga task yozilmaydi (handlers.py::on_general_message), shuning
# uchun uning odoo_project_id'si ahamiyatsiz.
SEED_TOPICS = [
    (10, "Marketing", 34),
    (3, "Moliya", 27),
    (2, "Sotuv", 28),
    (4, "Logistika", 29),
    (5, "Taminot", 30),
    (6, "Ombor", 31),
    (7, "CRM", 32),
    (9, "HR", 33),
]


async def main() -> None:
    await init_db()
    async with SessionLocal() as session:
        for telegram_topic_id, name, odoo_project_id in SEED_TOPICS:
            session.add(Topic(telegram_topic_id=telegram_topic_id, name=name, odoo_project_id=odoo_project_id))
        await session.commit()
    print(f"Seeded {len(SEED_TOPICS)} topics.")


if __name__ == "__main__":
    asyncio.run(main())
