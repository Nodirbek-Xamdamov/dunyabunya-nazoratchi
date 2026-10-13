"""P0-2: hisobot havolalari muddatini cheklovchi tozalash mexanizmi
(app/services/reports_cleanup.py) uchun testlar. Haqiqiy REPORTS_DIR'ga
teginmaydi — har test o'z vaqtinchalik papkasida ishlaydi."""
from __future__ import annotations

import time

import pytest

import app.services.reports_cleanup as reports_cleanup


@pytest.fixture(autouse=True)
def _isolated_reports_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(reports_cleanup, "REPORTS_DIR", tmp_path)
    yield


def _make_file(path, *, age_hours: float) -> None:
    path.write_text("x")
    old_time = time.time() - age_hours * 3600
    import os
    os.utime(path, (old_time, old_time))


def test_removes_files_older_than_ttl():
    old_file = reports_cleanup.REPORTS_DIR / "old_report.html"
    _make_file(old_file, age_hours=49)

    removed = reports_cleanup.cleanup_expired_reports(ttl_hours=48)

    assert removed == 1
    assert not old_file.exists()


def test_keeps_files_within_ttl():
    fresh_file = reports_cleanup.REPORTS_DIR / "fresh_report.xlsx"
    _make_file(fresh_file, age_hours=1)

    removed = reports_cleanup.cleanup_expired_reports(ttl_hours=48)

    assert removed == 0
    assert fresh_file.exists()


def test_mixed_old_and_fresh_files():
    old_1 = reports_cleanup.REPORTS_DIR / "old1.pdf"
    old_2 = reports_cleanup.REPORTS_DIR / "old2.html"
    fresh = reports_cleanup.REPORTS_DIR / "fresh.xlsx"
    _make_file(old_1, age_hours=100)
    _make_file(old_2, age_hours=72)
    _make_file(fresh, age_hours=2)

    removed = reports_cleanup.cleanup_expired_reports(ttl_hours=48)

    assert removed == 2
    assert not old_1.exists()
    assert not old_2.exists()
    assert fresh.exists()


def test_missing_directory_returns_zero_no_error(tmp_path, monkeypatch):
    monkeypatch.setattr(reports_cleanup, "REPORTS_DIR", tmp_path / "does_not_exist")
    assert reports_cleanup.cleanup_expired_reports(ttl_hours=48) == 0


def test_uses_configured_ttl_when_not_overridden(monkeypatch):
    monkeypatch.setattr(reports_cleanup.settings, "REPORT_LINK_TTL_HOURS", 1)
    old_file = reports_cleanup.REPORTS_DIR / "old.html"
    _make_file(old_file, age_hours=2)

    removed = reports_cleanup.cleanup_expired_reports()  # ttl_hours berilmagan

    assert removed == 1
    assert not old_file.exists()
