"""app.monitor_classifier -- kutayotgan xabarlarni paketlab tasniflash: arzon qoida, suhbat
konteksti, model javobini tekshirish, qayta urinish. Haqiqiy model chaqirilmaydi."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import llm_client, monitor_classifier, monitor_db
from app.llm_client import LLMUnavailable
from app.monitor_classifier import classify_pending, is_junk, parse_results
from app.monitor_db import Classification, ManagerCheck
from tests.monitor_helpers import CHAT, OTHER_CHAT, add_group, add_message, all_rows, get_message


def _item(number, category="Narx va To'lovlar", sentiment="Neytral", *, related=True, needs_manager=False, summary="Mazmun"):
    return {"id": number, "summary": summary, "is_company_related": related, "category": category,
            "sentiment": sentiment, "needs_manager": needs_manager}


def _fake_model(monkeypatch, *answers):
    """`request_json` navbat bilan `answers`ni qaytaradi (Exception bo'lsa -- tashlaydi)."""
    request = AsyncMock(side_effect=list(answers))
    monkeypatch.setattr(llm_client, "request_json", request)
    return request


async def _classifications():
    return {c.message_ref: c for c in await all_rows(Classification)}


@pytest.mark.parametrize("text, junk", [
    (None, True), ("", True), ("👍", True), ("+", True), ("???", True), ("🙏🙏 !!", True),
    ("ok", False), ("Ха", False), ("5", False), ("Sement bormi?", False),
])
def test_only_messages_without_letters_or_digits_skip_the_model(text, junk):
    assert is_junk(SimpleNamespace(full_text=text or "")) is junk


def test_parse_results_validates_and_normalizes():
    numbers = {1: 11, 2: 12, 3: 13, 4: 14, 5: 15, 6: 16}
    data = {"results": [
        _item(1, "Dastavka va Logistika", "Shikoyat", needs_manager=True, summary="  Mijoz   kechikishdan norozi "),
        _item(2, "Dastavka va Logistika", "Ijobiy"),  # ijobiy fikr -- doim "Ijobiy Fikrlar"
        _item(3, "Ijobiy Fikrlar", "Neytral"),
        _item(4, related=False, needs_manager=True),  # aloqasiz xabarga toifa ham, menejer ham kerak emas
        _item(5, "O'ylab topilgan toifa"),  # ro'yxatda yo'q -- yaroqsiz
        _item(6, sentiment="Yaxshi"),  # yaroqsiz
        _item(1, "Boshqa"),  # takror -- birinchisi qoladi
        _item(99),  # so'ralmagan raqam
        "axlat",
    ]}
    results = parse_results(data, numbers)

    assert set(results) == {11, 12, 13, 14}
    assert (results[11].category, results[11].sentiment, results[11].needs_manager) == (
        "Dastavka va Logistika", "Shikoyat", True)
    assert results[11].summary == "Mijoz kechikishdan norozi"
    assert (results[12].category, results[12].sentiment) == ("Ijobiy Fikrlar", "Ijobiy")
    assert (results[13].category, results[13].sentiment) == ("Ijobiy Fikrlar", "Ijobiy")
    assert (results[14].is_company_related, results[14].category, results[14].sentiment, results[14].needs_manager) == (
        False, None, None, False)
    assert parse_results(None, numbers) == {} and parse_results({"results": "x"}, numbers) == {}


async def test_junk_is_filtered_by_rule_and_the_rest_goes_to_the_model_with_context(monkeypatch):
    await add_group()
    await add_message(1, "Assalomu alaykum, eshitaman", is_staff=True, sender_id=102, name="Ali Xodim")
    question = await add_message(2, "Sement M500 qanchadan?")
    sticker = await add_message(3, None, media_type="sticker")
    await add_message(4, "65 mingdan", is_staff=True, sender_id=102, name="Ali Xodim", reply_to=2)
    thanks = await add_message(5, "Rahmat, kecha yetib keldi, hammasi joyida")
    request = _fake_model(monkeypatch, {"results": [
        _item(1, needs_manager=True, summary="Mijoz sement narxini so'radi"),
        _item(2, "Ijobiy Fikrlar", "Ijobiy", summary="Mijoz yetkazib berishdan mamnun"),
    ]})

    assert await classify_pending() == 3

    request.assert_awaited_once()
    _system, transcript, schema, _name = request.await_args.args
    assert schema["properties"]["results"]["items"]["properties"]["category"]["enum"] == list(monitor_db.CATEGORIES)
    assert "Guruh: Chilonzor obyekti" in transcript
    # Tasniflanadigan mijoz xabarlari raqamlangan, xodim xabarlari kontekst bo'lib turadi, stiker yo'q emas
    assert "   [xodim] Ali Xodim: Assalomu alaykum, eshitaman" in transcript
    assert "#1 [mijoz] Vali Mijoz: Sement M500 qanchadan?" in transcript
    assert "   [xodim] Ali Xodim (Vali Mijozga javoban): 65 mingdan" in transcript
    assert "#2 [mijoz] Vali Mijoz: Rahmat, kecha yetib keldi, hammasi joyida" in transcript
    assert "Tasniflanadigan xabarlar: #1, #2." in transcript

    saved = await _classifications()
    assert (saved[question].category, saved[question].needs_manager, saved[question].model) == (
        "Narx va To'lovlar", True, "openai:gpt-5.4-mini")
    assert (saved[thanks].category, saved[thanks].sentiment) == ("Ijobiy Fikrlar", "Ijobiy")
    assert (saved[sticker].is_company_related, saved[sticker].model) == (False, "rule")
    # Menejer javobi kerak bo'lgan xabar uchun tekshiruv ochildi
    (check,) = await all_rows(ManagerCheck)
    assert check.first_message_id == 2
    assert await classify_pending() == 0  # hammasi tasniflangan, model qayta chaqirilmaydi
    request.assert_awaited_once()


async def test_each_group_is_classified_in_its_own_request_in_batches(monkeypatch):
    await add_group()
    await add_group(OTHER_CHAT, "Sergeli")
    for message_id in range(1, 4):
        await add_message(message_id, f"Savol {message_id}")
    await add_message(9, "Boshqa guruh savoli", chat_id=OTHER_CHAT)
    monkeypatch.setattr(monitor_classifier.settings, "CLASSIFY_BATCH_SIZE", 2)
    request = _fake_model(
        monkeypatch,
        {"results": [_item(1)]}, {"results": [_item(1), _item(2)]}, {"results": [_item(1)]},
    )

    assert await classify_pending() == 4
    transcripts = [call.args[1] for call in request.await_args_list]
    assert ["Sergeli" in t for t in transcripts] == [True, False, False]
    assert "Savol 1" in transcripts[1] and "Savol 2" in transcripts[1] and "#1 [mijoz] Vali Mijoz: Savol 3" in transcripts[2]


async def test_invalid_answer_is_retried_once_then_left_for_the_next_run(monkeypatch):
    await add_group()
    first, second = await add_message(1, "Savol bir"), await add_message(2, "Savol ikki")
    request = _fake_model(
        monkeypatch,
        {"results": [_item(1), _item(2, "Noto'g'ri toifa")]},  # ikkinchisi yaroqsiz
        None,  # qayta so'ralganda javobni o'qib bo'lmadi
        {"results": [_item(1, "Boshqa")]},  # keyingi aylanish
    )

    assert await classify_pending() == 1
    assert "Savol bir" in request.await_args_list[1].args[1]  # kontekst sifatida qoladi
    assert "Tasniflanadigan xabarlar: #1." in request.await_args_list[1].args[1]
    message = await get_message(CHAT, 2)
    assert (message.status, message.attempts) == ("pending", 1)
    assert set(await _classifications()) == {first}

    assert await classify_pending() == 1
    assert (await _classifications())[second].category == "Boshqa"


async def test_message_that_never_classifies_becomes_failed(monkeypatch):
    await add_group()
    await add_message(1, "Savol")
    monkeypatch.setattr(monitor_classifier.settings, "CLASSIFY_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(llm_client, "request_json", AsyncMock(return_value={"results": []}))

    await classify_pending()
    await classify_pending()
    assert (await get_message(CHAT, 1)).status == "failed"
    assert await classify_pending() == 0


async def test_unreachable_model_leaves_messages_pending_without_counting_attempts(monkeypatch):
    await add_group()
    await add_message(1, "Savol")
    monkeypatch.setattr(llm_client, "request_json", AsyncMock(side_effect=LLMUnavailable("ollama o'chiq")))

    with pytest.raises(LLMUnavailable):
        await classify_pending()
    message = await get_message(CHAT, 1)
    assert (message.status, message.attempts) == ("pending", 0)


def test_prompt_lists_all_categories_with_uzbek_and_russian_examples():
    prompt = monitor_classifier.SYSTEM_PROMPT
    for category in monitor_db.CATEGORIES:
        assert f'"{category}"' in prompt
    assert "Когда будет доставка?" in prompt and "Buyurtmam qachon yetib keladi?" in prompt
    assert "rahmat, yetib keldi" in prompt


# --- Ovozli xabar -> matn (OpenAI) ---------------------------------------------------------------


@pytest.fixture
def voice_bot(monkeypatch):
    """Ovozni matnga aylantirish yoqilgan, fayl yuklab beradigan bot bilan."""
    bot = SimpleNamespace(download=AsyncMock())
    monkeypatch.setattr(monitor_classifier.settings, "GROUP_STT_ENABLED", True)
    monkeypatch.setattr(monitor_classifier.settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(monitor_classifier.settings, "GROUP_STT_MAX_DURATION_SECONDS", 600)
    monitor_classifier.set_bot(bot)
    yield bot
    monitor_classifier.set_bot(None)


async def _add_voice(message_id=1, *, media_type="voice", duration=12, caption=None) -> int:
    await monitor_db.save_message(
        chat_id=CHAT, message_id=message_id, sender_id=200, sender_username="vali", sender_name="Vali Mijoz",
        is_staff=False, text=caption, media_type=media_type, file_id=f"file-{message_id}", reply_to_message_id=None,
        sent_at=monitor_db.utc_now(), media_duration=duration,
    )
    return (await get_message(CHAT, message_id)).id


def _fake_stt(monkeypatch, *answers):
    transcribe = AsyncMock(side_effect=list(answers))
    monkeypatch.setattr(monitor_classifier.stt, "transcribe_openai", transcribe)
    return transcribe


async def test_voice_message_is_transcribed_by_openai_then_classified(monkeypatch, voice_bot):
    await add_group()
    voice = await _add_voice()
    transcribe = _fake_stt(monkeypatch, "uch kundan beri kutyapman, mashina kelmadi")
    request = _fake_model(monkeypatch, {"results": [_item(1, "Dastavka va Logistika", "Shikoyat", needs_manager=True)]})

    assert await classify_pending() == 1

    assert voice_bot.download.await_args.args[0] == "file-1"
    path = voice_bot.download.await_args.kwargs["destination"]
    assert path.endswith(".ogg") and transcribe.await_args.args == (path,)
    assert not monitor_classifier.os.path.exists(path)  # vaqtinchalik fayl o'chirilgan
    assert "#1 [mijoz] Vali Mijoz: [ovozli xabar] uch kundan beri kutyapman, mashina kelmadi" in request.await_args.args[1]
    message = await get_message(CHAT, 1)
    assert message.transcript == "uch kundan beri kutyapman, mashina kelmadi" and message.status == "done"
    assert (await _classifications())[voice].sentiment == "Shikoyat"


async def test_voice_without_speech_is_filtered_without_calling_the_model(monkeypatch, voice_bot):
    await add_group()
    voice = await _add_voice()
    _fake_stt(monkeypatch, "")
    request = _fake_model(monkeypatch)

    assert await classify_pending() == 1
    request.assert_not_called()
    saved = (await _classifications())[voice]
    assert (saved.is_company_related, saved.model) == (False, "rule")


async def test_failed_transcription_is_retried_then_given_up(monkeypatch, voice_bot):
    await add_group()
    await _add_voice()
    transcribe = _fake_stt(monkeypatch, None, None, None)
    _fake_model(monkeypatch)

    assert await classify_pending() == 0  # OpenAI javob bermadi -- xabar kutib turadi
    assert await classify_pending() == 0
    message = await get_message(CHAT, 1)
    assert (message.status, message.transcript, message.attempts) == ("pending", None, 2)
    assert await classify_pending() == 1  # uchinchi urinishdan keyin matnsiz davom etadi
    assert transcribe.await_count == 3
    assert (await get_message(CHAT, 1)).transcript == ""


async def test_long_recordings_and_download_errors_are_not_retried(monkeypatch, voice_bot):
    await add_group()
    await _add_voice(1, media_type="video", duration=3600)  # juda uzun
    await _add_voice(2, caption="Mana qarang, hammasi singan")
    transcribe = _fake_stt(monkeypatch)
    voice_bot.download = AsyncMock(side_effect=RuntimeError("file is too big"))
    request = _fake_model(monkeypatch, {"results": [_item(1, "Mahsulot Sifati va Nuqsonlar", "Shikoyat")]})

    assert await classify_pending() == 2
    voice_bot.download.assert_awaited_once()  # uzun video umuman yuklanmadi
    transcribe.assert_not_called()
    assert "Mana qarang, hammasi singan" in request.await_args.args[1]  # izohi bo'yicha tasniflandi


async def test_voice_is_not_transcribed_when_switched_off_or_without_bot(monkeypatch, voice_bot):
    await add_group()
    await _add_voice(1)
    transcribe = _fake_stt(monkeypatch)
    monkeypatch.setattr(monitor_classifier.settings, "GROUP_STT_ENABLED", False)
    assert await classify_pending() == 1

    monkeypatch.setattr(monitor_classifier.settings, "GROUP_STT_ENABLED", True)
    monitor_classifier.set_bot(None)
    await _add_voice(2)
    assert await classify_pending() == 1
    transcribe.assert_not_called()
    assert all(c.model == "rule" for c in (await _classifications()).values())
