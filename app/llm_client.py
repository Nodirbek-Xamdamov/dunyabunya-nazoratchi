"""
Tasniflash va xulosa uchun til modeliga so'rov (2026-10-13; avval `app/group_ai.py` ichida edi).

Provayder va model `.env`dan: `GROUP_AI_PROVIDER` -- "ollama" (shu kompyuterdagi lokal model,
kalit kerak emas, xabarlar tashqariga chiqmaydi) yoki "openai" (`OPENAI_API_KEY`);
`GROUP_AI_MODEL` -- model nomi. Javob har doim berilgan JSON sxema bo'yicha so'raladi.

`request_json` natijasi:
  - dict            -- model javobi (sxemaga mos JSON)
  - None            -- model javob berdi, lekin uni o'qib bo'lmadi (chaqiruvchi qayta urinadi)
  - LLMUnavailable  -- modelga yetib bo'lmadi yoki u xato qaytardi (chaqiruvchi kutib turadi)
"""

import asyncio
import json
import logging
import weakref

import httpx
import openai
from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    """Modelga yetib bo'lmadi (tarmoq, kalit, kvota, server xatosi)."""


_openai_client: AsyncOpenAI | None = None
_ollama_client: httpx.AsyncClient | None = None
# Har bir event loop uchun alohida: testlarda har test o'z loop'ida ishlaydi
_slots: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def model_name() -> str:
    return f"{settings.GROUP_AI_PROVIDER}:{settings.GROUP_AI_MODEL}"


def _slot() -> asyncio.Semaphore:
    """Modelga bir vaqtda ketadigan so'rovlar sonini cheklaydi (`GROUP_AI_MAX_CONCURRENCY`)."""
    loop = asyncio.get_running_loop()
    slot = _slots.get(loop)
    if slot is None:
        slot = _slots[loop] = asyncio.Semaphore(max(1, settings.GROUP_AI_MAX_CONCURRENCY))
    return slot


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=60.0, max_retries=1)
    return _openai_client


def _get_ollama_client() -> httpx.AsyncClient:
    global _ollama_client
    if _ollama_client is None:
        # Model xotiradan tushib qolgan bo'lsa birinchi so'rov uni qayta yuklaydi (10-20 soniya)
        _ollama_client = httpx.AsyncClient(base_url=settings.OLLAMA_URL, timeout=180.0)
    return _ollama_client


def _is_reasoning_model(model: str) -> bool:
    """`reasoning_effort` faqat "o'ylaydigan" modellarda bor (gpt-5*, o1/o3/o4)."""
    return model.startswith(("gpt-5", "o1", "o3", "o4"))


async def _request_openai(system_prompt: str, user_text: str, schema: dict, schema_name: str) -> str:
    if not settings.OPENAI_API_KEY:
        raise LLMUnavailable("OPENAI_API_KEY berilmagan")
    model = settings.GROUP_AI_MODEL
    extra = {"reasoning_effort": "low"} if _is_reasoning_model(model) else {"temperature": 0}
    try:
        response = await _get_openai_client().chat.completions.create(
            model=model,
            max_completion_tokens=4000,
            response_format={"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}],
            **extra,
        )
    except openai.OpenAIError as exc:
        raise LLMUnavailable(f"OpenAI: {type(exc).__name__}: {exc}") from exc
    return response.choices[0].message.content or ""


async def _request_ollama(system_prompt: str, user_text: str, schema: dict) -> str:
    payload = {
        "model": settings.GROUP_AI_MODEL,
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}],
        "stream": False,
        # Ollama javobni shu sxemaga majburlaydi
        "format": schema,
        # "O'ylash" rejimi javobni bir necha barobar sekinlashtiradi
        "think": False,
        "keep_alive": settings.GROUP_AI_KEEP_ALIVE,
        "options": {"temperature": 0, "num_ctx": 8192},
    }
    try:
        response = await _get_ollama_client().post("/api/chat", json=payload)
        response.raise_for_status()
        return response.json()["message"]["content"]
    except httpx.HTTPStatusError as exc:
        raise LLMUnavailable(f"Ollama {exc.response.status_code}: {exc.response.text[:200]}") from exc
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"Ollama'ga ulanib bo'lmadi ({settings.OLLAMA_URL}): {exc!r}") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise LLMUnavailable(f"Ollama javobi kutilgan ko'rinishda emas: {exc!r}") from exc


async def request_json(system_prompt: str, user_text: str, schema: dict, schema_name: str = "result") -> dict | None:
    """Modeldan `schema` bo'yicha JSON so'raydi."""
    async with _slot():
        if settings.GROUP_AI_PROVIDER == "ollama":
            content = await _request_ollama(system_prompt, user_text, schema)
        else:
            content = await _request_openai(system_prompt, user_text, schema, schema_name)
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        logger.warning("Model javobi JSON emas (%s belgi)", len(content or ""))
        return None
    return data if isinstance(data, dict) else None
