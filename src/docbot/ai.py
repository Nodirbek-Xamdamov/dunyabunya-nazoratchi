"""Reading a document picture with the OpenAI Responses API (strict JSON schema output).

The AI only *reads*: its answer is data that is checked against Odoo afterwards (``verify``), never an
instruction. Rate limits, 5xx, timeouts and a rejected key are transient (the job waits and retries);
a request OpenAI refuses for this particular picture raises ``AiRejected`` (the sender is asked).
"""

import base64
import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any

import aiohttp

from docbot.config import AiSettings
from docbot.documents import Documents
from docbot.errors import DocbotError, TransientError

log = logging.getLogger(__name__)

API_URL = "https://api.openai.com/v1/responses"

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "similarity", "dunyabunya", "rotation", "doc_number", "partner", "phone", "person", "total", "first_item", "last_item",
        "page_number", "page_count", "signed",
    ],
    "properties": {
        "similarity": {
            "type": "integer",
            "description": "0-100: how sure you are that the picture shows one of the described printed documents "
            "(title, logo, table of goods, QR code), even if blurred or small. 0 = something else (people, goods, chat, other papers)",
        },
        "dunyabunya": {
            "type": "boolean",
            "description": "true only if the dunyabunya logo or the name 'dunyabunya' is printed on the document itself",
        },
        "rotation": {
            "type": "integer",
            "enum": [0, 90, 180, 270],
            "description": "how many degrees CLOCKWISE the picture must be turned so the document's text reads normally",
        },
        "doc_number": {
            "type": ["string", "null"],
            "description": "the document's own number: from its title, or the small number printed in the top right "
            "corner of every page (e.g. S81878 above www.dunyabunya.uz)",
        },
        "partner": {"type": ["string", "null"], "description": "customer or vendor name ('Xaridor:', 'Yetkazib beruvchi:')"},
        "phone": {"type": ["string", "null"], "description": "customer phone ('Telefon raqami:')"},
        "person": {"type": ["string", "null"], "description": "manager / responsible person ('Menejer:')"},
        "total": {"type": ["number", "null"], "description": "total to pay ('To'lov uchun:') as a plain number"},
        "first_item": {"type": ["integer", "null"], "description": "row number (№ column) of the first goods row on this page"},
        "last_item": {"type": ["integer", "null"], "description": "row number (№ column) of the last goods row on this page"},
        "page_number": {"type": ["integer", "null"], "description": "page number printed at the bottom of the page (2 in '2/5')"},
        "page_count": {"type": ["integer", "null"], "description": "total pages, if printed with the page number (5 in '2/5')"},
        "signed": {
            "type": "boolean",
            "description": "true if anything is written or drawn BY HAND with a pen on the paper: a signature, handwritten "
            "words or numbers, ticks, crosses, scribbles. false if it shows only printed content (printed text and empty "
            "signature lines do not count, nor does a stamp alone)",
        },
    },
}


class AiRejected(DocbotError):
    """OpenAI refused this picture (bad request, content refusal, unreadable answer). Retrying will not help."""


@dataclass(frozen=True)
class Extraction:
    similarity: int  # 0-100, see SCHEMA
    doc_number: str | None = None
    branded: bool = False  # the dunyabunya logo/name is visible on the document
    rotation: int = 0  # clockwise turn that makes the document upright
    partner: str | None = None
    phone: str | None = None
    person: str | None = None
    total: float | None = None
    first_item: int | None = None  # goods rows on this page (a multi-page document continues the numbering)
    last_item: int | None = None
    page_number: int | None = None  # printed at the bottom of the page
    page_count: int | None = None
    signed: bool | None = None  # pen marks on the paper (a signature, handwriting); None: not asked (older answers)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Extraction":
        total = data.get("total")
        similarity = data.get("similarity")
        return cls(
            similarity=max(0, min(100, int(similarity))) if isinstance(similarity, int | float) else 0,
            branded=bool(data.get("dunyabunya", data.get("branded", False))),
            rotation=int(data["rotation"]) % 360 if isinstance(data.get("rotation"), int) and data["rotation"] % 90 == 0 else 0,
            doc_number=_text(data.get("doc_number")),
            partner=_text(data.get("partner")),
            phone=_text(data.get("phone")),
            person=_text(data.get("person")),
            total=float(total) if isinstance(total, int | float) else None,
            first_item=_row(data.get("first_item")),
            last_item=_row(data.get("last_item")),
            page_number=_row(data.get("page_number")),
            page_count=_row(data.get("page_count")),
            signed=data["signed"] if isinstance(data.get("signed"), bool) else None,
        )


@dataclass(frozen=True)
class AiResult:
    extraction: Extraction
    model: str
    input_tokens: int | None
    output_tokens: int | None
    seconds: float


def _row(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _text(value: Any) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def build_prompt(documents: Documents) -> str:
    kinds = "\n".join(f"- {kind.title}: {kind.hint or kind.number}" for kind in documents.kinds)
    return (
        "Pictures come from a work chat. Some show a printed dunyabunya business document (a phone photo of paper, "
        "a screenshot, or a small picture inside a chat screenshot); others show anything else. Our document kinds:\n"
        f"{kinds}\n"
        "They are printed on white paper with the dunyabunya logo, a title with the document number, a table of goods "
        "and usually a QR code in the bottom left corner. A long document has several pages: every page repeats the "
        "document number small in the top right corner and its page number at the bottom; the goods rows are numbered "
        "on across pages; the last page ends with the QR code. A handed-over document is signed with a pen. First rate "
        "the similarity. Then, if it is a document, read ONLY the document itself; ignore chat messages, invoice numbers (INV/...) and other text around it. "
        "Copy the fields exactly as printed. If a field is not readable, return null. Never guess digits."
    )


class OpenAIReader:
    def __init__(self, api_key: str, settings: AiSettings, documents: Documents):
        self._key = api_key
        self._settings = settings
        self._prompt = build_prompt(documents)
        self._session: aiohttp.ClientSession | None = None

    @property
    def model(self) -> str:
        return self._settings.model

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._settings.timeout_seconds))
        return self._session

    def request_body(self, jpeg: bytes) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._settings.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": self._prompt},
                        {
                            "type": "input_image",
                            "detail": self._settings.image_detail,
                            "image_url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii"),
                        },
                    ],
                }
            ],
            "text": {"format": {"type": "json_schema", "name": "document", "strict": True, "schema": SCHEMA}},
        }
        if self._settings.reasoning_effort != "none":
            body["reasoning"] = {"effort": self._settings.reasoning_effort}
        return body

    async def read(self, jpeg: bytes) -> AiResult:
        started = time.monotonic()
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        try:
            async with self._client().post(API_URL, json=self.request_body(jpeg), headers=headers) as response:
                raw = await response.text()
                status = response.status
                retry_after = response.headers.get("Retry-After")
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise TransientError(f"OpenAI bilan aloqa yo'q: {type(exc).__name__}: {exc}") from None

        if status == 429 or status >= 500:
            raise TransientError(f"OpenAI HTTP {status}", retry_after=_seconds(retry_after))
        if status in (401, 403):
            raise TransientError(f"OpenAI kaliti rad etildi (HTTP {status}); OPENAI_API_KEY ni tekshiring", retry_after=600)
        if status >= 400:
            raise AiRejected(f"OpenAI HTTP {status}: {_error_message(raw)}")

        try:
            payload = json.loads(raw)
        except ValueError:
            raise TransientError(f"OpenAI javobi o'qilmadi (HTTP {status})") from None
        extraction = Extraction.from_dict(_output_json(payload))
        usage = payload.get("usage") or {}
        return AiResult(
            extraction=extraction,
            model=str(payload.get("model") or self._settings.model),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            seconds=round(time.monotonic() - started, 2),
        )


def _seconds(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _error_message(raw: str) -> str:
    try:
        return str(json.loads(raw)["error"]["message"])[:300]
    except (ValueError, KeyError, TypeError):
        return raw[:300]


def _output_json(payload: dict[str, Any]) -> dict[str, Any]:
    for item in payload.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") == "refusal":
                raise AiRejected(f"OpenAI rad etdi: {str(part.get('refusal'))[:200]}")
            if part.get("type") == "output_text":
                try:
                    return json.loads(part["text"])
                except (ValueError, KeyError) as exc:
                    raise AiRejected(f"OpenAI javobi JSON emas: {exc}") from None
    raise AiRejected(f"OpenAI javobida matn yo'q (status {payload.get('status')})")
