"""Reading the customer name and the order number from a document photo with an OpenAI vision model.

A photo lying on its side is read badly, so the model also says how the picture is turned; such a picture is
turned upright and read once more. The model only reads: its answer is shown to people as text, never treated as
an instruction.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import aiohttp
from PIL import Image, UnidentifiedImageError

from app.config import settings

log = logging.getLogger(__name__)

OPENAI_URL = "https://api.openai.com/v1/responses"
TIMEOUT_SECONDS = 90
# a call that fails for a passing reason is repeated: a reminder without its order number and customer is of little use
ATTEMPTS = 3
RETRY_PAUSE_SECONDS = 3
# a picture is read at most this many times: as sent, and again after each turn towards upright
MAX_READS = 3
# the model's "turned N degrees clockwise" is undone by turning the picture the other way (Pillow turns anticlockwise)
_TURN_BACK = {90: Image.Transpose.ROTATE_90, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_270}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["customer", "order_number", "rotation"],
    "properties": {
        "customer": {
            "type": ["string", "null"],
            "description": "what is written next to the label 'Mijoz' / 'Klient' / 'Клиент' / 'Xaridor' / 'Покупатель'",
        },
        "order_number": {
            "type": ["string", "null"],
            "description": "the order number: next to 'Buyurtma', 'Buyurtma raqami', 'Заказ', 'Order', or in the title",
        },
        "rotation": {
            "type": "integer",
            "enum": [0, 90, 180, 270],
            "description": "0 if the text stands upright, otherwise how many degrees clockwise the picture is turned",
        },
    },
}

PROMPT = (
    "The picture is a phone photo or a screenshot of a sales document from Uzbekistan: an order, an invoice, "
    "a waybill or a reconciliation statement (sverka, akt sverki). "
    "The text is Uzbek (Latin or Cyrillic) or Russian. Read three fields and answer in JSON:\n"
    "- customer: the customer, written next to the label 'Mijoz', 'Klient', 'Клиент', 'Мижоз', 'Xaridor' "
    "or 'Покупатель'. Copy everything written there after the label: a name is often followed by digits or by "
    "a note in brackets, and they belong to it. Do not include the label. The name next to 'Menejer' or "
    "'Менеджер' is the seller's own employee, never the customer.\n"
    "- order_number: the order number, written next to 'Buyurtma', 'Buyurtma raqami', 'Заказ', 'Order', '№', "
    "or in the document title; it is usually the letter S followed by digits. Only the number itself. If the "
    "picture shows several documents, read the one whose number starts with S.\n"
    "- rotation: how the picture is turned: 0 if its text stands upright, otherwise how many degrees clockwise "
    "it is turned (90, 180 or 270).\n"
    "Copy customer and order_number exactly as written, in the original script. If one of them is missing or not "
    "readable, return null for it. Never guess letters or digits."
)

_EMPTY = {"", "null", "none", "n/a", "unknown", "-"}
# the label a model sometimes copies together with the value: "Xaridor: Aliyev Vali"
_LABEL = re.compile(r"^(?:mijoz|klient|xaridor|клиент|мижоз|харидор|покупатель)\s*[:\-–—]+\s*", re.IGNORECASE)


class ExtractError(Exception):
    """The picture could not be read (OpenAI unreachable, HTTP error, unusable answer)."""


class _Passing(ExtractError):
    """A failure worth another try: no connection, a timeout, HTTP 429 or 5xx."""


@dataclass(frozen=True)
class Extraction:
    customer: str | None = None
    order_number: str | None = None
    # how far the model saw the picture turned clockwise; 0 = upright
    rotation: int = 0


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split()).strip(" :№#")
    return None if value.lower() in _EMPTY else value


def parse_answer(raw: str) -> Extraction:
    try:
        data = json.loads(raw)
    except ValueError:
        raise ExtractError(f"model answer is not JSON: {raw[:200]!r}") from None
    if not isinstance(data, dict):
        raise ExtractError(f"model answer is not an object: {raw[:200]!r}")
    customer = _clean(data.get("customer"))
    if customer:
        customer = _clean(_LABEL.sub("", customer))
    rotation = data.get("rotation")
    return Extraction(
        customer=customer, order_number=_clean(data.get("order_number")),
        rotation=rotation if rotation in _TURN_BACK else 0,
    )


def request_body(image: bytes, mime: str, model: str | None = None) -> dict[str, Any]:
    image_url = f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"
    return {
        "model": model or settings.openai_model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": PROMPT},
                    {"type": "input_image", "detail": "high", "image_url": image_url},
                ],
            }
        ],
        "text": {"format": {"type": "json_schema", "name": "document", "strict": True, "schema": SCHEMA}},
        # copying a few fields needs no long thinking
        "reasoning": {"effort": "low"},
    }


def output_text(payload: dict[str, Any]) -> str:
    """The text of a Responses API answer."""
    for item in payload.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") == "output_text":
                return part.get("text") or ""
    raise ExtractError(f"OpenAI answer has no text (status {payload.get('status')})")


def turned_upright(image: bytes, rotation: int) -> bytes | None:
    """The picture turned back by `rotation` degrees, as a JPEG; None when it is not a picture Pillow can open."""
    try:
        picture = Image.open(io.BytesIO(image))
        picture.load()
    except (UnidentifiedImageError, OSError):
        return None
    out = io.BytesIO()
    picture.transpose(_TURN_BACK[rotation]).convert("RGB").save(out, "JPEG", quality=90)
    return out.getvalue()


async def ask(session: aiohttp.ClientSession, body: dict[str, Any]) -> dict[str, Any]:
    """One call to OpenAI; returns the whole answer."""
    if not settings.openai_api_key:
        raise ExtractError("OPENAI_API_KEY is not set")
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    try:
        async with session.post(OPENAI_URL, json=body, headers=headers) as response:
            raw, status = await response.text(), response.status
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise _Passing(f"OpenAI is unreachable: {type(exc).__name__}: {exc}") from None
    if status == 429 or status >= 500:
        raise _Passing(f"OpenAI HTTP {status}: {raw[:300]}")
    if status != 200:
        # a rejected key or a rejected request: repeating it changes nothing
        raise ExtractError(f"OpenAI HTTP {status}: {raw[:300]}")
    try:
        return json.loads(raw)
    except ValueError:
        raise _Passing(f"unexpected OpenAI answer: {raw[:300]}") from None


async def _read_once(session: aiohttp.ClientSession, image: bytes, mime: str, model: str | None) -> Extraction:
    body = request_body(image, mime, model)
    for attempt in range(1, ATTEMPTS + 1):
        started = time.monotonic()
        try:
            payload = await ask(session, body)
        except _Passing as exc:
            if attempt == ATTEMPTS:
                raise
            log.warning("reading the photo failed, attempt %s of %s: %s", attempt, ATTEMPTS, exc)
            await asyncio.sleep(RETRY_PAUSE_SECONDS * attempt)
            continue
        usage = payload.get("usage") or {}
        log.info(
            "photo read by %s in %.1fs, tokens: %s in, %s out", payload.get("model") or body["model"],
            time.monotonic() - started, usage.get("input_tokens"), usage.get("output_tokens"),
        )
        return parse_answer(output_text(payload))
    raise AssertionError("unreachable")


async def read_photo(image: bytes, mime: str = "image/jpeg", model: str | None = None) -> Extraction:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)) as session:
        for reads in range(1, MAX_READS + 1):
            found = await _read_once(session, image, mime, model)
            if not found.rotation or reads == MAX_READS:
                break
            upright = turned_upright(image, found.rotation)
            if upright is None:
                break
            # after a turn the wrong way the model says 180 and the next turn puts it right
            log.info("photo is turned %s degrees: turned back and read again", found.rotation)
            image, mime = upright, "image/jpeg"
    return found
