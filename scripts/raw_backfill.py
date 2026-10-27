"""
Xom arxivni (`RAW_TELEGRAM_DIR`, `app/raw_archive.py`) monitoring bazasidagi ESKI xabarlar bilan
to'ldirish: arxiv 2026-10-26 da paydo bo'ldi, undan oldingi xabarlar faqat `data/monitor.db`da.
Loyiha papkasidan, BOT TO'XTATILGAN holda (ikkalasi bir faylga yozmasin):

    .venv\\Scripts\\python.exe scripts\\raw_backfill.py --dry-run   # nima yozilishini sanaydi
    .venv\\Scripts\\python.exe scripts\\raw_backfill.py             # yozadi va fayllarni yuklaydi

Baza faqat o'qiladi. Kun faylida bor xabar (chat_id + message_id) qayta yozilmaydi, shuning
uchun qayta ishga tushirish xavfsiz. Bu yozuvlarda "source": "monitor.db" -- Telegram'ning to'liq
xabari ("telegram") yo'q, chunki bazada faqat asosiy maydonlar saqlangan. Fayllar (rasm, ovoz,
hujjat) Telegram'dan file_id bo'yicha qayta yuklab olinadi.
"""

import argparse
import asyncio
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from aiogram import Bot  # noqa: E402

from app import raw_archive  # noqa: E402
from app.config import settings  # noqa: E402


def _utc(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc) if text else None


def _iso(moment: datetime | None) -> str | None:
    return raw_archive.to_local(moment).isoformat(timespec="seconds") if moment else None


def read_messages(db_path: str) -> list[dict]:
    connection = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(
            "SELECT m.*, g.title AS chat_title FROM messages m LEFT JOIN groups g ON g.chat_id = m.chat_id "
            "ORDER BY m.sent_at_utc, m.id"
        )]
    finally:
        connection.close()


def record_of(row: dict, saved_at: str) -> dict:
    record = {
        "event": raw_archive.EVENT_MESSAGE,
        "source": "monitor.db",
        "saved_at": saved_at,
        "chat_id": row["chat_id"],
        "chat_title": row["chat_title"],
        "message_id": row["message_id"],
        "date": _iso(_utc(row["sent_at_utc"])),
        "edit_date": _iso(_utc(row["edited_at"])),
        "sender_id": row["sender_id"],
        "sender_name": row["sender_name"],
        "sender_username": row["sender_username"],
        "sender_is_bot": False,
        "type": row["media_type"],
        "text": row["text"],
        "reply_to": row["reply_to_message_id"],
        "thread_id": None,
    }
    if row["file_id"]:
        record.update(file_type=row["media_type"], file_id=row["file_id"], file_unique_id=None, file_name=None,
                      file_size=None, mime_type=None, duration=row["media_duration"])
    return record


def existing_ids(day_path: Path) -> set[int]:
    if not day_path.exists():
        return set()
    found = set()
    for line in day_path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get("event") == raw_archive.EVENT_MESSAGE:
            found.add(item["message_id"])
    return found


async def download_all(paths: list[Path]) -> Counter:
    results: Counter = Counter()
    bot = Bot(token=settings.BOT_TOKEN)
    try:
        for path in paths:
            for job in raw_archive.unfinished_jobs(path):
                for attempt in range(3):
                    if await raw_archive.handle(bot, job, attempt):
                        break
                    await asyncio.sleep(5)
                results[path.parent.name] += 1
    finally:
        await bot.session.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Eski xabarlarni xom arxivga ko'chirish")
    parser.add_argument("--dry-run", action="store_true", help="faqat sanaydi, hech narsa yozmaydi")
    parser.add_argument("--no-files", action="store_true", help="fayllarni yuklab olmaydi")
    args = parser.parse_args()
    if not raw_archive.enabled():
        sys.exit("RAW_TELEGRAM_DIR .env da yozilmagan")

    saved_at = raw_archive.to_local(datetime.now(timezone.utc)).isoformat(timespec="seconds")
    written: Counter = Counter()
    skipped = 0
    touched: dict[Path, set[int]] = {}
    for row in read_messages(settings.DB_PATH):
        record = record_of(row, saved_at)
        day = datetime.fromisoformat(record["date"]).date()
        if args.dry_run:
            written[(row["chat_title"], day.isoformat())] += 1
            continue
        path = raw_archive.day_file(row["chat_id"], row["chat_title"], day)
        if path not in touched:
            touched[path] = existing_ids(path)
        if row["message_id"] in touched[path]:
            skipped += 1
            continue
        raw_archive.append(path, record)
        touched[path].add(row["message_id"])
        written[(row["chat_title"], day.isoformat())] += 1

    for (title, day), count in sorted(written.items(), key=lambda item: (item[0][1], item[0][0] or "")):
        print(f"{day}  {title}: {count}")
    print(f"Jami: {sum(written.values())} ta xabar {'yoziladi' if args.dry_run else 'yozildi'}, "
          f"{skipped} tasi arxivda bor edi")
    if args.dry_run or args.no_files:
        return
    files = asyncio.run(download_all(sorted(touched)))
    print(f"Fayllar: {sum(files.values())} ta ishlandi")


if __name__ == "__main__":
    main()
