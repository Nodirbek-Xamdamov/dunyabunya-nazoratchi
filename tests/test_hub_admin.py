"""app.bot.handlers.hub_admin -- ichki guruhdagi boshqaruv buyruqlari: hisobot, guruhlarni
kuzatuvdan chiqarish/qaytarish, menejerlarni biriktirish."""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import daily_report, group_registry, group_stats
from app.bot.handlers import group_monitor, hub_admin
from app.database import async_session_maker
from app.models import Group

G1, G2 = -1001111, -1002222


@pytest.fixture(autouse=True)
async def _groups(monkeypatch, tmp_path):
    monkeypatch.setattr(hub_admin.settings, "DAILY_REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(group_stats, "_tz", None)
    group_monitor.reset_state()
    async with async_session_maker() as session:
        session.add_all([Group(group_id=G1, title="Chilonzor <obyekt>"), Group(group_id=G2, title="Sergeli obyekt")])
        await session.commit()


async def test_groups_list_and_search():
    text = await hub_admin.groups_text()
    assert "Guruhlar: 2 ta" in text and "Chilonzor &lt;obyekt&gt;" in text and f"<code>{G2}</code>" in text
    assert "Sergeli" in await hub_admin.groups_text("serg") and "Chilonzor" not in await hub_admin.groups_text("serg")
    assert await hub_admin.groups_text("yo'q guruh") == "Guruh topilmadi."


async def test_group_can_be_paused_and_resumed_by_title_or_id():
    assert "endi kuzatilmaydi" in await hub_admin.set_group_active_text("chilon", False)
    assert await group_registry.ensure_group(G1, "Chilonzor <obyekt>") is False
    assert "kuzatilmaydi: 1" in await hub_admin.groups_text()

    assert "kuzatuvga qaytarildi" in await hub_admin.set_group_active_text(str(G1), True)
    assert await group_registry.ensure_group(G1, "Chilonzor <obyekt>") is True


async def test_ambiguous_or_unknown_group_is_not_changed():
    answer = await hub_admin.set_group_active_text("obyekt", False)
    assert "Bir nechta guruh" in answer and str(G1) in answer and str(G2) in answer
    assert "topilmadi" in await hub_admin.set_group_active_text("Yunusobod", False)
    assert "Guruhni ko'rsating" in await hub_admin.set_group_active_text("", False)
    assert await group_registry.ensure_group(G1, "Chilonzor <obyekt>") is True


@pytest.mark.parametrize(
    "arguments, expected",
    [
        ("Sergeli obyekt @Ali_M Ali Valiyev", ("Sergeli obyekt", "@Ali_M", "Ali Valiyev")),
        ("-1002222 @ali", ("-1002222", "@ali", "")),
        ("Sergeli", ("Sergeli", "", "")),
        ("@ali", ("", "@ali", "")),
    ],
)
def test_manager_command_arguments(arguments, expected):
    assert hub_admin.split_manager_args(arguments) == expected


async def test_manager_can_be_added_listed_and_removed():
    assert "menejer biriktirilmagan" in await hub_admin.managers_text("sergeli")
    assert "Hech bir guruhga" in await hub_admin.managers_text()

    assert "biriktirildi" in await hub_admin.add_manager_text("sergeli @Ali_M Ali Valiyev")
    assert "allaqachon" in await hub_admin.add_manager_text(f"{G2} @ali_m")
    assert [(m.username, m.full_name, m.source) for m in await group_registry.list_managers(G2)] == [
        ("ali_m", "Ali Valiyev", "manual")
    ]
    assert "@ali_m (Ali Valiyev) — qo'lda" in await hub_admin.managers_text("sergeli")
    assert "Sergeli obyekt" in await hub_admin.managers_text() and "@ali_m" in await hub_admin.managers_text()

    assert "olib tashlandi" in await hub_admin.remove_manager_text("sergeli @Ali_M")
    assert "menejer emas" in await hub_admin.remove_manager_text("sergeli @Ali_M")
    assert await group_registry.list_managers(G2) == ()


async def test_manager_commands_explain_wrong_usage():
    assert "Yozilishi" in await hub_admin.add_manager_text("sergeli")
    assert "Yozilishi" in await hub_admin.remove_manager_text("")
    assert "topilmadi" in await hub_admin.add_manager_text("Yunusobod @ali")
    assert "Guruhni ko'rsating" in await hub_admin.add_manager_text("@ali")


def test_report_day_argument():
    today = group_stats.local_day()
    assert hub_admin.parse_report_day(None) == today and hub_admin.parse_report_day(" Bugun ") == today
    assert hub_admin.parse_report_day("kecha") == today - timedelta(days=1)
    assert hub_admin.parse_report_day("2026-10-11") == date(2026, 10, 11)
    assert hub_admin.parse_report_day("11.10.2026") is None


async def test_report_command_sends_html_file_into_the_same_topic():
    bot = MagicMock()
    bot.send_document = AsyncMock()
    message = SimpleNamespace(
        bot=bot, chat=SimpleNamespace(id=-100500), message_thread_id=9, is_topic_message=True, reply=AsyncMock()
    )
    await hub_admin.handle_report(message, SimpleNamespace(args="2026-10-11"))

    call = bot.send_document.await_args
    assert call.args[0] == -100500 and call.kwargs["message_thread_id"] == 9
    assert call.args[1].filename == "stats_2026-10-11.html"
    assert daily_report.report_path(date(2026, 10, 11)).exists()
    message.reply.assert_not_called()

    await hub_admin.handle_report(message, SimpleNamespace(args="ertaga"))
    assert "Sana noto'g'ri" in message.reply.await_args.args[0]
