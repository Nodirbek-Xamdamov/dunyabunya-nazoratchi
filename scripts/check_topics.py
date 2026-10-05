"""Topiklar (bo'limlar) to'g'ri Odoo loyihalariga ulanganini tekshiradi va tuzatadi.

    python scripts/check_topics.py          # faqat ko'rsatadi
    python scripts/check_topics.py --fix    # topikni O'Z NOMIDAGI Odoo loyihasiga qayta ulaydi

Odoo instansiyasi almashganda (masalan Odoo.sh test bazasi qayta qurilganda yoki prod'ga
qaytilganda) loyiha raqamlari o'zgaradi — shundan keyin shu skriptni ishga tushiring.
Loyiha nomi topik nomi bilan bir xil bo'lishi shart (Sotuv, Moliya, ...). Shu nomli loyiha
Odoo'da bo'lmasa yoki bir nechta bo'lsa — skript uni o'zgartirmaydi, faqat xabar beradi.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import update  # noqa: E402

from app.db.models import Topic  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.topic_check import find_topic_mismatches  # noqa: E402


async def main(fix: bool) -> int:
    mismatches = await find_topic_mismatches()
    if not mismatches:
        print("OK: barcha topiklar o'z nomidagi Odoo loyihasiga ulangan.")
        return 0

    unresolved = 0
    for m in mismatches:
        linked = f"'{m['odoo_project_name']}'" if m["odoo_project_name"] else "mavjud emas"
        print(f"XATO: '{m['name']}' topiki -> loyiha #{m['odoo_project_id']} ({linked})", end="")
        if m["correct_project_id"] is None:
            unresolved += 1
            print(" | Odoo'da shu nomli yagona loyiha topilmadi — qo'lda hal qiling")
            continue
        print(f" | to'g'risi: #{m['correct_project_id']}")
        if fix:
            async with SessionLocal() as session:
                await session.execute(
                    update(Topic).where(Topic.telegram_topic_id == m["topic_id"])
                    .values(odoo_project_id=m["correct_project_id"])
                )
                await session.commit()
            print(f"   tuzatildi: '{m['name']}' -> #{m['correct_project_id']}")

    if not fix:
        print("\nTuzatish uchun: python scripts/check_topics.py --fix")
        return 1
    print("\nBot topiklarni har xabarda bazadan o'qiydi — qayta ishga tushirish shart emas.")
    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--fix" in sys.argv[1:])))
