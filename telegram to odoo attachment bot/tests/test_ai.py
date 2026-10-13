"""OpenAIReader against a local HTTP server that answers like the Responses API."""

import json
from pathlib import Path

import pytest
import yaml
from aiohttp import web

from docbot import ai
from docbot.ai import AiRejected, OpenAIReader
from docbot.config import AiSettings, DocumentsConfig
from docbot.documents import Documents
from docbot.errors import TransientError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def documents() -> Documents:
    raw = yaml.safe_load((PROJECT_ROOT / "config" / "documents.yaml").read_text(encoding="utf-8"))
    return Documents(DocumentsConfig.model_validate(raw))


def answer(fields: dict) -> dict:
    return {
        "model": "gpt-5.4-mini-2026-03-17",
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(fields)}]},
        ],
        "usage": {"input_tokens": 962, "output_tokens": 521},
    }


@pytest.fixture
async def api(monkeypatch, unused_tcp_port_factory):
    """Start a fake API; ``api.reply`` sets the next (status, body, headers)."""
    state = {"reply": (200, answer({}), {}), "requests": []}

    async def handle(request: web.Request) -> web.Response:
        state["requests"].append((dict(request.headers), await request.json()))
        status, body, headers = state["reply"]
        return web.Response(status=status, text=json.dumps(body) if not isinstance(body, str) else body, headers=headers)

    app = web.Application()
    app.router.add_post("/v1/responses", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = unused_tcp_port_factory()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    monkeypatch.setattr(ai, "API_URL", f"http://127.0.0.1:{port}/v1/responses")
    yield state
    await runner.cleanup()


@pytest.fixture
async def reader():
    r = OpenAIReader("sk-test-key-0123456789", AiSettings(), documents())
    yield r
    await r.close()


async def test_a_reading_is_parsed_with_its_usage(api, reader):
    api["reply"] = (200, answer({
        "similarity": 88, "dunyabunya": True, "doc_number": " S81878 ", "partner": "Dilorhod 6161", "phone": None,
        "person": "MIRKAMOLYOV BOTIR", "total": 1780000,
    }), {})
    result = await reader.read(b"\xff\xd8\xff jpeg")
    assert result.extraction.doc_number == "S81878"
    assert result.extraction.total == 1780000.0 and result.extraction.phone is None
    assert result.extraction.similarity == 88 and result.extraction.branded is True
    assert (result.model, result.input_tokens, result.output_tokens) == ("gpt-5.4-mini-2026-03-17", 962, 521)

    headers, body = api["requests"][0]
    assert headers["Authorization"] == "Bearer sk-test-key-0123456789"
    assert body["model"] == "gpt-5.4-mini" and body["reasoning"] == {"effort": "low"}
    assert body["text"]["format"]["strict"] is True
    assert "similarity" in body["text"]["format"]["schema"]["required"]
    content = body["input"][0]["content"]
    assert "Sotuv buyurtmasi" in content[0]["text"] and "INV/" in content[0]["text"]
    assert content[1]["image_url"].startswith("data:image/jpeg;base64,") and content[1]["detail"] == "high"


async def test_rate_limits_and_server_errors_are_retried(api, reader):
    api["reply"] = (429, {"error": {"message": "slow down"}}, {"Retry-After": "7"})
    with pytest.raises(TransientError) as info:
        await reader.read(b"x")
    assert info.value.retry_after == 7.0
    api["reply"] = (503, "busy", {})
    with pytest.raises(TransientError, match="HTTP 503"):
        await reader.read(b"x")


async def test_a_rejected_key_waits_for_someone_to_fix_it(api, reader):
    api["reply"] = (401, {"error": {"message": "Incorrect API key"}}, {})
    with pytest.raises(TransientError, match="OPENAI_API_KEY") as info:
        await reader.read(b"x")
    assert info.value.retry_after == 600


async def test_a_refused_picture_goes_to_the_sender(api, reader):
    api["reply"] = (400, {"error": {"message": "Invalid image"}}, {})
    with pytest.raises(AiRejected, match="Invalid image"):
        await reader.read(b"x")
    refusal = {"output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]}
    api["reply"] = (200, refusal, {})
    with pytest.raises(AiRejected, match="rad etdi"):
        await reader.read(b"x")


async def test_an_unreachable_api_is_transient(monkeypatch, reader, unused_tcp_port):
    monkeypatch.setattr(ai, "API_URL", f"http://127.0.0.1:{unused_tcp_port}/v1/responses")
    with pytest.raises(TransientError, match="aloqa yo'q"):
        await reader.read(b"x")
