"""app.monitor_media -- kompaniyaga oid suhbatdagi rasm/GIF'larni saqlash: suhbat qoidasi, kutish,
kunlik limit, hajm chegarasi, tarmoq xatosida qayta urinish. Haqiqiy Telegram chaqirilmaydi."""

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest

from app import monitor_db, monitor_media
from app.monitor_db import MediaFile
from tests.monitor_helpers import ADMIN_ID, CHAT, OTHER_CHAT, T0, add_group, add_message, all_rows, classify

# Rasmdan 10 daqiqa (MEDIA_CONTEXT_MINUTES) o'tgandan keyingi payt
LATER = T0 + timedelta(hours=1)


@pytest.fixture
def media(monkeypatch, tmp_path):
    monkeypatch.setattr(monitor_media.settings, "MEDIA_DIR", str(tmp_path))
    monkeypatch.setattr(monitor_media.settings, "MEDIA_DAILY_LIMIT", 200)
    monkeypatch.setattr(monitor_media.settings, "MEDIA_MAX_FILE_MB", 10)
    monkeypatch.setattr(monitor_media.settings, "MEDIA_CONTEXT_MINUTES", 10)
    monitor_media._failures.clear()
    return tmp_path


def _bot(size=1500, file_path="photos/file_7.jpg", download_error=None):
    async def download_file(path, destination, timeout=30):
        assert timeout == monitor_media.DOWNLOAD_TIMEOUT
        if download_error is not None:
            Path(destination).write_bytes(b"half")  # uzilgan yuklab olish chala fayl qoldiradi
            raise download_error
        Path(destination).write_bytes(b"J" * size)

    return SimpleNamespace(
        get_file=AsyncMock(return_value=SimpleNamespace(file_path=file_path, file_size=size)),
        download_file=AsyncMock(side_effect=download_file),
    )


async def _photo(message_id=10, *, caption=None, minutes=0.0, media_type="photo", **kwargs) -> int:
    return await add_message(message_id, caption, media_type=media_type, minutes=minutes,
                             file_id=f"file-{message_id}", **kwargs)


async def _decisions() -> dict[int, MediaFile]:
    return {row.message_ref: row for row in await all_rows(MediaFile)}


async def test_photo_next_to_a_company_request_is_saved_for_the_web_page(media):
    await add_group()
    photo = await _photo(10)
    await classify(photo, related=False)  # izohsiz rasm -- qoida bo'yicha "aloqasiz"
    await classify(await add_message(11, "Shu tovardan 20 ta kerak", minutes=3))
    bot = _bot()

    assert await monitor_media.process_media(bot, LATER) == 1

    saved = (await _decisions())[photo]
    assert (saved.status, saved.path, saved.size) == ("saved", "2026-10/1001000000001_10.jpg", 1500)
    assert (media / saved.path).read_bytes() == b"J" * 1500
    assert not list(media.rglob("*.part"))
    bot.get_file.assert_awaited_once_with("file-10")


async def test_gif_and_photo_in_a_chat_without_company_talk_are_skipped(media):
    await add_group()
    gif = await _photo(10, media_type="animation")
    await classify(gif, related=False)
    await classify(await add_message(11, "Hammaga salom!", minutes=1), related=False)
    photo = await _photo(12, minutes=30)  # atrofida hech narsa yo'q
    await classify(photo, related=False)
    bot = _bot()

    assert await monitor_media.process_media(bot, LATER) == 2

    assert {row.status for row in (await _decisions()).values()} == {"skipped"}
    bot.get_file.assert_not_called()
    assert not list(media.rglob("*.*"))


async def test_caption_reply_links_and_staff_photos_count(media):
    await add_group()
    own = await _photo(10, caption="Mana shu kafel singan keldi")  # o'z izohi kompaniyaga oid
    await classify(own, "Mahsulot Sifati va Nuqsonlar", "Shikoyat")
    request = await add_message(20, "Hisob-faktura yuboring", minutes=60)
    await classify(request)
    invoice = await _photo(21, minutes=150, sender_id=ADMIN_ID, name="Ali", is_staff=True, reply_to=20)  # javob
    asked = await _photo(30, minutes=300)
    await classify(asked, related=False)
    await classify(await add_message(31, "Bu nima?", minutes=400, reply_to=30))  # rasmga javob
    bot = _bot()

    assert await monitor_media.process_media(bot, T0 + timedelta(hours=8)) == 3
    assert {ref: row.status for ref, row in (await _decisions()).items()} == {
        own: "saved", invoice: "saved", asked: "saved",
    }


async def test_waits_for_the_conversation_to_settle_and_be_classified(media):
    await add_group()
    photo = await _photo(10)
    await classify(photo, related=False)
    follow_up = await add_message(11, "Shundan 20 ta", minutes=4)  # hali tasniflanmagan
    bot = _bot()

    assert await monitor_media.process_media(bot, T0 + timedelta(minutes=5)) == 0  # 10 daqiqa o'tmagan
    assert await monitor_media.process_media(bot, LATER) == 0  # mijoz xabari tasnif kutyapti
    await classify(follow_up)
    assert await monitor_media.process_media(bot, LATER) == 1
    assert (await _decisions())[photo].status == "saved"
    assert await monitor_media.process_media(bot, LATER) == 0  # qaror bir marta


async def test_daily_limit_and_size_limit(media, monkeypatch):
    monkeypatch.setattr(monitor_media.settings, "MEDIA_DAILY_LIMIT", 2)
    monkeypatch.setattr(monitor_media.settings, "MEDIA_MAX_FILE_MB", 1)
    await add_group()
    await add_group(OTHER_CHAT, "Boshqa guruh")
    photos = [await _photo(10 + index, minutes=index) for index in range(3)]
    other = await _photo(20, chat_id=OTHER_CHAT, minutes=5)
    for photo in (*photos, other):
        await classify(photo, related=False)
    await classify(await add_message(30, "Narxi qancha?", minutes=2))
    await classify(await add_message(31, "Narxi qancha?", chat_id=OTHER_CHAT, minutes=5))

    assert await monitor_media.process_media(_bot(), LATER) == 4
    decisions = await _decisions()
    assert [decisions[photo].status for photo in photos] == ["saved", "saved", "limit"]
    assert decisions[other].status == "limit"  # limit -- bir kunga, hamma guruhlar uchun

    await monitor_db.save_message(
        chat_id=CHAT, message_id=40, sender_id=200, sender_username="vali", sender_name="Vali Mijoz", is_staff=False,
        text="Mana video-gif", media_type="animation", file_id="file-40", reply_to_message_id=None,
        sent_at=T0 + timedelta(days=1),
    )
    big = (await all_rows(monitor_db.Message))[-1]
    await classify(big.id)
    assert await monitor_media.process_media(_bot(size=2 * 1024 * 1024, file_path="animations/file_1.mp4"),
                                             LATER + timedelta(days=1)) == 1
    assert ((await _decisions())[big.id].status, (await _decisions())[big.id].size) == ("too_big", 2 * 1024 * 1024)


async def test_network_error_is_retried_and_refused_file_is_not(media):
    await add_group()
    photo = await _photo(10, caption="Shu rangdan bormi?")
    await classify(photo)
    flaky = _bot(download_error=TimeoutError())

    assert await monitor_media.process_media(flaky, LATER) == 0  # qaror yozilmadi -- keyin qayta
    assert await _decisions() == {} and not list(media.rglob("*.part"))  # chala fayl qolmaydi
    assert await monitor_media.process_media(_bot(), LATER) == 1
    assert (await _decisions())[photo].status == "saved"

    second = await _photo(11, caption="Bu-chi?", minutes=1)
    await classify(second)
    refused = _bot()
    refused.get_file = AsyncMock(side_effect=TelegramBadRequest(method=MagicMock(), message="file is too big"))
    assert await monitor_media.process_media(refused, LATER) == 1
    assert (await _decisions())[second].status == "failed"


async def test_gives_up_after_repeated_network_errors(media):
    await add_group()
    photo = await _photo(10, caption="Shu rangdan bormi?")
    await classify(photo)
    flaky = _bot(download_error=ConnectionError("reset"))

    assert [await monitor_media.process_media(flaky, LATER) for _ in range(3)] == [0, 0, 1]
    assert (await _decisions())[photo].status == "failed"
