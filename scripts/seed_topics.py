"""Bir martalik skript: dunyabunya_agent'da allaqachon aniqlangan 8 bo'lim/topik/Odoo-loyiha
bog'lanishini shu loyihaning o'z bazasiga ko'chiradi (bir xil CEO guruhi, bir xil Telegram
topiklar, bir xil test-Odoo instansi ishlatilgani uchun qiymatlar bir xil).

Ishlatish: `docker compose exec -T app python3 scripts/seed_topics.py`
"""

import asyncio

from app.db.models import Topic
from app.db.session import SessionLocal, init_db

# (telegram_topic_id, name, odoo_project_id) — dunyabunya_agent'ning "topics" jadvalidan,
# 2026-08-26'da hasanxojayev-odoo-prod-35427738'da qayta yaratilgan loyihalarga mos.
SEED_TOPICS = [
    (10, "Marketing", 40),
    (3, "Moliya", 33),
    (2, "Sotuv", 34),
    (4, "Logistika", 35),
    (5, "Taminot", 36),
    (6, "Ombor", 37),
    (7, "CRM", 38),
    (9, "HR", 39),
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
