# app/ai_service.py
import json
import re
from pathlib import Path

from openai import AsyncOpenAI

from app.config import settings
from app.models import ChatMessage
from app.pii_mask import mask_pii
from app.tools import TOOLS_REGISTRY

client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds)

def _build_not_found_message() -> str:
    return (
        "Kechirasiz, bu mahsulot haqida hozircha ma'lumotim yo'q. "
        f"Batafsil ma'lumot uchun {settings.company_phone} raqamiga qo'ng'iroq qilishingiz mumkin. "
        "Xohlasangiz, operatorga ulab qo'yay?"
    )


NOT_FOUND_MESSAGE = _build_not_found_message()

_SYSTEM_PROMPT_PATH = Path(__file__).parent / "system_prompt.txt"
_SYSTEM_PROMPT_TEMPLATE = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _build_system_instruction() -> str:
    text = _SYSTEM_PROMPT_TEMPLATE
    text = text.replace("{KOMPANIYA_NOMI}", settings.company_name)
    text = text.replace("{TELEFON}", settings.company_phone)
    text = text.replace("{MANZIL}", settings.company_address)
    text = text.replace("{ISH_VAQTI}", settings.company_work_hours)
    text = text.replace("{OPERATOR_TELEGRAM}", settings.operator_telegram)
    return text


SYSTEM_INSTRUCTION = _build_system_instruction()

TOOLS = [tool.openai_schema() for tool in TOOLS_REGISTRY]
TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS_REGISTRY}

# OpenAI o'zi javob bera olmagan (timeout/tarmoq/xato) holatlar uchun — aniq til
# aniqlash emas, faqat qaysi tilda uzr so'rashni tanlash uchun sodda evristika.
_RU_ONLY_RE = re.compile(r"[ёъэы]", re.IGNORECASE)
_UNAVAILABLE_MESSAGE = {
    "uz": "Hozir javob bera olmayapman, birozdan so'ng urinib ko'ring.",
    "ru": "Сейчас не могу ответить, попробуйте немного позже.",
}

# UI markdown ko'rsata olmaydi — model shunga rioya qilishi kerak (system prompt),
# lekin bu kod darajasidagi qo'shimcha xavfsizlik to'ri.
_MD_BOLD_RE = re.compile(r"\*\*(.*?)\*\*")
_MD_HEADER_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_MD_CODE_RE = re.compile(r"`([^`]*)`")

# Chat pufakchasi tor: "248 880 so'm" qator oxirida "248" / "880 so'm" bo'lib
# ketmasligi uchun raqam guruhlari va "so'm/сум" oldidagi probel bo'linmaydigan
# probelga (U+00A0) almashtiriladi.
_NUMBER_GROUP_SPACE_RE = re.compile(r"(?<=\d) (?=\d{3}\b)")
_CURRENCY_SPACE_RE = re.compile(r"(?<=\d) (?=so'm|сум)")


def _detect_language(text: str) -> str:
    return "ru" if _RU_ONLY_RE.search(text or "") else "uz"


def _strip_markdown(text: str) -> str:
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_HEADER_RE.sub("", text)
    text = _MD_CODE_RE.sub(r"\1", text)
    text = text.replace("|", " ")
    return text


def _keep_prices_together(text: str) -> str:
    text = _NUMBER_GROUP_SPACE_RE.sub(" ", text)
    return _CURRENCY_SPACE_RE.sub(" ", text)


def _finalize(text: str) -> str:
    text = _keep_prices_together(_strip_markdown(text or "").strip())
    if not text:
        text = "Kechirasiz, javob shakllantira olmadim."
    return text


def _last_user_text(history: list[ChatMessage]) -> str:
    for m in reversed(history):
        if m.role != "bot":
            return m.content
    return ""


async def get_answer(history: list[ChatMessage], conversation_id: str) -> str:
    recent_history = history[-settings.history_max_messages :]

    messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
    for m in recent_history:
        role = "assistant" if m.role == "bot" else "user"
        content = mask_pii(m.content) if role == "user" else m.content
        messages.append({"role": role, "content": content})

    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            tools=TOOLS,
            max_tokens=settings.max_response_tokens,
        )
    except Exception:
        lang = _detect_language(_last_user_text(history))
        return _UNAVAILABLE_MESSAGE[lang]

    message = response.choices[0].message

    tool_calls = message.tool_calls or []
    call = next((c for c in tool_calls if c.function.name in TOOLS_BY_NAME), None)

    if call is not None:
        tool = TOOLS_BY_NAME[call.function.name]
        try:
            args = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}

        result = await tool.handler(conversation_id, **args)

        # Faqat search_products_tool bo'sh natijasi shu qat'iy "topilmadi"
        # qisqa yo'lini talab qiladi — boshqa tool (masalan
        # connect_operator_tool)ning natijasi bu shaklda emas. Mahsulot topilib,
        # lekin omborda yo'q bo'lsa, bu yerda to'xtatilmaydi: model narxni va
        # "hozircha yo'q"ni aytib, alternativa taklif qiladi (system_prompt.txt
        # 5-6-qoidalar).
        if call.function.name == "search_products_tool" and not result:
            return _finalize(NOT_FOUND_MESSAGE)

        messages.append(message.model_dump(exclude_none=True))
        messages.append({
            "role": "tool",
            "tool_call_id": call.id,
            "content": json.dumps(result, ensure_ascii=False, default=str),
        })

        try:
            response = await client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                max_tokens=settings.max_response_tokens,
            )
        except Exception:
            lang = _detect_language(_last_user_text(history))
            return _UNAVAILABLE_MESSAGE[lang]
        return _finalize(response.choices[0].message.content)

    return _finalize(message.content)
