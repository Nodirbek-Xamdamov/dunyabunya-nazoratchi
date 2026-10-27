"""prototype.db dagi kutayotgan mijoz xabarlarini yangi tasnif (7 bo'lim, D-33) bilan lokal Gemma'da
tasniflaydi, 5 daqiqa tekshiruvlarini hal qiladi va natijani SONLARDA chiqaradi (matnsiz).

    cd apps\\prototype
    ..\\..\\.venv\\Scripts\\python.exe -m tools.classify
"""

import asyncio
import logging
import sqlite3
import sys
import time
from pathlib import Path

import httpx


async def main() -> None:
    from app import monitor_classifier, monitor_db
    from app.config import settings

    here = Path.cwd().resolve()
    assert Path(monitor_db.__file__).resolve().is_relative_to(here), f"app nusxadan emas: {monitor_db.__file__}"
    target = Path(settings.DB_PATH).resolve()
    assert target.is_relative_to(here) and target.exists(), f"prototype.db yo'q yoki nusxadan tashqarida: {target}"
    if settings.GROUP_AI_PROVIDER != "ollama" or not settings.OLLAMA_URL.startswith("http://127.0.0.1"):
        sys.exit("faqat lokal Ollama (127.0.0.1) ruxsat etilgan")
    try:
        httpx.get(f"{settings.OLLAMA_URL}/api/tags", timeout=5).raise_for_status()
    except httpx.HTTPError as exc:
        sys.exit(f"Ollama ishlamayapti: {exc!r} -- to'xtadim")

    started = time.monotonic()
    total = 0
    while True:
        done = await monitor_classifier.classify_pending(limit=200)
        total += done
        print(f"tasniflandi: +{done} (jami {total}, {time.monotonic() - started:.0f} s)", flush=True)
        if not done:
            break
    answered, missed = await monitor_db.resolve_checks()
    print(f"5 daqiqa tekshiruvlari: javob berilgan {answered}, javobsiz {missed}")
    await monitor_db.engine.dispose()

    db = sqlite3.connect(target)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS prototype_unplaced (message_ref INTEGER PRIMARY KEY)")
        db.executemany("INSERT OR IGNORE INTO prototype_unplaced VALUES (?)",
                       [(pk,) for pk in sorted(monitor_classifier.UNPLACED_PKS)])
        db.commit()
        q = lambda sql: db.execute(sql).fetchall()  # noqa: E731
        print("--- natija (faqat sonlar)")
        print("xabarlar holati:", dict(q("SELECT status, count(*) FROM messages GROUP BY status")))
        print("tasnif modeli:", dict(q("SELECT model, count(*) FROM classifications GROUP BY model")))
        print("bo'limlar:", q("SELECT category, count(*), sum(sentiment='Shikoyat') FROM classifications "
                              "WHERE is_company_related = 1 GROUP BY category ORDER BY count(*) DESC"))
        print("kompaniyaga oid:", q("SELECT count(*) FROM classifications WHERE is_company_related = 1")[0][0])
        print("bo'limsiz (odam ko'radi):", q("SELECT count(*) FROM prototype_unplaced")[0][0])
        print("aloqasiz (model):", q("SELECT count(*) FROM classifications c WHERE is_company_related = 0 "
                                     "AND model != 'rule' AND message_ref NOT IN (SELECT message_ref FROM prototype_unplaced)")[0][0])
        print("aloqasiz (qoida: matnsiz/stiker/emoji):", q("SELECT count(*) FROM classifications WHERE model = 'rule'")[0][0])
        print(f"vaqt: {time.monotonic() - started:.0f} s")
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
