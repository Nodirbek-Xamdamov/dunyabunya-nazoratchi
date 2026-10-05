"""Topiklar (bo'limlar) to'g'ri Odoo loyihalariga ulanganini tekshiradi va tuzatadi.

    python scripts/check_topics.py                  # faqat ko'rsatadi
    python scripts/check_topics.py --fix            # topikni O'Z NOMIDAGI Odoo loyihasiga qayta ulaydi
    python scripts/check_topics.py --fix --create   # shu nomli loyiha Odoo'da yo'q bo'lsa — yaratadi ham

Odoo instansiyasi almashganda (masalan Odoo.sh test bazasi qayta qurilganda yoki asosiy
(prod) bazaga o'tilganda) loyiha raqamlari o'zgaradi — .env ni yangilagach shu skriptni
ishga tushiring. Loyiha nomi topik nomi bilan bir xil bo'lishi shart (Sotuv, Moliya, ...).
Shu nomli loyihalar bir nechta bo'lsa — skript taxmin qilmaydi, faqat xabar beradi.
`--create` Odoo'ga YOZADI (yangi loyiha) — faqat yangi bazaga o'tayotganda ishlating.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import update  # noqa: E402

from app.db.models import Topic  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.topic_check import create_project, find_topic_mismatches  # noqa: E402


async def _remap(topic_id: int, project_id: int) -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(Topic).where(Topic.telegram_topic_id == topic_id).values(odoo_project_id=project_id)
        )
        await session.commit()


async def main(fix: bool, create: bool) -> int:
    mismatches = await find_topic_mismatches()
    if not mismatches:
        print("OK: barcha topiklar o'z nomidagi Odoo loyihasiga ulangan.")
        return 0

    unresolved = 0
    for m in mismatches:
        linked = f"'{m['odoo_project_name']}'" if m["odoo_project_name"] else "mavjud emas"
        print(f"XATO: '{m['name']}' topiki -> loyiha #{m['odoo_project_id']} ({linked})", end="")
        target = m["correct_project_id"]
        if target is None and m["candidates"] == 0 and fix and create:
            target = await create_project(m["name"])
            print(f" | Odoo'da '{m['name']}' loyihasi yaratildi: #{target}", end="")
        if target is None:
            unresolved += 1
            reason = ("Odoo'da shu nomli loyiha yo'q (--fix --create bilan yaratiladi)" if m["candidates"] == 0
                      else f"Odoo'da shu nomli {m['candidates']} ta loyiha bor — qo'lda hal qiling")
            print(f" | {reason}")
            continue
        print(f" | to'g'risi: #{target}")
        if fix:
            await _remap(m["topic_id"], target)
            print(f"   tuzatildi: '{m['name']}' -> #{target}")

    if not fix:
        print("\nTuzatish uchun: python scripts/check_topics.py --fix")
        return 1
    print("\nBot topiklarni har xabarda bazadan o'qiydi — qayta ishga tushirish shart emas.")
    return 1 if unresolved else 0


if __name__ == "__main__":
    args = set(sys.argv[1:])
    sys.exit(asyncio.run(main("--fix" in args, "--create" in args)))
