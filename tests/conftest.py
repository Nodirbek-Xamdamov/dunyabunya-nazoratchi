"""
Shared fixtures for all tests.
"""
import os
import sys

# Add parent directory to path so we can import app modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set test environment variables before importing any app modules
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:test-fake-token-not-real")  # aiogram token-shape uchun
os.environ.setdefault("TELEGRAM_GROUP_ID", "-100123456")
os.environ.setdefault("ODOO_URL", "http://localhost:8069")
os.environ.setdefault("ODOO_DB", "test_db")
os.environ.setdefault("ODOO_LOGIN", "test@test.com")
os.environ.setdefault("ODOO_API_KEY", "test-api-key-123")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test_db")
os.environ.setdefault("ENCRYPTION_KEY", "CzhWj-NmIOcrZ2uA919JFExdhfeQxA3ERgZ9SxL14Pw=")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")  # Use DB 15 for tests

import pytest


@pytest.fixture
def mock_odoo_response():
    """Factory fixture for mock Odoo RPC responses."""
    def _make(records=None, count=0):
        return records or []
    return _make


@pytest.fixture(autouse=True)
def _fresh_odoo_tool_caches(monkeypatch):
    """odoo_tools maydon sxemasi va "ombor mahsuloti" filtrini jarayon ichida keshlaydi —
    bir testning mok'langan Odoo javobi keyingisiga o'tib ketmasligi uchun har testda tozalanadi."""
    from app.services import odoo_tools
    monkeypatch.setattr(odoo_tools, "_fields_meta_cache", {})
    monkeypatch.setattr(odoo_tools, "_storable_domain_cache", None)


@pytest.fixture(autouse=True)
def _isolated_metrics(tmp_path, monkeypatch):
    """Hisoblagichlar testlarda haqiqiy storage/metrics.json ga tegmaydi."""
    from app import metrics
    monkeypatch.setattr(metrics, "_PATH", tmp_path / "metrics.json")
    monkeypatch.setattr(metrics, "_days", {})
    monkeypatch.setattr(metrics, "_loaded", True)
    monkeypatch.setattr(metrics, "_dirty", False)


@pytest.fixture(autouse=True)
def _isolated_ceo_state(tmp_path, monkeypatch):
    """ceo_state.is_ceo() CEO tanilganda ID'ni fonda faylga yozadi — testlar
    haqiqiy storage/ceo_state.json'ni ifloslantirmasligi uchun (ilgari
    "real_ceo": 555 shunday yozilib qolgan edi) har test o'z vaqtinchalik faylini oladi."""
    from app import ceo_state
    monkeypatch.setattr(ceo_state, "_PATH", tmp_path / "ceo_state.json")
    monkeypatch.setattr(ceo_state, "_ids", {})
    monkeypatch.setattr(ceo_state, "_loaded", False)
