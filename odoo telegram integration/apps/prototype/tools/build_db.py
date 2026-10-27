"""prototype.db ni eski bazaning eng oxirgi faqat-o'qish nusxasidan yaratadi (2026-10-27).

Manba: D:\\genesis_old_data\\sqlite_copy\\ ichidagi eng yangi .db (faqat `mode=ro` bilan ochiladi).
Ko'chadi: groups, messages (mijoz xabarlari "pending", xodimlarniki "staff" bo'lib, urinishlar 0),
media_files. Ko'chmaydi: eski tasnif (classifications), 5 daqiqa tekshiruvlari, kunlik statistika
va xulosalar -- ular yangi tasnifdan qayta hisoblanadi. prototype.db allaqachon bo'lsa to'xtaydi
(hech narsa o'chirilmaydi).

    cd apps\\prototype
    ..\\..\\.venv\\Scripts\\python.exe -m tools.build_db
"""

import asyncio
import sqlite3
import sys
from pathlib import Path

SOURCE_DIR = Path(r"D:\genesis_old_data\sqlite_copy")
COPY_TABLES = ("groups", "messages", "media_files")


def latest_source() -> Path:
    files = sorted(SOURCE_DIR.glob("*.db"), key=lambda p: p.stat().st_mtime)
    if not files:
        sys.exit(f"{SOURCE_DIR} da .db nusxa yo'q")
    return files[-1]


async def main() -> None:
    from app import monitor_db
    from app.config import settings

    here = Path.cwd().resolve()
    assert Path(monitor_db.__file__).resolve().is_relative_to(here), f"app nusxadan emas: {monitor_db.__file__}"
    target = Path(settings.DB_PATH).resolve()
    assert target.is_relative_to(here), f"DB_PATH nusxa papkasidan tashqarida: {target}"
    if target.exists():
        sys.exit(f"{target} allaqachon bor -- qayta yaratilmaydi")

    source = latest_source()
    print(f"manba: {source.name}")
    await monitor_db.init_db()
    await monitor_db.engine.dispose()

    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        for table in COPY_TABLES:
            src_cols = [row[1] for row in src.execute(f"PRAGMA table_info({table})")]
            dst_cols = {row[1] for row in dst.execute(f"PRAGMA table_info({table})")}
            cols = [c for c in src_cols if c in dst_cols]
            rows = src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
            marks = ", ".join("?" * len(cols))
            dst.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks})", rows)
            print(f"{table}: {len(rows)} qator")
        dst.execute("UPDATE messages SET status = CASE WHEN is_staff THEN 'staff' ELSE 'pending' END, attempts = 0")
        dst.commit()
        for status, count in dst.execute("SELECT status, count(*) FROM messages GROUP BY status"):
            print(f"messages.status {status}: {count}")
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    asyncio.run(main())
