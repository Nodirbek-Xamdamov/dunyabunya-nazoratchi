"""
Pytest konfiguratsiyasi: haqiqiy Telegram/PostgreSQL'ga bog'lanmasdan, mock va vaqtinchalik
SQLite baza bilan test muhitini tayyorlaydi. Bu fayldagi environ o'zgaruvchilari har qanday
`app.*` moduli import qilinishidan OLDIN o'rnatilishi shart (pytest buni avtomatik
ta'minlaydi -- conftest.py har doim testlardan oldin yuklanadi).
"""

import base64
import os
import shutil
import uuid
from pathlib import Path

TEST_DIR = Path(__file__).parent
_TEST_DB_PATH = TEST_DIR / f"_test_{uuid.uuid4().hex}.db"
_TMP_PDF_DIR = TEST_DIR / "_tmp_pdfs"
_TMP_LOG_DIR = TEST_DIR / "_tmp_webhook_logs"

os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TEST_DB_PATH}")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("BOT_TOKEN", "123456789:AAEaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("BOT_USERNAME", "@test_bot")
os.environ.setdefault("TELEGRAM_API_ID", "12345")
os.environ.setdefault("TELEGRAM_API_HASH", "test_api_hash")
os.environ.setdefault("PDF_STORAGE_DIR", str(_TMP_PDF_DIR))
os.environ.setdefault("WEBHOOK_LOG_DIR", str(_TMP_LOG_DIR))
# Loyiha `.env`ida lokal model (ollama) yoqilgan bo'lsa ham testlar haqiqiy modelga
# so'rov yubormasligi kerak -- LLM chaqiruvlari testlarda alohida soxtalashtiriladi.
os.environ["GROUP_AI_PROVIDER"] = "openai"
os.environ["OPENAI_API_KEY"] = ""
# Nutq modeli ham testlarda yuklanmaydi (kerakli testlar `transcribe_file`ni soxtalashtiradi)
os.environ["GROUP_STT_ENABLED"] = "false"
# Loyiha `.env`ida nima yoqilgan bo'lishidan qat'i nazar testlar standart sozlamalardan boshlaydi;
# kerakli test o'ziga kerak sozlamani `monkeypatch` bilan yoqadi.
os.environ["GROUP_STT_PROVIDER"] = "local"
os.environ["GROUP_MEDIA_AI_PROVIDER"] = ""
os.environ["GROUP_IMAGE_ANALYSIS_ENABLED"] = "false"
os.environ["GROUP_ALERT_MODE"] = "dm"
os.environ["HUB_TOPIC_IDS"] = ""
os.environ["REPORT_TIMEZONE"] = "Asia/Tashkent"
os.environ["DAILY_REPORT_DIR"] = str(TEST_DIR / "_tmp_reports")

import pytest
import pytest_asyncio

from app import group_registry
from app.database import Base, engine


@pytest_asyncio.fixture(autouse=True)
async def reset_db():
    """Har bir test uchun toza baza jadvallarini ta'minlaydi."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    group_registry.clear_caches()  # guruh/menejer keshi bazadan olingan -- baza bilan birga tozalanadi
    yield


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_artifacts():
    yield
    for path in (_TEST_DB_PATH, _TMP_PDF_DIR, _TMP_LOG_DIR, TEST_DIR / "_tmp_reports"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink(missing_ok=True)


# Haqiqiy (minimal) PDF bayt ketma-ketligi -- testlarda diskka yozilib, o'qilishi mumkin
_MINIMAL_PDF_BYTES = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
)


@pytest.fixture
def sample_pdf_base64() -> str:
    return base64.b64encode(_MINIMAL_PDF_BYTES).decode()
