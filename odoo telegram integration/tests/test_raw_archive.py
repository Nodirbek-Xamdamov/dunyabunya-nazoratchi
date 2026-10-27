"""app.raw_archive -- guruh xabarlarining xom arxivi: yil/oy/guruh/kun fayllari, tahrir asl kunga,
guruh papkasi nomi o'zgarmaydi, fayllar yonidagi papkaga yuklanadi. Haqiqiy Telegram chaqirilmaydi."""

import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetFile
from aiogram.types import Message

from app import raw_archive
from app.bot.handlers import group_monitor

GROUP_ID = -1004338052437
# 2026-10-25 23:30 Toshkent vaqti (UTC+5) -- UTC bo'yicha hali 25-sana, mahalliy bo'yicha ham 25
LATE_EVENING = int(datetime(2026, 10, 25, 18, 30, tzinfo=timezone.utc).timestamp())
# 2026-10-26 00:30 Toshkent -- UTC bo'yicha 25-sana 19:30, lekin kun mahalliy vaqt bo'yicha 26
AFTER_MIDNIGHT = int(datetime(2026, 10, 25, 19, 30, tzinfo=timezone.utc).timestamp())


@pytest.fixture
def archive(monkeypatch, tmp_path):
    monkeypatch.setattr(raw_archive.settings, "RAW_TELEGRAM_DIR", str(tmp_path))
    monkeypatch.setattr(raw_archive.settings, "RAW_TELEGRAM_FILES", True)
    monkeypatch.setattr(raw_archive, "_index", None)
    monkeypatch.setattr(raw_archive, "_queue", None)
    monkeypatch.setattr(raw_archive, "_known", set())
    return tmp_path


def _message(message_id=5, *, date=LATE_EVENING, title="Oqtosh zakaz dunyabunya", chat_id=GROUP_ID, **extra):
    data = {
        "message_id": message_id, "date": date,
        "chat": {"id": chat_id, "type": "supergroup", "title": title},
        "from": {"id": 7, "is_bot": False, "first_name": "Vali", "last_name": "Mijoz", "username": "vali"},
    }
    if not extra:  # matn, fayl yoki xizmat maydoni berilmasa -- oddiy matnli xabar
        data["text"] = "Sement qanchadan?"
    return Message.model_validate(data | extra)


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_message_goes_to_year_month_group_day_file(archive):
    path = raw_archive.record(_message())

    assert path == archive / "2026" / "10-Oktabr" / "Oqtosh zakaz dunyabunya" / "2026-10-25.jsonl"
    (line,) = _lines(path)
    assert line["event"] == "message"
    assert line["date"] == "2026-10-25T23:30:00+05:00"
    assert (line["chat_id"], line["message_id"], line["text"]) == (GROUP_ID, 5, "Sement qanchadan?")
    assert (line["sender_id"], line["sender_name"], line["sender_username"]) == (7, "Vali Mijoz", "vali")
    assert line["type"] == "text"
    # Telegram'ning to'liq xabari asl maydon nomlari bilan ("from", "from_user" emas)
    assert line["telegram"]["from"]["id"] == 7 and "from_user" not in line["telegram"]


def test_day_is_the_local_day_not_the_utc_day(archive):
    path = raw_archive.record(_message(date=AFTER_MIDNIGHT))
    assert path.name == "2026-10-26.jsonl"


def test_edit_is_appended_to_the_original_messages_day(archive):
    raw_archive.record(_message(text="Sement qancha"))
    edited = _message(text="Sement qanchadan?", edit_date=AFTER_MIDNIGHT + 3600)

    path = raw_archive.record(edited, edited=True)

    assert path.name == "2026-10-25.jsonl"
    first, second = _lines(path)
    assert (first["event"], second["event"]) == ("message", "edit")
    assert second["text"] == "Sement qanchadan?" and second["edit_date"] == "2026-10-26T01:30:00+05:00"


def test_service_messages_and_bots_are_kept_too(archive):
    joined = _message(6, new_chat_members=[{"id": 9, "is_bot": False, "first_name": "Yangi"}])
    bot_post = _message(7, text="Avto xabar")
    bot_post = bot_post.model_copy(update={"from_user": bot_post.from_user.model_copy(update={"is_bot": True})})

    raw_archive.record(joined)
    path = raw_archive.record(bot_post)

    service, from_bot = _lines(path)
    assert service["type"] == "new_chat_members"
    assert from_bot["sender_is_bot"] is True


def test_group_folder_keeps_its_first_name_and_names_do_not_clash(archive):
    first = raw_archive.record(_message(1))
    renamed = raw_archive.record(_message(2, title="Oqtosh: yangi nom"))
    other = raw_archive.record(_message(3, chat_id=-1001, title="oqtosh zakaz dunyabunya"))
    odd = raw_archive.record(_message(4, chat_id=-1002, title='Zakaz <1>/"2"?. '))

    assert renamed.parent == first.parent
    assert other.parent.name == "oqtosh zakaz dunyabunya (1001)"
    assert odd.parent.name == "Zakaz 1 2"
    index = json.loads((archive / "guruhlar.json").read_text(encoding="utf-8"))
    assert index[str(GROUP_ID)] == {"folder": "Oqtosh zakaz dunyabunya", "title": "Oqtosh: yangi nom",
                                    "first_day": "2026-10-25"}


def _bot(file_path="photos/file_7.jpg", content=b"JPEG", error=None):
    async def download_file(path, destination, timeout=30):
        if error is not None:
            Path(destination).write_bytes(b"half")
            raise error
        Path(destination).write_bytes(content)

    return SimpleNamespace(
        get_file=AsyncMock(return_value=SimpleNamespace(file_path=file_path, file_size=len(content))),
        download_file=AsyncMock(side_effect=download_file),
    )


PHOTO = [{"file_id": "small", "file_unique_id": "s1", "width": 90, "height": 90},
         {"file_id": "big", "file_unique_id": "b1", "width": 1280, "height": 960, "file_size": 4}]


async def test_photo_is_saved_next_to_the_day_file_with_a_file_record(archive):
    path = raw_archive.record(_message(12, photo=PHOTO, caption="shundan 20 ta"))
    (job,) = raw_archive.unfinished_jobs(path)
    assert (job.file_id, job.name) == ("big", "23-30-00_12")
    bot = _bot()

    assert await raw_archive.handle(bot, job, 0)

    saved = archive / "2026" / "10-Oktabr" / "Oqtosh zakaz dunyabunya" / "2026-10-25 fayllar" / "23-30-00_12.jpg"
    assert saved.read_bytes() == b"JPEG"
    message, file_line = _lines(path)
    assert message["text"] == "shundan 20 ta" and message["file_type"] == "photo"
    assert (file_line["event"], file_line["status"], file_line["size"]) == ("file", "saved", 4)
    assert file_line["path"] == "2026-10-25 fayllar/23-30-00_12.jpg"
    assert raw_archive.unfinished_jobs(path) == []
    bot.get_file.assert_awaited_once_with("big")


async def test_document_keeps_its_name_and_voice_becomes_ogg(archive):
    doc = {"file_id": "d", "file_unique_id": "d1", "file_name": "hisob: oktabr.pdf", "mime_type": "application/pdf"}
    voice = {"file_id": "v", "file_unique_id": "v1", "duration": 4, "mime_type": "audio/ogg"}
    path = raw_archive.record(_message(20, document=doc))
    raw_archive.record(_message(21, voice=voice))
    doc_job, voice_job = raw_archive.unfinished_jobs(path)

    await raw_archive.handle(_bot("documents/file_1.pdf"), doc_job, 0)
    await raw_archive.handle(_bot("voice/file_2.oga"), voice_job, 0)

    names = sorted(item.name for item in raw_archive.files_dir(path).iterdir())
    assert names == ["23-30-00_20_hisob oktabr.pdf", "23-30-00_21.ogg"]


async def test_too_big_file_is_recorded_and_not_retried(archive):
    path = raw_archive.record(_message(30, video={"file_id": "x", "file_unique_id": "x1", "width": 1, "height": 1,
                                                  "duration": 600}))
    (job,) = raw_archive.unfinished_jobs(path)
    bot = _bot()
    bot.get_file = AsyncMock(side_effect=TelegramBadRequest(GetFile(file_id="x"), "file is too big"))

    assert await raw_archive.handle(bot, job, 0)

    assert _lines(path)[-1]["status"] == "too_big"
    assert raw_archive.unfinished_jobs(path) == []


async def test_network_error_is_retried_then_marked_failed(archive):
    path = raw_archive.record(_message(40, photo=PHOTO))
    (job,) = raw_archive.unfinished_jobs(path)
    bot = _bot(error=TimeoutError())

    assert not await raw_archive.handle(bot, job, 0)
    assert len(_lines(path)) == 1 and not list(raw_archive.files_dir(path).glob("*"))  # chala fayl qolmaydi
    assert await raw_archive.handle(bot, job, 2)
    assert _lines(path)[-1]["status"] == "failed"


def test_unfinished_jobs_ignore_a_half_written_last_line(archive):
    path = raw_archive.record(_message(50, photo=PHOTO))
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"event": "message", "message_id": 5')

    assert [job.message_id for job in raw_archive.unfinished_jobs(path)] == [50]


def test_recent_day_files_finds_days_across_months(archive):
    raw_archive.record(_message(1, date=int(datetime(2026, 10, 31, 6, tzinfo=timezone.utc).timestamp())))
    raw_archive.record(_message(2, date=int(datetime(2026, 11, 1, 6, tzinfo=timezone.utc).timestamp())))

    found = raw_archive.recent_day_files(3, today=date(2026, 11, 2))

    assert [path.name for path in found] == ["2026-10-31.jsonl", "2026-11-01.jsonl"]
    assert found[1].parent.parent.name == "11-Noyabr"


def test_queued_file_is_not_queued_twice_when_the_caption_is_edited(archive, monkeypatch):
    queue = SimpleNamespace(items=[], put_nowait=lambda item: queue.items.append(item))
    monkeypatch.setattr(raw_archive, "_queue", queue)

    raw_archive.record(_message(60, photo=PHOTO, caption="bir"))
    raw_archive.record(_message(60, photo=PHOTO, caption="ikki", edit_date=LATE_EVENING + 60), edited=True)

    assert [job.message_id for job, _attempt in queue.items] == [60]


def test_handler_skips_excluded_groups_and_survives_archive_errors(archive, monkeypatch):
    monkeypatch.setattr(group_monitor.settings, "GROUP_MONITOR_EXCLUDED_GROUP_IDS", str(GROUP_ID))
    group_monitor.archive(_message(1))
    assert not list(archive.rglob("*.jsonl"))

    monkeypatch.setattr(group_monitor.settings, "GROUP_MONITOR_EXCLUDED_GROUP_IDS", "")
    monkeypatch.setattr(raw_archive, "record", lambda *_a, **_k: (_ for _ in ()).throw(OSError("E: yo'q")))
    group_monitor.archive(_message(2))  # xato ichida qoladi, monitoring davom etadi


def test_archive_is_off_without_a_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(raw_archive.settings, "RAW_TELEGRAM_DIR", "")
    group_monitor.archive(_message(1))
    assert not raw_archive.enabled()
