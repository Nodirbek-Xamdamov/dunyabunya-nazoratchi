"""
app.userbot ichidagi sof formatlash funksiyalarini sinovdan o'tkazadi. Modul import
qilinganda Telethon `TelegramClient(...)` obyekti yaratiladi -- bu ishlashi uchun
faol asyncio event loop talab qiladi, shu sabab import async test funksiyasi ichida
(pytest-asyncio loop allaqachon ishlab turgan holatda) amalga oshiriladi.
"""


async def test_custom_emoji_and_link_entities_map_to_correct_substrings():
    from app.userbot import CUSTOM_EMOJI_IDS, MESSAGE_TEMPLATE, build_formatted_message

    entities = build_formatted_message(MESSAGE_TEMPLATE, "test_bot")
    utf16 = MESSAGE_TEMPLATE.encode("utf-16-le")

    found_emoji_chars = set()
    found_urls = {}

    for entity in entities:
        start = entity.offset * 2
        end = start + entity.length * 2
        substring = utf16[start:end].decode("utf-16-le")

        doc_id = getattr(entity, "document_id", None)
        url = getattr(entity, "url", None)

        if doc_id is not None:
            assert CUSTOM_EMOJI_IDS[substring] == doc_id
            found_emoji_chars.add(substring)
        elif url is not None:
            found_urls[substring] = url

    assert found_emoji_chars == set(CUSTOM_EMOJI_IDS.keys())
    assert found_urls["Botga start bosish"] == "https://t.me/test_bot"
    assert found_urls["Instagram"] == "https://www.instagram.com/dunyabunya.uz/"
    assert found_urls["Telegram"] == "https://t.me/dunyabunya"
    assert found_urls["Youtube"] == "https://www.youtube.com/@dunyabunyauz"


async def test_auto_reply_points_to_support_and_bot():
    """2026-08-03: support telefon raqami ham qo'shildi (avval faqat @username bor edi)."""
    from app.userbot import AUTO_REPLY_TEXT

    assert "+998 78 113 09 90" in AUTO_REPLY_TEXT
    assert "@dunyabunya_support" in AUTO_REPLY_TEXT
    assert "https://t.me/" in AUTO_REPLY_TEXT
