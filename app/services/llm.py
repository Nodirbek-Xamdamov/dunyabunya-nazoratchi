from __future__ import annotations

import json
import logging
import re
from datetime import date

import anthropic
from pydantic import BaseModel, Field

from app.config import settings
from app.constants import DEPARTMENT_NAMES, DEPARTMENTS
from app.prompt_guard import DATA_ONLY_NOTE, wrap_user_input

log = logging.getLogger(__name__)

_extra_headers = {}
if settings.ANTHROPIC_WORKSPACE_ID:
    _extra_headers["anthropic-workspace-id"] = settings.ANTHROPIC_WORKSPACE_ID

_client = anthropic.AsyncAnthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    default_headers=_extra_headers or None,
)

_openai_client = None


def _get_openai_client():
    """Lazy singleton OpenAI async client (testlarda monkeypatch qilish oson)."""
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI

        _openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=12, max_retries=0)
    return _openai_client


class AnalyzeResult(BaseModel):
    is_task: bool
    is_task_confidence: float = 1.0
    title: str = ""
    description: str = ""
    assignee_name: str | None = None
    deadline: str | None = None
    department_scores: dict[str, float] = Field(default_factory=dict)
    # Ikkala model ham javob bermadi — xabar tahlil QILINMAGAN (vazifami-yo'qmi noma'lum)
    analysis_failed: bool = False


def _as_score(value, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _valid_deadline(value) -> str | None:
    """Faqat haqiqiy YYYY-MM-DD sana o'tadi — model "ertaga" yoki "05.10.2026" qaytarsa
    Odoo vazifani umuman yaratmay, xato bilan rad etardi."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10]).isoformat()
    except ValueError:
        return None


def _clean_name(value) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip()
    return None if name.casefold() in ("", "null", "none", "yo'q", "-") else name


def _build_result(data: dict, text: str) -> AnalyzeResult:
    """Model qaytargan JSON'dan natija yasaydi — kutilmagan turdagi qiymatlar (matnli
    ishonch darajasi, ro'yxat ko'rinishidagi baholar...) xabarni qayta ishlashni yiqitmaydi."""
    scores = data.get("department_scores")
    is_task = bool(data.get("is_task", data.get("task", True)))
    title = str(data.get("title") or "").strip()
    description = str(data.get("description") or "").strip()
    if is_task:
        # Vazifa Odoo'ga bo'sh nom/tavsif bilan tushmasligi uchun — asl xabar matni
        description = description or text.strip()
        title = title or description[:80]
    result = AnalyzeResult(
        is_task=is_task,
        is_task_confidence=_as_score(data.get("is_task_confidence"), 0.8),
        title=title,
        description=description,
        assignee_name=_clean_name(data.get("assignee_name")),
        deadline=_valid_deadline(data.get("deadline")),
        department_scores={str(k): _as_score(v, 0.0) for k, v in scores.items()} if isinstance(scores, dict) else {},
    )
    for name in DEPARTMENT_NAMES:
        result.department_scores.setdefault(name, 0.0)
    return result


_DEPARTMENTS_JSON = json.dumps(DEPARTMENTS, ensure_ascii=False, indent=2)

_SYSTEM_TEMPLATE = f"""Siz kompaniya CEO'sining Telegram xabarlarini tahlil qiluvchi yordamchisisan.
Xabar VAZIFA (task — biror ish bajarilishi kerakligini bildiradi) yoki oddiy xabar/suhbat/eslatma
ekanini aniqla.

Bugungi sana: {{today}}. Muddatlarni ("ertaga", "shu hafta oxirigacha", "indinga") shu sanaga
nisbatan hisobla.

Agar vazifa bo'lsa:
- qisqa "title" (sarlavha) va batafsilroq "description" yoz.
- "assignee_name": agar CEO kimningdir ismini aniq aytgan bo'lsa (masalan "Aziz qilsin",
  "Nodirga yuborilsin", "Dilnozani chaqirsin"), o'sha ismni yoz, aks holda null.
  O'zbek tilida ismga TURLI QO'SHIMCHALAR qo'shiladi — ularni to'liq olib tashlab,
  FAQAT SOF ISMNI (nominativ o'zakni) yozish SHART:
  • Kelishik/egalik/ko'plik: -ga, -ka, -qa, -da, -dan, -ni, -ning, -lar, -larga, -lardan,
    -larning (masalan: "Nodirga"→"Nodir", "Muroddan"→"Murod", "Azizning"→"Aziz",
    "Dilnozalarni"→"Dilnoza")
  • Ismga yopishgan hurmat/erkalash: -bek, -jon, -xon (masalan: "Nodirbekka"→"Nodir",
    "Murodjon"→"Murod", "Azizbekdan"→"Aziz", "Boburjonni"→"Bobur", "Alisherxon"→"Alisher",
    "Rustamxon"→"Rustam")
  Agar ism bir nechta so'zdan iborat bo'lsa (ism + familiya), HAR BIR bo'lakdan
  qo'shimchalarni olib tashlab, qolganini yoz (masalan: "Nodirbek Xamdamovga"→
  "Nodir Xamdamov", "Aziza Karimova"→"Aziza Karimova").
  Ovozli transkriptda ism biroz buzilgan bo'lishi mumkin — kontekstdan taxmin qilib
  to'g'ri yoz.
- "deadline": agar CEO muddat aytgan bo'lsa, buni YYYY-MM-DD formatga hisoblab yoz, aks holda
  null.
- "is_task_confidence": 0.0-1.0 oralig'ida, bu haqiqatan ham vazifa ekaningizga qanchalik
  ishonchingiz komil ekanini bildiradi.

Xabar OVOZLI transkriptidan kelgan bo'lishi mumkin — tanish so'zga o'xshash, lekin ma'nosiz
transkripsiya xatolarini (masalan "mamashular" -> "mana shu + lar") kontekstdan taxmin qilib
to'g'rilab tushun, so'zma-so'z emas.

Muhim: STATISTIKA, HISOBOT, MA'LUMOT O'QISH so'rovlari — bu TASK EMAS!
Masalan: "sotuv statistikasi", "oylik hisobot", "3 oylik sotuv", "dashboard ko'rsat"
Bunday xabarlar uchun is_task=false va department_scores barchasi 0.0 bo'lishi kerak.

DEPARTMENT SCORES — JUDA MUHIM:
Xabar task bo'lsa, HAR BIR bo'lim uchun 0.0-1.0 bahoni aniq yozing!
Masalan: "Omborni tekshirish" -> Ombor=0.9, boshqalar=0.0
"Sotuv buyurtmasi tuzish" -> Sotuv=0.9, boshqalar=0.0
"Moliyaviy hisobot tayyorlash" -> Moliya=0.9, boshqalar=0.0
"CRM lidlarini ko'rish" -> CRM=0.9, boshqalar=0.0
"Xodimni ishga olish" -> HR=0.9, boshqalar=0.0

8 ta bo'lim: Moliya, Sotuv, Logistika, Taminot, Ombor, CRM, HR, Marketing

Javobni FAQAT JSON formatida qaytaring:
{{"is_task": true, "is_task_confidence": 0.95, "title": "...", "description": "...", "assignee_name": null, "deadline": null, "department_scores": {{"Moliya": 0.0, "Sotuv": 0.9, "Logistika": 0.0, "Taminot": 0.0, "Ombor": 0.0, "CRM": 0.0, "HR": 0.0, "Marketing": 0.0}}}}

MUHIM: Faqat JSON qaytaring, boshqa narsa yozmang! department_scores da 8 ta kalitning BARCHASI bo'lishi shart!
"""


async def analyze_message(text: str) -> AnalyzeResult:
    """Xabar tahlili — birinchi qatlam OpenAI (tez/arzon), keyin kerak bo'lsa Claude (fallback)."""
    import asyncio

    system = _SYSTEM_TEMPLATE.replace("{today}", date.today().isoformat())
    system += "\n\n" + DATA_ONLY_NOTE
    data = None

    # 1) Tezkor OpenAI tahlili
    if settings.OPENAI_API_KEY:
        try:
            response = await asyncio.wait_for(
                _get_openai_client().chat.completions.create(
                    model=settings.OPENAI_CHAT_MODEL,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": wrap_user_input(text)},
                    ],
                    response_format={"type": "json_object"},
                    max_completion_tokens=2048,
                ),
                timeout=12,
            )
            raw = (response.choices[0].message.content or "").strip()
            json_match = re.search(r'\{[\s\S]*\}', raw)
            if json_match:
                raw = json_match.group()
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = parsed
            else:
                log.warning("analyze_message: OpenAI (%s) JSON obyekt qaytarmadi — Claude'ga o'tiladi",
                            settings.OPENAI_CHAT_MODEL)
        except Exception as e:  # noqa: BLE001
            # P1-3: zaxira modelga o'tish logda ko'rinishi shart (limit/kalit/tarmoq muammosi
            # shu yerdan bilinadi — ilgari bu holat hech qayerda qayd etilmasdi)
            log.warning("analyze_message: OpenAI (%s) ishlamadi (%s: %s) — Claude'ga o'tiladi",
                        settings.OPENAI_CHAT_MODEL, type(e).__name__, str(e)[:200])
            data = None

    if data is None:
        # 2) Fallback: Claude (ishonchliroq, lekin sekin)
        try:
            response = await _client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=2048,
                system=system,
                messages=[{"role": "user", "content": wrap_user_input(text)}],
            )
            raw = response.content[0].text.strip()
            json_match = re.search(r'\{[\s\S]*\}', raw)
            if json_match:
                raw = json_match.group()
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Claude JSON obyekt qaytarmadi")
        except Exception as e:  # noqa: BLE001
            log.error("analyze_message: OpenAI ham, Claude (%s) ham ishlamadi (%s: %s) — xabar tahlil qilinmadi",
                      settings.ANTHROPIC_MODEL, type(e).__name__, str(e)[:200])
            return AnalyzeResult(
                is_task=True, is_task_confidence=0.7,
                title=text[:80] if text else "",
                description=text or "",
                department_scores={name: 0.0 for name in DEPARTMENT_NAMES},
                analysis_failed=True,
            )

    return _build_result(data, text)
