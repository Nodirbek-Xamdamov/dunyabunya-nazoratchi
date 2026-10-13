"""app.monitor_db -- monitoring bazasi: xabarlarni saqlash, tasnif natijalari, kunlik sonlar, zaxira."""

import sqlite3
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

from app import monitor_db
from app.monitor_db import ClassificationResult, Classification, DailyStat, Message
from tests.monitor_helpers import CHAT, DAY, OTHER_CHAT, T0, add_group, add_message, all_rows, classify, get_message


async def test_sqlite_runs_in_wal_mode_with_foreign_keys_and_busy_timeout():
    async with monitor_db.engine.connect() as connection:
        pragma = lambda name: connection.execute(text(f"PRAGMA {name}"))  # noqa: E731
        assert (await pragma("journal_mode")).scalar() == "wal"
        assert (await pragma("foreign_keys")).scalar() == 1
        assert (await pragma("busy_timeout")).scalar() == 5000


async def test_existing_database_gets_new_columns_on_start():
    """Avvalgi versiyada yaratilgan bazaga keyin qo'shilgan ustunlar bot ishga tushganda qo'shiladi."""
    async with monitor_db.engine.begin() as connection:
        await connection.execute(text("DROP INDEX IF EXISTS ix_messages_sent"))
        await connection.execute(text("ALTER TABLE messages DROP COLUMN transcript"))
        await connection.execute(text("ALTER TABLE messages DROP COLUMN media_duration"))

    await monitor_db.init_db()
    await monitor_db.init_db()  # ikkinchi marta hech narsa qilmaydi

    async with monitor_db.engine.connect() as connection:
        columns = [row[1] for row in await connection.execute(text("PRAGMA table_info(messages)"))]
        indexes = [row[1] for row in await connection.execute(text("PRAGMA index_list(messages)"))]
    assert {"transcript", "media_duration"} <= set(columns) and "ix_messages_sent" in indexes
    await add_group()
    await add_message(1, "Salom")  # yangilangan jadvalga yozish ishlaydi


async def test_message_is_stored_once_and_edit_updates_it():
    await add_group()
    await add_message(7, "Sement qanchadn?")
    edited_at = T0 + timedelta(minutes=1)
    is_new = await monitor_db.save_message(
        chat_id=CHAT, message_id=7, sender_id=200, sender_username="vali", sender_name="Vali Mijoz", is_staff=False,
        text="Sement qanchadan?", media_type="text", file_id=None, reply_to_message_id=None, sent_at=T0,
        edited_at=edited_at,
    )

    assert is_new is False
    (message,) = await all_rows(Message)
    assert (message.text, message.edited_at, message.status) == ("Sement qanchadan?", edited_at, "pending")
    assert message.sent_at_utc == T0 and message.sent_at_utc.tzinfo is not None


async def test_local_date_is_tashkent_day_and_staff_messages_are_not_queued():
    await add_group()
    late_evening_utc = datetime(2026, 10, 13, 19, 30, tzinfo=timezone.utc)  # Toshkentda 14-oktabr 00:30
    await monitor_db.save_message(
        chat_id=CHAT, message_id=1, sender_id=102, sender_username=None, sender_name="Ali", is_staff=True,
        text="Ertaga yetkazamiz", media_type="text", file_id=None, reply_to_message_id=None, sent_at=late_evening_utc,
    )
    (message,) = await all_rows(Message)
    assert message.local_date == date(2026, 10, 14) and message.status == "staff"
    assert await monitor_db.pending_messages(10) == []


async def test_pending_messages_come_grouped_by_chat_in_order_with_conversation_context():
    await add_group()
    await add_group(OTHER_CHAT, "Sergeli")
    await add_message(1, "Salom", is_staff=True, sender_id=102, name="Ali")
    await add_message(2, "Narxi qancha?")
    await add_message(5, "Boshqa guruh", chat_id=OTHER_CHAT)
    await add_message(3, "65 ming", is_staff=True, sender_id=102, name="Ali")
    await add_message(4, "Yetkazasizmi?")

    pending = await monitor_db.pending_messages(10)
    assert [(m.chat_id, m.message_id) for m in pending] == [(OTHER_CHAT, 5), (CHAT, 2), (CHAT, 4)]
    assert [m.message_id for m in await monitor_db.pending_messages(10, DAY + timedelta(days=1))] == []

    # 2..4 oralig'i xodim javobi bilan birga + undan oldingi 1 ta xabar
    conversation = await monitor_db.conversation(CHAT, 2, 4, context=1)
    assert [(m.message_id, m.is_staff) for m in conversation] == [(1, True), (2, False), (3, True), (4, False)]


async def test_message_is_classified_only_once_and_failed_after_max_attempts():
    await add_group()
    first, second = await add_message(1), await add_message(2)
    result = ClassificationResult(first, True, "Narx va To'lovlar", "Neytral", "Narx so'radi", True)
    assert await monitor_db.save_classifications([result], "m1") == [first]
    assert await monitor_db.save_classifications([result], "m2") == []  # qayta tasniflanmaydi
    (classification,) = await all_rows(Classification)
    assert classification.model == "m1"

    for _ in range(3):
        await monitor_db.record_failed_attempt([second], max_attempts=3)
    message = await get_message(CHAT, 2)
    assert (message.status, message.attempts) == ("failed", 3)
    assert await monitor_db.pending_messages(10) == []


async def test_day_stats_are_counted_by_sql_for_customer_messages_only():
    await add_group()
    await classify(await add_message(1), "Dastavka va Logistika", "Shikoyat")
    await classify(await add_message(2), "Dastavka va Logistika", "Neytral")
    await classify(await add_message(3), "Narx va To'lovlar", "Neytral")
    await classify(await add_message(4), "Ijobiy Fikrlar", "Ijobiy")
    await classify(await add_message(5, "ok"), related=False)
    await classify(await add_message(6, "👍"), related=False)
    await add_message(7, "hali tasniflanmagan")
    await add_message(8, "Xodim xabari", is_staff=True, sender_id=102)
    await classify(await add_message(9, minutes=24 * 60), "Boshqa", "Shikoyat")  # ertangi kun

    stats = await monitor_db.compute_day_stats(DAY)
    rows = {row.category: (row.total, row.complaints, row.positive, row.neutral, row.share) for row in stats.categories}
    assert list(rows) == list(monitor_db.CATEGORIES)
    assert rows["Dastavka va Logistika"] == (2, 1, 0, 1, 50)
    assert rows["Narx va To'lovlar"] == (1, 0, 0, 1, 25)
    assert rows["Ijobiy Fikrlar"] == (1, 0, 1, 0, 25)
    assert rows["Boshqa"] == (0, 0, 0, 0, 0)
    assert (stats.total, stats.complaints, stats.positive, stats.neutral) == (4, 1, 1, 2)
    assert (stats.filtered, stats.unclassified) == (2, 1)
    assert all(r.complaints + r.positive + r.neutral == r.total for r in stats.categories)

    await monitor_db.save_day_stats(stats)
    await monitor_db.save_day_stats(stats)  # qayta hisoblansa qatorlar ikkilanmaydi
    stored = {row.category: row for row in await all_rows(DailyStat)}
    assert len(stored) == 11  # 8 toifa + jami + filtrlangan + javobsiz
    assert stored[monitor_db.STAT_TOTAL].total == 4 and stored[monitor_db.STAT_FILTERED].total == 2
    assert await monitor_db.stored_complaints(DAY) == 1
    assert await monitor_db.stored_complaints(DAY - timedelta(days=1)) is None


async def test_empty_day_has_zero_rows_and_no_share():
    stats = await monitor_db.compute_day_stats(DAY)
    assert stats.total == 0 and [row.share for row in stats.categories] == [None] * 8


async def test_category_messages_put_complaints_first():
    await add_group()
    await classify(await add_message(1), "Dastavka va Logistika", "Neytral", summary="Qachon keladi")
    await classify(await add_message(2), "Dastavka va Logistika", "Shikoyat", summary="Kechikdi")
    messages = await monitor_db.category_messages(DAY, "Dastavka va Logistika", 10)
    assert [(m.sentiment, m.summary, m.group) for m in messages] == [
        ("Shikoyat", "Kechikdi", "Chilonzor obyekti"), ("Neytral", "Qachon keladi", "Chilonzor obyekti"),
    ]


async def test_unsent_days_and_report_sent_mark():
    await add_group()
    await add_message(1)  # DAY
    await add_message(2, minutes=-24 * 60)  # DAY - 1
    today = DAY + timedelta(days=1)
    assert await monitor_db.unsent_days(today) == [DAY - timedelta(days=1), DAY]

    await monitor_db.save_summary(DAY, monitor_db.SUMMARY_OVERALL, "xulosa")
    await monitor_db.mark_report_sent(DAY)
    await monitor_db.save_summary(DAY, monitor_db.SUMMARY_OVERALL, "yangi xulosa")  # yuborilgan belgisi saqlanadi
    assert await monitor_db.unsent_days(today) == [DAY - timedelta(days=1)]
    assert await monitor_db.sent_days(today) == {DAY}
    assert await monitor_db.unsent_days(DAY) == [DAY - timedelta(days=1)]  # bugungi kun hech qachon kirmaydi


async def test_backup_copies_database_and_keeps_only_newest(tmp_path):
    await add_group()
    await add_message(1)
    folder = tmp_path / "backups"
    for offset in range(4):
        path = monitor_db.backup_database(str(folder), keep=3, day=DAY + timedelta(days=offset))
    assert sorted(p.name for p in folder.iterdir()) == [
        "monitor_2026-10-14.db", "monitor_2026-10-15.db", "monitor_2026-10-16.db",
    ]
    copy = sqlite3.connect(path)
    assert copy.execute("select count(*) from messages").fetchone() == (1,)
    copy.close()
