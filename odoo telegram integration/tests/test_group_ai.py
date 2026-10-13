"""app.group_ai -- guruh xabarlarini tasniflashni tekshiradi: kalit-so'zlar bo'yicha (LLM'siz)
rejim, "N daqiqada javob beraman" muddatini ajratish, va Model javobiga "kompaniya haqidami"
hamda ishonch foizi chegarasini qo'llash."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app import group_ai
from app.group_ai import MessageAnalysis, analyze_by_keywords, analyze_message, parse_promised_minutes


@pytest.mark.parametrize(
    "text",
    [
        "tovar yo'q ekan",
        "Bu tovar yo‘q ekan",
        "tovarlar omborda qolmagan",
        "mahsulot tugabdi",
        "товара нет",
        "нет в наличии",
        "Товар йўқ экан",
    ],
)
def test_out_of_stock_detected(text):
    assert analyze_by_keywords(text, is_staff=True).out_of_stock is True


@pytest.mark.parametrize(
    "text", ["tovar bormi?", "tovar qachon keladi", "salom", "Bu tovar yo'q bo'lsa boshqasini bering"]
)
def test_out_of_stock_not_detected(text):
    assert analyze_by_keywords(text, is_staff=False).out_of_stock is False


def test_customer_complaint_and_praise_when_addressed_to_staff():
    def category(text):
        return analyze_by_keywords(text, is_staff=False, addressed_to_staff=True).category

    assert category("Xizmatingiz juda yomon, noroziman") == "complaint"
    assert category("Xizmatingizdan juda xursandman, katta rahmat!") == "praise"
    assert category("Ertaga soat nechida kelasiz?") == "none"


@pytest.mark.parametrize(
    "text",
    [
        "Yomon emas, rahmat",
        "Kechikmasdan olib keldingiz",
        "Kechirasiz, ertaga kechikib boraman",
        "Brak chiqsa almashtirasizmi?",
        "Zo'r, ertaga olaman",
        "Ok rahmat",
        "Отлично, тогда завтра заберу",
    ],
)
def test_ordinary_phrases_are_not_complaint_or_praise(text):
    assert analyze_by_keywords(text, is_staff=False, addressed_to_staff=True).category == "none"


def test_customer_talk_not_addressed_to_staff_is_ignored():
    assert analyze_by_keywords("Xizmatingiz juda yomon, noroziman", is_staff=False).category == "none"


def test_staff_message_is_never_complaint_or_praise():
    assert analyze_by_keywords("yomon ob-havo", is_staff=True, addressed_to_staff=True).category == "none"


@pytest.mark.parametrize(
    "text, minutes",
    [
        ("10 minutda javob beraman", 10),
        ("5 daqiqa kuting", 5),
        ("15 мин", 15),
        ("1 soatda aytaman", 60),
        ("yarim soatda javob beraman", 30),
        ("hozir qarayman", None),
        ("narxi 10 ming so'm", None),
        ("900 minutda", 240),
    ],
)
def test_parse_promised_minutes(text, minutes):
    assert parse_promised_minutes(text) == minutes


def test_promise_only_counts_for_staff():
    assert analyze_by_keywords("10 minutda javob bering", is_staff=False).promised_minutes is None
    assert analyze_by_keywords("10 minutda javob beraman", is_staff=True).promised_minutes == 10


async def test_analyze_message_uses_keywords_without_api_key(monkeypatch):
    monkeypatch.setattr(group_ai.settings, "OPENAI_API_KEY", None)
    assert (await analyze_message("tovar yo'q ekan", is_staff=True)).out_of_stock is True


async def test_analyze_message_falls_back_when_llm_fails(monkeypatch):
    monkeypatch.setattr(group_ai.settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(group_ai, "_analyze_with_llm", AsyncMock(return_value=None))
    assert (await analyze_message("tovar yo'q ekan", is_staff=True)).out_of_stock is True


async def test_analyze_message_skips_empty_text():
    assert await analyze_message("👍", is_staff=False) == MessageAnalysis()


def _fake_llm(monkeypatch, **fields):
    """Model javobini soxtalashtiradi; `messages.create` mock'ini qaytaradi."""
    data = {"about_company": True, "category": "none", "out_of_stock": False, "confidence": 95, "needs_reply": False,
            "reply_promise": False, "promised_minutes": 0}
    data.update(fields)
    choice = SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(data)))
    create = AsyncMock(return_value=SimpleNamespace(choices=[choice]))
    monkeypatch.setattr(group_ai.settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(group_ai.settings, "GROUP_AI_MIN_CONFIDENCE", 80)
    monkeypatch.setattr(group_ai, "_get_client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    return create


async def test_llm_confident_complaint_about_company_is_kept(monkeypatch):
    _fake_llm(monkeypatch, category="complaint", confidence=92)
    assert (await analyze_message("matn", is_staff=False)).category == "complaint"


async def test_llm_result_not_about_company_is_dropped(monkeypatch):
    _fake_llm(monkeypatch, about_company=False, category="complaint", out_of_stock=True, confidence=99)
    assert await analyze_message("matn", is_staff=False) == MessageAnalysis()


async def test_llm_low_confidence_result_is_dropped(monkeypatch):
    """Ishonchi past xulosa hech qayerga tashlanmaydi, lekin xabar kompaniyaga oid deb sanaladi."""
    _fake_llm(monkeypatch, category="praise", out_of_stock=True, needs_reply=True, confidence=79)
    result = await analyze_message("matn", is_staff=False)
    assert (result.category, result.out_of_stock, result.needs_reply) == ("none", False, False)
    assert result.about_company is True and result.sentiment == "neutral"


async def test_llm_promise_does_not_depend_on_confidence(monkeypatch):
    _fake_llm(monkeypatch, about_company=False, confidence=10, reply_promise=True, promised_minutes=10)
    assert (await analyze_message("matn", is_staff=True)).promised_minutes == 10


@pytest.mark.parametrize(
    "text, llm_minutes, expected",
    [
        ("10 minutda javob beraman", 10, 10),
        ("Дайте 15 минут, уточню у бухгалтера", 5, 15),  # model raqamni noto'g'ri ko'chirgan
        ("Yarim soatda aytaman", 30, 30),
        ("Bir soatda javobini aytaman", 60, 60),  # so'z bilan yozilgan -- modelga ishonamiz
        ("Hozir aniqlab aytaman", 0, 5),  # muddat aytilmagan -- odatdagi kutish vaqti
        ("10 minutda aytaman, tovar 2 soatda boradi", 10, 10),
    ],
)
async def test_llm_promise_minutes_are_checked_against_text(monkeypatch, text, llm_minutes, expected):
    _fake_llm(monkeypatch, reply_promise=True, promised_minutes=llm_minutes)
    monkeypatch.setattr(group_ai.settings, "STAFF_REPLY_TIMEOUT_MINUTES", 5)
    assert (await analyze_message(text, is_staff=True)).promised_minutes == expected


async def test_llm_duration_without_reply_promise_is_not_a_promise(monkeypatch):
    """"2 soatda yetkazamiz" -- yetkazib berish vaqti: model va'da emas degan bo'lsa, matndagi
    raqamdan qat'i nazar kutish surilmaydi. Mijoz xabarida va'da umuman bo'lmaydi."""
    _fake_llm(monkeypatch, reply_promise=False, promised_minutes=120)
    assert (await analyze_message("2 soatda yetkazib beramiz", is_staff=True)).promised_minutes is None
    _fake_llm(monkeypatch, reply_promise=True, promised_minutes=10)
    assert (await analyze_message("10 minutda javob bering", is_staff=False)).promised_minutes is None


async def test_llm_request_contains_context_and_sender_role(monkeypatch):
    create = _fake_llm(monkeypatch)
    await analyze_message("Ha, yomon o'ynashdi", is_staff=False, context="[mijoz] Sardor: Futbolni ko'rdingmi?")

    content = create.await_args.kwargs["messages"][1]["content"]
    assert "[mijoz] Sardor: Futbolni ko'rdingmi?" in content
    assert "(mijoz yozgan):\nHa, yomon o'ynashdi" in content


async def test_llm_needs_reply_only_for_trusted_customer_messages(monkeypatch):
    _fake_llm(monkeypatch, needs_reply=True)
    assert (await analyze_message("matn", is_staff=False)).needs_reply is True
    assert (await analyze_message("matn", is_staff=True)).needs_reply is False

    _fake_llm(monkeypatch, needs_reply=True, about_company=False)
    assert (await analyze_message("matn", is_staff=False)).needs_reply is False


_RAW = {"about_company": True, "category": "none", "out_of_stock": False, "confidence": 95, "needs_reply": False,
        "reply_promise": False, "promised_minutes": 0, "topic": "other"}


def _ollama_response(**fields):
    content = json.dumps(_RAW | fields)
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"message": {"content": content}})


def _fake_ollama(monkeypatch, post):
    monkeypatch.setattr(group_ai.settings, "GROUP_AI_PROVIDER", "ollama")
    monkeypatch.setattr(group_ai.settings, "GROUP_AI_MODEL", "gemma4:12b")
    monkeypatch.setattr(group_ai.settings, "GROUP_AI_MIN_CONFIDENCE", 80)
    monkeypatch.setattr(group_ai.settings, "OPENAI_API_KEY", None)
    monkeypatch.setattr(group_ai, "_get_ollama_client", lambda: SimpleNamespace(post=post))


async def test_ollama_provider_needs_no_api_key_and_sends_schema(monkeypatch):
    post = AsyncMock(return_value=_ollama_response(category="complaint", needs_reply=True, topic="delivery"))
    _fake_ollama(monkeypatch, post)

    result = await analyze_message("Uch kundan beri kutyapman", is_staff=False, context="[xodim] Ali: Yo'lda")

    assert result == MessageAnalysis(category="complaint", needs_reply=True, about_company=True, topic="delivery")
    assert post.await_args.args == ("/api/chat",)
    payload = post.await_args.kwargs["json"]
    assert payload["model"] == "gemma4:12b"
    assert payload["format"] == group_ai._OUTPUT_SCHEMA
    assert payload["think"] is False and payload["stream"] is False
    assert "[xodim] Ali: Yo'lda" in payload["messages"][1]["content"]
    assert "images" not in payload["messages"][1]


async def test_ollama_unreachable_falls_back_to_keywords(monkeypatch):
    _fake_ollama(monkeypatch, AsyncMock(side_effect=httpx.ConnectError("connection refused")))
    assert (await analyze_message("tovar yo'q ekan", is_staff=True)).out_of_stock is True


async def test_llm_response_with_missing_field_falls_back_to_keywords(monkeypatch):
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"message": {"content": "{}"}})
    _fake_ollama(monkeypatch, AsyncMock(return_value=response))
    assert (await analyze_message("tovar yo'q ekan", is_staff=True)).out_of_stock is True


# --- 8 ta toifa, kayfiyat (2026-10-12) ---------------------------------------------------------


@pytest.mark.parametrize(
    "fields, category_name, sentiment",
    [
        (dict(category="complaint", topic="delivery"), "Dastavka va Logistika", "negative"),
        (dict(category="complaint", topic="quality"), "Mahsulot Sifati va Nuqsonlar", "negative"),
        (dict(category="none", topic="price"), "Narx va To'lovlar", "neutral"),
        (dict(category="none", topic="returns"), "Qaytarish va Kafolat", "neutral"),
        # Ijobiy fikr qaysi soha haqida bo'lishidan qat'i nazar bitta toifaga tushadi
        (dict(category="praise", topic="delivery"), "Ijobiy Fikrlar", "positive"),
        (dict(category="none", topic="other"), "Boshqa", "neutral"),
        (dict(category="complaint", topic="nimadir"), "Boshqa", "negative"),  # model sxemadan chiqib ketgan
    ],
)
async def test_llm_topic_becomes_one_of_eight_categories(monkeypatch, fields, category_name, sentiment):
    _fake_llm(monkeypatch, **fields)
    result = await analyze_message("matn", is_staff=False)
    assert (result.category_name, result.sentiment) == (category_name, sentiment)
    assert result.category_name in group_ai.CATEGORIES.values() and len(group_ai.CATEGORIES) == 8


async def test_topic_is_ignored_for_chatter_and_staff(monkeypatch):
    _fake_llm(monkeypatch, about_company=False, topic="delivery")
    result = await analyze_message("matn", is_staff=False)
    assert result.topic == "other" and result.company_related is False

    _fake_llm(monkeypatch, topic="delivery")
    assert (await analyze_message("matn", is_staff=True)).topic == "other"


def test_keyword_mode_marks_only_found_messages_as_company_related():
    found = analyze_by_keywords("Xizmatingiz juda yomon", is_staff=False, addressed_to_staff=True)
    assert found.company_related is True and found.category_name == "Boshqa" and found.sentiment == "negative"
    assert analyze_by_keywords("Bugun havo yomon", is_staff=False).company_related is False


# --- Rasm ----------------------------------------------------------------------------------------


async def test_ollama_image_request_sends_image_and_gets_description(monkeypatch):
    post = AsyncMock(return_value=_ollama_response(
        image_description="  Singan g'ishtlar   taxlami ", category="complaint", topic="quality"))
    _fake_ollama(monkeypatch, post)

    result = await analyze_message("", is_staff=False, images=[b"\xff\xd8jpeg"])

    assert result.category_name == "Mahsulot Sifati va Nuqsonlar"
    assert result.image_note == "Singan g'ishtlar taxlami"
    payload = post.await_args.kwargs["json"]
    assert payload["messages"][1]["images"] == ["/9hqcGVn"]  # base64
    assert "(matnsiz, faqat rasm)" in payload["messages"][1]["content"]
    assert "image_description" in payload["messages"][0]["content"]
    # Model avval rasmni tasvirlaydi, keyin tasniflaydi
    assert list(payload["format"]["properties"])[0] == "image_description"
    assert payload["format"]["required"][0] == "image_description"


async def test_text_request_prompt_and_schema_have_no_image_part(monkeypatch):
    post = AsyncMock(return_value=_ollama_response())
    _fake_ollama(monkeypatch, post)
    await analyze_message("Sement bormi?", is_staff=False)

    payload = post.await_args.kwargs["json"]
    assert "image_description" not in payload["messages"][0]["content"]
    assert "image_description" not in payload["format"]["properties"]


async def test_openai_image_request_uses_image_parts(monkeypatch):
    create = _fake_llm(monkeypatch, image_description="Chek", topic="price")
    result = await analyze_message("To'lov qildim", is_staff=False, images=[b"abc"])

    assert result.image_note == "Chek"
    content = create.await_args.kwargs["messages"][1]["content"]
    assert content[0]["type"] == "text" and "To'lov qildim" in content[0]["text"]
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,YWJj"
    assert create.await_args.kwargs["response_format"]["json_schema"]["schema"] == group_ai._IMAGE_OUTPUT_SCHEMA


async def test_image_request_failure_retries_with_text_only(monkeypatch):
    """Rasm ko'ra olmaydigan model rasmli so'rovni rad etsa, xabar matni bo'yicha tasniflanadi."""
    calls = []

    async def post(path, json):
        calls.append("images" in json["messages"][1])
        if "images" in json["messages"][1]:
            raise httpx.ConnectError("model does not support images")
        return _ollama_response(category="complaint")

    _fake_ollama(monkeypatch, post)
    result = await analyze_message("Yarmi singan keldi", is_staff=False, images=[b"abc"])
    assert calls == [True, False] and result.category == "complaint"


# --- Xabar turiga qarab boshqa model -----------------------------------------------------------------


@pytest.mark.parametrize("model, has_reasoning", [("gpt-4o-mini", False), ("gpt-5.4-mini", True), ("gpt-4.1", False)])
async def test_openai_reasoning_effort_only_for_reasoning_models(monkeypatch, model, has_reasoning):
    create = _fake_llm(monkeypatch)
    monkeypatch.setattr(group_ai.settings, "GROUP_AI_MODEL", model)
    await analyze_message("matn", is_staff=False)

    kwargs = create.await_args.kwargs
    assert kwargs["model"] == model
    assert ("reasoning_effort" in kwargs) is has_reasoning
    assert ("temperature" in kwargs) is not has_reasoning


async def test_provider_override_is_used_for_this_message_only(monkeypatch):
    post = AsyncMock(return_value=_ollama_response())
    _fake_ollama(monkeypatch, post)
    create = _fake_llm(monkeypatch, category="complaint")  # OPENAI_API_KEY ham shu yerda qo'yiladi

    result = await analyze_message("matn", is_staff=False, provider="openai", model="gpt-4o-mini")

    assert result.category == "complaint"
    assert create.await_args.kwargs["model"] == "gpt-4o-mini"
    post.assert_not_called()


async def test_provider_override_falls_back_to_main_model(monkeypatch):
    post = AsyncMock(return_value=_ollama_response(category="praise"))
    _fake_ollama(monkeypatch, post)
    create = _fake_llm(monkeypatch)
    create.side_effect = group_ai.openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))

    result = await analyze_message("matn", is_staff=False, provider="openai", model="gpt-4o-mini")

    assert result.category == "praise"
    assert post.await_args.kwargs["json"]["model"] == "gemma4:12b"


async def test_provider_override_without_key_goes_straight_to_main_model(monkeypatch):
    post = AsyncMock(return_value=_ollama_response(category="praise"))
    _fake_ollama(monkeypatch, post)  # OPENAI_API_KEY = None
    assert (await analyze_message("matn", is_staff=False, provider="openai", model="gpt-4o-mini")).category == "praise"
