from __future__ import annotations

import json
import re
from datetime import date

import anthropic
from pydantic import BaseModel, Field

from app.config import settings
from app.constants import DEPARTMENT_NAMES, DEPARTMENTS
from app.prompt_guard import DATA_ONLY_NOTE, wrap_user_input

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
        except Exception:
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
        except Exception:
            return AnalyzeResult(
                is_task=True, is_task_confidence=0.7,
                title=text[:80] if text else "",
                description=text or "",
                department_scores={name: 0.0 for name in DEPARTMENT_NAMES},
            )

    result = AnalyzeResult(
        is_task=bool(data.get("is_task", data.get("task", True))),
        is_task_confidence=float(data.get("is_task_confidence") or 0.8),
        title=str(data.get("title") or ""),
        description=str(data.get("description") or ""),
        assignee_name=data.get("assignee_name") or None,
        deadline=data.get("deadline") or None,
        department_scores=data.get("department_scores") or {},
    )

    for name in DEPARTMENT_NAMES:
        result.department_scores.setdefault(name, 0.0)

    return result
