"""app.monitor_report -- kunlik Excel hisobot: raqamlar koddan, xulosa modeldan, fayl tuzilishi,
yuborish va qayta urinish, yuborilmagan kunlarni yetkazib olish."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramNetworkError
from openpyxl import load_workbook

from app import llm_client, monitor_db, monitor_report, monitor_sla
from app.llm_client import LLMUnavailable
from app.monitor_db import DailySummary
from tests.monitor_helpers import ADMIN_ID, CHAT, DAY, OTHER_CHAT, add_group, add_message, all_rows, classify

REPORT_CHAT = -1009990000001


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path):
    settings = monitor_report.settings
    monkeypatch.setattr(settings, "REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "REPORT_CHAT_ID", REPORT_CHAT)
    monkeypatch.setattr(settings, "REPORT_THREAD_ID", None)
    monkeypatch.setattr(settings, "ADMIN_CHAT_ID", 555)
    monkeypatch.setattr(settings, "SLA_MINUTES", 5)
    monkeypatch.setattr(monitor_report, "_SEND_RETRY_DELAYS", (0, 0, 0))


def _fake_model(monkeypatch):
    """Model har so'rovga so'rovning birinchi qatorini (toifa yoki guruh nomi) qaytaradi -- qaysi
    toifa/guruh uchun chaqirilgani ko'rinadi."""
    async def request(system_prompt, user_text, schema, name):
        first_line = user_text.splitlines()[0]
        if name == "group_summary":
            categories = schema["properties"]["toifalar"]["required"]
            return {"toifalar": {c: f"AI [{first_line}] {c}" for c in categories}, "umumiy": f"AI [{first_line}]"}
        return {"summary": f"AI xulosa [{first_line}]"}

    mock = AsyncMock(side_effect=request)
    monkeypatch.setattr(llm_client, "request_json", mock)
    return mock


def _calls(model, name):
    return [call for call in model.await_args_list if call.args[3] == name]


async def _seed_day():
    await add_group()
    await classify(await add_message(1, "Uch kundan beri kutyapman"), "Dastavka va Logistika", "Shikoyat",
                   summary="Mijoz yetkazib berish kechikkanidan norozi")
    await classify(await add_message(2, "Qachon keladi?"), "Dastavka va Logistika", "Neytral")
    await classify(await add_message(3, "Sement qanchadan?"), "Narx va To'lovlar", "Neytral")
    await classify(await add_message(4, "Rahmat, yetib keldi"), "Ijobiy Fikrlar", "Ijobiy")
    await classify(await add_message(5, "ok"), related=False)
    # Javobsiz qolgan so'rov
    request = await add_message(6, "Armatura bormi?", minutes=60)
    await classify(request, "Boshqa", "Neytral", needs_manager=True, summary="Mijoz armatura bor-yo'qligini so'radi")
    await monitor_sla.open_checks([request])
    # Undan oldingi kun: 3 ta shikoyat
    yesterday = await monitor_db.compute_day_stats(DAY - timedelta(days=1))
    yesterday.categories[0].total = yesterday.categories[0].complaints = 3
    await monitor_db.save_day_stats(yesterday)


async def test_numbers_come_from_code_and_model_only_writes_text(monkeypatch):
    await _seed_day()
    model = _fake_model(monkeypatch)
    report = await monitor_report.build_report(DAY)

    assert (report.stats.total, report.stats.complaints, report.stats.missed, report.stats.filtered) == (5, 1, 1, 1)
    # Xabari yo'q toifa uchun model chaqirilmaydi: xabari bor 4 ta toifa + umumiy xulosa, guruhga esa bitta so'rov
    assert len(_calls(model, "summary")) == 5 and len(_calls(model, "group_summary")) == 1
    assert report.category_summaries["Dastavka va Logistika"] == "AI xulosa [Toifa: Dastavka va Logistika]"
    assert report.category_summaries["Komplektatsiya va Hajm"] == "Bugun bu toifada xabar bo'lmadi"
    assert list(report.category_summaries) == list(monitor_db.CATEGORIES)

    # Modelga tayyor raqamlar va xabarlar mazmuni beriladi
    delivery_prompt = model.await_args_list[0].args[1]
    assert "jami 2 ta xabar: shikoyat 1, ijobiy 0, neytral 1" in delivery_prompt
    assert "- Chilonzor obyekti | Shikoyat | Mijoz yetkazib berish kechikkanidan norozi" in delivery_prompt

    # Solishtirish va javobsiz so'rovlar jumlalarini kod yozadi (kecha 3 ta shikoyat, bugun 1 ta)
    assert report.overall_summary.startswith("AI xulosa [Sana: 2026-yil 13-oktabr, seshanba]")
    assert "2 taga kamaydi: oldingi kuni 3 ta, shu kuni 1 ta." in report.overall_summary
    assert "Javobsiz qolgan so'rovlar: 1 ta" in report.overall_summary
    assert [(m.customer, m.summary, m.manager) for m in report.missed] == [
        ("Vali Mijoz (@vali)", "Mijoz armatura bor-yo'qligini so'radi", "@menejer_bek")
    ]

    # Kunlik sonlar va xulosalar bazada qoladi (oylik taqqoslash uchun)
    summaries = {row.category: row.summary_text for row in await all_rows(DailySummary) if row.date == DAY}
    assert set(summaries) == set(monitor_db.CATEGORIES) | {"umumiy"}
    assert await monitor_db.stored_complaints(DAY) == 1


@pytest.mark.parametrize("today, previous, expected", [
    (5, 3, "2 taga ko'paydi: oldingi kuni 3 ta, shu kuni 5 ta."),
    (4, 4, "o'zgarmadi: 4 ta."),
    (2, None, "solishtirish uchun ma'lumot yo'q"),
])
def test_comparison_sentence(today, previous, expected):
    stats = monitor_db.DayStats(DAY, [monitor_db.CategoryStat("Boshqa", today, today, 0, 0)])
    text = monitor_report.comparison_sentences(stats, previous)
    assert expected in text and "Barcha so'rovlarga 5 daqiqa ichida javob berilgan." in text


async def test_report_is_built_without_model_and_pending_messages_are_classified_first(monkeypatch):
    await add_group()
    await add_message(1, "👍")  # tasniflanmagan: hisobotdan oldin qoida bilan ajratiladi
    await add_message(2, "Sement qanchadan?")
    monkeypatch.setattr(llm_client, "request_json", AsyncMock(side_effect=LLMUnavailable("o'chiq")))

    report = await monitor_report.build_report(DAY)
    assert (report.stats.filtered, report.stats.unclassified, report.stats.total) == (1, 1, 0)
    assert "kompaniyaga oid mijoz xabari bo'lmadi" in report.overall_summary


def _texts(sheet, column="A"):
    return [cell.value for cell in sheet[column] if cell.value]


def _row(sheet, row, columns="ABCDEFGHI"):
    return [sheet[f"{column}{row}"].value for column in columns]


def _find(sheet, text) -> int:
    return next(cell.row for cell in sheet["A"] if cell.value == text)


async def test_excel_overall_sheet_has_stats_summaries_and_missed_requests_below(monkeypatch):
    await _seed_day()
    _fake_model(monkeypatch)
    report = await monitor_report.build_report(DAY)
    path = monitor_report.write_excel(report)

    assert path.name == "hisobot_2026-10-13.xlsx"
    workbook = load_workbook(path)
    assert workbook.sheetnames == ["Umumiy", "Chilonzor obyekti"]
    sheet = workbook["Umumiy"]
    assert sheet["A1"].value == "Umumiy (barcha guruhlar) — 2026-yil 13-oktabr, seshanba"
    rows = [[cell.value for cell in row[:6]] for row in sheet.iter_rows(min_row=3, max_row=14)]
    assert rows[0][0] == "Toifalar jadvali"
    assert rows[1] == ["Toifa", "Jami", "Shikoyat", "Ijobiy", "Neytral", "Ulush"]
    assert rows[2] == ["Dastavka va Logistika", 2, 1, 0, 1, 0.4]
    assert [row[0] for row in rows[2:10]] == list(monitor_db.CATEGORIES)
    assert rows[10] == ["Jami kompaniyaga oid", 5, 1, 1, 3, 1]
    assert rows[11] == ["Kompaniyaga aloqasiz (filtrlangan)", 1, None, None, None, None]
    assert sheet["A4"].font.bold and sheet["A13"].font.bold and sheet["F5"].number_format == "0%"
    assert all(row[1] == row[2] + row[3] + row[4] for row in rows[2:11])

    texts = _texts(sheet)
    for index, category in enumerate(monitor_db.CATEGORIES, start=1):
        heading = f"{index}) {category} — umumiy xulosa"
        assert texts[texts.index(heading) + 1] == report.category_summaries[category]
    assert texts[texts.index("Umumiy xulosa") + 1] == report.overall_summary
    summary_cell = sheet.cell(row=17, column=1)
    assert summary_cell.alignment.wrap_text and "A17:F17" in [str(r) for r in sheet.merged_cells.ranges]

    # Umumiy xulosa ostida -- hamma guruhlarning javobsiz so'rovlari
    start = _find(sheet, "Javobsiz so'rovlar (1 ta)")
    assert start > _find(sheet, "Umumiy xulosa")
    assert _row(sheet, start + 1) == [
        "Guruh", "Vaqt", "Mijoz", None, "So'rov mazmuni", None, None, "Keyin kimdir javob berdimi", "Menejer",
    ]
    assert _row(sheet, start + 2) == [
        "Chilonzor obyekti", "11:00", "Vali Mijoz (@vali)", None, "Mijoz armatura bor-yo'qligini so'radi", None, None,
        "Yo'q", "@menejer_bek",
    ]


async def test_each_group_gets_its_own_sheet_with_its_report_and_missed_requests(monkeypatch):
    await _seed_day()  # "Chilonzor obyekti": 5 ta xabar, 1 ta javobsiz so'rov
    await add_group(OTHER_CHAT, "Yunusobod: ombor [2]", managers=())
    await classify(await add_message(1, "Rahmat!", chat_id=OTHER_CHAT), "Ijobiy Fikrlar", "Ijobiy")
    request = await add_message(2, "Hisob-faktura qachon?", chat_id=OTHER_CHAT, minutes=120)
    await classify(request, "Narx va To'lovlar", needs_manager=True, summary="Mijoz hisob-faktura so'radi")
    await monitor_sla.open_checks([request])
    await add_message(3, "Ertaga yuboramiz", chat_id=OTHER_CHAT, sender_id=ADMIN_ID, name="Ali", username="ali",
                      is_staff=True, minutes=150)
    await add_group(-1001000000003, "Sergeli (xabarsiz)")
    model = _fake_model(monkeypatch)

    report = await monitor_report.build_report(DAY)
    # Xabari yo'q guruh uchun model chaqirilmaydi (guruhlar parallel tayyorlanadi -- tartib har xil)
    assert sorted(call.args[1].splitlines()[0] for call in _calls(model, "group_summary")) == [
        "Guruh: Chilonzor obyekti", "Guruh: Yunusobod: ombor [2]",
    ]
    assert [group.title for group in report.groups] == ["Chilonzor obyekti", "Sergeli (xabarsiz)", "Yunusobod: ombor [2]"]
    workbook = load_workbook(monitor_report.write_excel(report))
    # Guruhlar alifbo tartibida, varaq nomida Excel taqiqlagan belgilar yo'q
    assert workbook.sheetnames == ["Umumiy", "Chilonzor obyekti", "Sergeli (xabarsiz)", "Yunusobod- ombor -2-"]

    sheet = workbook["Yunusobod- ombor -2-"]
    assert sheet["A1"].value == "Yunusobod: ombor [2] — 2026-yil 13-oktabr, seshanba"
    rows = {row[0]: row[1:6] for row in sheet.iter_rows(min_row=5, max_row=14, max_col=6, values_only=True)}
    assert rows["Ijobiy Fikrlar"] == (1, 0, 1, 0, 0.5) and rows["Jami kompaniyaga oid"] == (2, 0, 1, 1, 1)
    texts = _texts(sheet)
    assert texts[texts.index("5) Narx va To'lovlar — umumiy xulosa") + 1] == "AI [Guruh: Yunusobod: ombor [2]] Narx va To'lovlar"
    assert texts[texts.index("1) Dastavka va Logistika — umumiy xulosa") + 1] == "Bugun bu toifada xabar bo'lmadi"
    overall = texts[texts.index("Guruh bo'yicha xulosa") + 1]
    assert overall.startswith("AI [Guruh: Yunusobod: ombor [2]]") and "Javobsiz qolgan so'rovlar: 1 ta" in overall
    start = _find(sheet, "Javobsiz so'rovlar (1 ta)")
    assert _row(sheet, start + 1, "ABCDEFGH") == [
        "Mijoz", "Vaqt", "So'rov mazmuni", None, None, None, "Keyin kimdir javob berdimi", "Menejer",
    ]
    assert _row(sheet, start + 2, "ABCDEFGH") == [
        "Vali Mijoz (@vali)", "12:00", "Mijoz hisob-faktura so'radi", None, None, None,
        "Ha — Ali (@ali), 30 daqiqadan keyin", "menejer yo'q",
    ]

    # Xabarsiz guruh varag'i ham bor, model chaqirilmagan
    quiet = workbook["Sergeli (xabarsiz)"]
    texts = _texts(quiet)
    assert "kompaniyaga oid mijoz xabari bo'lmadi" in texts[texts.index("Guruh bo'yicha xulosa") + 1]
    assert texts[-1] == "Barcha so'rovlarga 5 daqiqada javob berilgan"

    # Umumiy varaqda ikkala guruhning javobsiz so'rovi
    start = _find(workbook["Umumiy"], "Javobsiz so'rovlar (2 ta)")
    assert [workbook["Umumiy"][f"A{start + offset}"].value for offset in (2, 3)] == [
        "Chilonzor obyekti", "Yunusobod: ombor [2]",
    ]


async def test_missed_list_says_all_answered_and_shows_groups_without_manager(monkeypatch):
    _fake_model(monkeypatch)
    report = await monitor_report.build_report(DAY)
    sheet = load_workbook(monitor_report.write_excel(report))["Umumiy"]
    assert _texts(sheet)[-2:] == ["Javobsiz so'rovlar (0 ta)", "Barcha so'rovlarga 5 daqiqada javob berilgan"]

    await add_group(managers=())
    request = await add_message(1, "Sement bormi?")
    await classify(request, "Boshqa", needs_manager=True)
    await monitor_sla.open_checks([request])
    await monitor_db.resolve_checks(datetime.now(monitor_db.local_tz()))
    report = await monitor_report.build_report(DAY)
    workbook = load_workbook(monitor_report.write_excel(report))
    heading = "Javobsiz so'rovlar (1 ta)"
    sheet = workbook["Umumiy"]
    assert sheet[f"I{_find(sheet, heading) + 2}"].value == "menejer yo'q"
    group_sheet = workbook["Chilonzor obyekti"]
    assert group_sheet[f"H{_find(group_sheet, heading) + 2}"].value == "menejer yo'q"


@pytest.mark.parametrize("titles, expected", [
    (["Ombor/2024: *yangi*?"], ["Ombor-2024- -yangi--"]),
    (["A" * 40], ["A" * 31]),
    (["Guruh", "guruh", "GURUH"], ["Guruh", "guruh (2)", "GURUH (3)"]),
    (["Umumiy"], ["Umumiy (2)"]),
    (["'[]'", "History"], ["--", "History guruhi"]),
])
def test_sheet_names_are_valid_and_unique(titles, expected):
    used = {"umumiy"}
    names = [monitor_report.sheet_name(title, used) for title in titles]
    assert names == expected and all(len(name) <= 31 for name in names)


async def test_removed_group_without_messages_gets_no_sheet(monkeypatch):
    await add_group(CHAT, "Chilonzor obyekti")
    await add_group(OTHER_CHAT, "Eski guruh")
    await monitor_db.set_group_present(OTHER_CHAT, False)
    assert await monitor_db.report_groups(DAY) == [(CHAT, "Chilonzor obyekti")]

    await add_message(1, "Salom", chat_id=OTHER_CHAT)  # shu kuni xabari bor -- varaq oladi
    await monitor_db.set_group_present(OTHER_CHAT, False)
    assert [title for _chat, title in await monitor_db.report_groups(DAY)] == ["Chilonzor obyekti", "Eski guruh"]
    assert await monitor_db.report_groups(DAY + timedelta(days=1)) == [(CHAT, "Chilonzor obyekti")]


async def test_group_comparison_uses_its_own_previous_day(monkeypatch):
    _fake_model(monkeypatch)
    await add_group()
    await classify(await add_message(1, "Kechikdi", minutes=-24 * 60), "Dastavka va Logistika", "Shikoyat")
    await classify(await add_message(2, "Yana kechikdi", minutes=-24 * 60 + 1), "Dastavka va Logistika", "Shikoyat")
    await classify(await add_message(3, "Rahmat"), "Ijobiy Fikrlar", "Ijobiy")
    await add_group(OTHER_CHAT, "Yangi guruh")
    await classify(await add_message(1, "Salom", chat_id=OTHER_CHAT), "Boshqa")

    groups = {group.title: group for group in (await monitor_report.build_report(DAY)).groups}
    assert "2 taga kamaydi: oldingi kuni 2 ta, shu kuni 0 ta." in groups["Chilonzor obyekti"].overall_summary
    # Guruh shu kuni qo'shilgan -- oldingi kun bilan solishtirib bo'lmaydi
    assert "solishtirish uchun ma'lumot yo'q" in groups["Yangi guruh"].overall_summary


async def test_group_summary_falls_back_to_numbers_when_model_fails(monkeypatch):
    await add_group()
    await classify(await add_message(1, "Kechikdi"), "Dastavka va Logistika", "Shikoyat")
    monkeypatch.setattr(llm_client, "request_json", AsyncMock(side_effect=LLMUnavailable("o'chiq")))

    (group,) = (await monitor_report.build_report(DAY)).groups
    assert group.category_summaries["Dastavka va Logistika"].startswith(
        "Bu toifada jami 1 ta xabar: shikoyat 1, ijobiy 0, neytral 0.")
    assert group.overall_summary.startswith("Shu kuni kompaniyaga oid jami 1 ta xabar")


def _report():
    stats = monitor_db.DayStats(DAY, [monitor_db.CategoryStat("Boshqa", 7, 2, 1, 4)], missed=3)
    return monitor_report.Report(DAY, stats, overall_summary="x")


async def test_report_goes_to_general_topic_with_short_caption(monkeypatch, tmp_path):
    path = tmp_path / "hisobot_2026-10-13.xlsx"
    path.write_bytes(b"x")
    bot = MagicMock()
    bot.send_document = AsyncMock()

    assert await monitor_report.send_report(bot, path, _report()) is True
    call = bot.send_document.await_args
    assert call.args[0] == REPORT_CHAT and call.args[1].filename == "hisobot_2026-10-13.xlsx"
    assert call.kwargs["message_thread_id"] is None  # General: topik ID'si berilmaydi
    assert call.kwargs["caption"] == (
        "📊 Kunlik hisobot — 2026-10-13\nJami (kompaniyaga oid): 7\nShikoyatlar: 2\nJavobsiz so'rovlar: 3"
    )

    monkeypatch.setattr(monitor_report.settings, "REPORT_THREAD_ID", 42)
    await monitor_report.send_report(bot, path, _report())
    assert bot.send_document.await_args.kwargs["message_thread_id"] == 42


async def test_failed_send_is_retried_then_reported_to_admin(tmp_path):
    path = tmp_path / "hisobot.xlsx"
    path.write_bytes(b"x")
    bot = MagicMock()
    error = TelegramNetworkError(method=MagicMock(), message="timeout")
    bot.send_document = AsyncMock(side_effect=[error, None])
    bot.send_message = AsyncMock()
    assert await monitor_report.send_report(bot, path, _report()) is True  # ikkinchi urinishda ketdi
    bot.send_message.assert_not_called()

    bot.send_document = AsyncMock(side_effect=error)
    assert await monitor_report.send_report(bot, path, _report()) is False
    assert bot.send_document.await_count == 3
    assert bot.send_message.await_args.args[0] == 555 and "2026-10-13" in bot.send_message.await_args.args[1]


async def test_unsent_days_are_sent_oldest_first_and_only_once(monkeypatch):
    _fake_model(monkeypatch)
    await add_group()
    await add_message(1, "Sement bormi?", minutes=-48 * 60)  # DAY - 2
    await add_message(2, "Sement bormi?")  # DAY
    bot = MagicMock()
    bot.send_document = AsyncMock()
    today = DAY + timedelta(days=1)

    await monitor_report.send_unsent_reports(bot, today, include_yesterday=False)
    names = [call.args[1].filename for call in bot.send_document.await_args_list]
    assert names == ["hisobot_2026-10-11.xlsx", "hisobot_2026-10-13.xlsx"]

    await monitor_report.send_unsent_reports(bot, today, include_yesterday=True)
    assert bot.send_document.await_count == 2  # hammasi yuborilgan

    # Yarim tunda: kecha bitta ham xabar bo'lmagan bo'lsa ham hisobot ketadi
    await monitor_report.send_unsent_reports(bot, today + timedelta(days=1), include_yesterday=True)
    assert bot.send_document.await_args.args[1].filename == "hisobot_2026-10-14.xlsx"


async def test_failed_send_leaves_day_unsent_for_the_next_try(monkeypatch):
    _fake_model(monkeypatch)
    await add_group()
    await add_message(1, "Sement bormi?")
    bot = MagicMock()
    bot.send_document = AsyncMock(side_effect=TelegramNetworkError(method=MagicMock(), message="timeout"))
    bot.send_message = AsyncMock()
    today = DAY + timedelta(days=1)

    await monitor_report.send_unsent_reports(bot, today, include_yesterday=True)
    assert await monitor_db.unsent_days(today) == [DAY]


def test_seconds_until_report_time(monkeypatch):
    tz = monitor_db.local_tz()
    assert monitor_report.seconds_until_report(datetime(2026, 10, 13, 23, 59, tzinfo=tz)) == 60
    assert monitor_report.seconds_until_report(datetime(2026, 10, 13, 0, 0, tzinfo=tz)) == 24 * 3600

    monkeypatch.setattr(monitor_report.settings, "REPORT_TIME", "11:00")
    assert monitor_report.seconds_until_report(datetime(2026, 10, 13, 19, 0, tzinfo=tz)) == 16 * 3600
    assert monitor_report.seconds_until_report(datetime(2026, 10, 14, 10, 30, tzinfo=tz)) == 30 * 60
    assert monitor_report.seconds_until_report(datetime(2026, 10, 14, 11, 0, tzinfo=tz)) == 24 * 3600


async def test_report_at_eleven_covers_the_24_hours_before_it(monkeypatch):
    """Sinov davri: hisobot 11:00 da ketadi va kechagi 11:00 dan bugungi 11:00 gacha bo'lgan
    xabarlarni qamraydi (kechqurundan ertalabgacha yig'ilganlari bitta hisobotda)."""
    monkeypatch.setattr(monitor_report.settings, "REPORT_TIME", "11:00")
    _fake_model(monkeypatch)
    await add_group()
    # T0 = 13-oktabr 10:00: bu hali 12-oktabrda boshlangan davr
    await classify(await add_message(1, "Ertalabki savol"), "Narx va To'lovlar")
    await classify(await add_message(2, "Kechki shikoyat", minutes=9 * 60), "Dastavka va Logistika", "Shikoyat")  # 19:00
    await classify(await add_message(3, "Tongi savol", minutes=23 * 60), "Boshqa")  # 14-oktabr 09:00
    await classify(await add_message(4, "Tushdan keyingi savol", minutes=26 * 60), "Boshqa")  # 14-oktabr 12:00

    tz = monitor_db.local_tz()
    assert monitor_db.report_day(datetime(2026, 10, 14, 10, 59, tzinfo=tz)) == DAY
    assert monitor_db.report_day(datetime(2026, 10, 14, 11, 0, tzinfo=tz)) == DAY + timedelta(days=1)
    assert await monitor_db.unsent_days(DAY + timedelta(days=1)) == [DAY - timedelta(days=1), DAY]

    report = await monitor_report.build_report(DAY)
    assert (report.stats.total, report.stats.complaints) == (2, 1)  # 19:00 va ertasi 09:00 dagi xabarlar
    workbook = load_workbook(monitor_report.write_excel(report))
    assert "13-oktabr 11:00 dan 14-oktabr 11:00 gacha" in workbook["Umumiy"]["A1"].value
    assert workbook["Chilonzor obyekti"]["A1"].value == (
        "Chilonzor obyekti — 2026-yil 13-oktabr 11:00 dan 14-oktabr 11:00 gacha")
    assert monitor_report.build_caption(report).startswith(
        "📊 Kunlik hisobot — 2026-10-13 11:00 – 2026-10-14 11:00\n")
    assert (await monitor_report.build_report(DAY - timedelta(days=1))).stats.total == 1


async def test_other_category_gets_a_general_summary_without_listing_messages(monkeypatch):
    await add_group()
    await classify(await add_message(1, "Armatura bormi?"), "Boshqa")
    await classify(await add_message(2, "Narxi qancha?"), "Narx va To'lovlar")
    model = _fake_model(monkeypatch)
    await monitor_report.build_report(DAY)

    prompts = {call.args[1].splitlines()[0]: call.args[1] for call in model.await_args_list}
    assert "sanab o'tma" in prompts["Toifa: Boshqa"] and "umumlashtirib yoz" in prompts["Toifa: Boshqa"]
    assert "aniq misol keltir" in prompts["Toifa: Narx va To'lovlar"]
    assert "sanab o'tma" not in prompts["Toifa: Narx va To'lovlar"]
