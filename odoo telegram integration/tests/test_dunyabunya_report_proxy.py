"""`/dunyabunya-reports/reports/{filename}` — dunyabunya_agent (BOSHQA, mustaqil loyiha)ning
HTML hisobot sahifalarini shu allaqachon-ochiq-port orqali proxy qilish (2026-08-20).
Chaqiruv haqiqiy tarmoqqa (localhost:8001) ketmasligi uchun `httpx.AsyncClient` mock qilinadi
— naqsh `test_webhook.py`dagi Telegram-mock uslubiga o'xshash."""
from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def client():
    from app.fastapi_app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class _FakeUpstreamResponse:
    def __init__(self, status_code: int, content: bytes = b""):
        self.status_code = status_code
        self.content = content


class _FakeUpstreamAsyncClient:
    def __init__(self, response: _FakeUpstreamResponse, captured_urls: list[str]):
        self._response = response
        self._captured_urls = captured_urls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url: str):
        self._captured_urls.append(url)
        return self._response


def _patch_upstream(monkeypatch, status_code: int, content: bytes = b""):
    import app.fastapi_app as fastapi_app_module

    captured_urls: list[str] = []
    response = _FakeUpstreamResponse(status_code=status_code, content=content)
    monkeypatch.setattr(
        fastapi_app_module.httpx, "AsyncClient", lambda **kwargs: _FakeUpstreamAsyncClient(response, captured_urls)
    )
    return captured_urls


async def test_proxy_returns_upstream_html_content(client, monkeypatch):
    html = b"<html><body>Sinov hisoboti</body></html>"
    captured_urls = _patch_upstream(monkeypatch, status_code=200, content=html)

    resp = await client.get("/dunyabunya-reports/reports/0123456789abcdef0123456789abcdef.html")

    assert resp.status_code == 200
    assert resp.content == html
    assert resp.headers["content-type"].startswith("text/html")
    assert captured_urls == ["http://localhost:8001/reports/0123456789abcdef0123456789abcdef.html"]


async def test_proxy_returns_404_when_upstream_404s(client, monkeypatch):
    _patch_upstream(monkeypatch, status_code=404)
    resp = await client.get("/dunyabunya-reports/reports/0123456789abcdef0123456789abcdef.html")
    assert resp.status_code == 404


async def test_proxy_returns_502_when_upstream_unreachable(client, monkeypatch):
    import app.fastapi_app as fastapi_app_module

    class _FailingAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def get(self, url: str):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(fastapi_app_module.httpx, "AsyncClient", lambda **kwargs: _FailingAsyncClient())

    resp = await client.get("/dunyabunya-reports/reports/0123456789abcdef0123456789abcdef.html")
    assert resp.status_code == 502


@pytest.mark.parametrize(
    "bad_filename",
    [
        "../../../etc/passwd",
        "not-a-uuid.html",
        "0123456789abcdef0123456789abcdef.txt",
        "0123456789ABCDEF0123456789ABCDEF.html",
    ],
)
async def test_proxy_rejects_filenames_not_matching_expected_shape(client, monkeypatch, bad_filename):
    captured_urls = _patch_upstream(monkeypatch, status_code=200, content=b"should not be reached")
    resp = await client.get(f"/dunyabunya-reports/reports/{bad_filename}")
    assert resp.status_code in (400, 404)  # 404 agar FastAPI yo'lning o'zi mos kelmasa (masalan / bo'lsa)
    assert captured_urls == []  # upstream'ga umuman so'rov yuborilmagan
