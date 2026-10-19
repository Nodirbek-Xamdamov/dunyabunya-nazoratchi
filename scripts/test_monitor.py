"""
Monitoringni HAQIQIY model bilan, lekin VAQTINCHALIK bazada sinash (jonli bazaga va Telegram'ga
tegmaydi). Loyiha papkasidan:

    .venv\\Scripts\\python.exe scripts\\test_monitor.py            # tasniflash + 5 daqiqa qoidasi + Excel
    .venv\\Scripts\\python.exe scripts\\test_monitor.py --no-sla   # faqat tasniflash va Excel (tez)

1. ~30 ta namuna xabar (o'zbekcha, ruscha, aralash: shikoyat, narx so'rovi, "rahmat", stiker, spam)
   bazaga yoziladi va botdagi AYNAN o'sha tasniflovchi bilan tasniflanadi; natija kutilgan javob
   bilan solishtirib ko'rsatiladi.
2. "5 daqiqa qoidasi" muddati 1 daqiqaga tushirib sinaladi: bir guruhda menejer 20 soniyada javob
   beradi (answered), ikkinchisida javob yo'q (missed), uchinchisida menejer yo'q (missed, "menejer yo'q").
3. Shu ma'lumotdan Excel hisobot tuziladi (xulosalarni model yozadi) va fayl yo'li chiqariladi.
"""

import argparse
import asyncio
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

_TMP = Path(tempfile.mkdtemp(prefix="monitor_test_"))
os.environ["DB_PATH"] = str(_TMP / "monitor_test.db")
os.environ["REPORTS_DIR"] = str(_TMP)
os.environ["SLA_MINUTES"] = "1"
os.environ["SLA_WORK_HOURS"] = ""
os.environ["REPORT_TIME"] = "00:00"  # sinovda "kun" = kalendar kuni

from app import llm_client, monitor_classifier, monitor_db, monitor_report, monitor_sla  # noqa: E402
from app.monitor_db import CATEGORIES  # noqa: E402

DELIVERY, QUALITY, COMPLETE, STAFF, PRICE, RETURNS, POSITIVE, OTHER = CATEGORIES
S, I, N = "Shikoyat", "Ijobiy", "Neytral"
X = "xodim"

# (guruh, yozuvchi, matn, media turi, kutilgan: None = xodim xabari (kontekst);
#  False = kompaniyaga aloqasiz; aks holda (ruxsat etilgan toifalar, sentiment, menejer kerakmi yoki None))
SAMPLES = [
    ("Chilonzor 12-mavze obyekti", X, "Assalomu alaykum, eshitaman", "text", None),
    ("Chilonzor 12-mavze obyekti", "Vali", "Assalomu alaykum", "text", False),
    ("Chilonzor 12-mavze obyekti", "Vali", "Sement M500 qanchadan?", "text", ({PRICE}, N, True)),
    ("Chilonzor 12-mavze obyekti", X, "65 mingdan", "text", None),
    ("Chilonzor 12-mavze obyekti", "Vali", "Ok", "text", False),
    ("Chilonzor 12-mavze obyekti", "Vali", "50 qop buyurtma bermoqchiman, ertaga yetkazib bera olasizmi?", "text",
     ({DELIVERY, OTHER}, N, True)),
    ("Chilonzor 12-mavze obyekti", "Sardor", "Uch kundan beri kutyapman, mashina hali kelmadi. Bu qanaqa ish?!", "text",
     ({DELIVERY}, S, True)),
    ("Chilonzor 12-mavze obyekti", "Sardor", "👍", "text", False),
    ("Chilonzor 12-mavze obyekti", "Vali", None, "sticker", False),

    ("Sergeli Yangi hayot", "Олег", "Здравствуйте, сколько стоит гипсокартон 12 мм?", "text", ({PRICE}, N, True)),
    ("Sergeli Yangi hayot", "Олег", "Плитка пришла битая, половина коробок разбита. Когда замените?", "text",
     ({QUALITY, RETURNS}, S, True)),
    ("Sergeli Yangi hayot", "Олег", "Менеджер не берёт трубку второй день, это нормально вообще?", "text",
     ({STAFF}, S, True)),
    ("Sergeli Yangi hayot", "Дилноза", "Спасибо, всё привезли вовремя, очень довольны", "text", ({POSITIVE}, I, False)),
    ("Sergeli Yangi hayot", "Дилноза", "Можно вернуть лишний утеплитель? Упаковку не вскрывали", "text",
     ({RETURNS}, N, True)),
    ("Sergeli Yangi hayot", "Олег", "Кто знает хорошего электрика?", "text", False),
    ("Sergeli Yangi hayot", "Дилноза", "Могу дать номер, отличный мастер", "text", False),

    ("Yunusobod Bodomzor villa", "Jasur", "10 qop buyurtma qilgandim, 8 ta kepti, 2 tasi qani?", "text",
     ({COMPLETE}, S, True)),
    ("Yunusobod Bodomzor villa", "Jasur", "Armatura 12 lik so'ragandim, 10 lik olib kelishibdi", "text",
     ({COMPLETE}, S, None)),
    ("Yunusobod Bodomzor villa", "Jasur", "Haydovchingiz juda qo'pol gapirdi, bunaqasi ketmaydi", "text",
     ({STAFF}, S, None)),
    ("Yunusobod Bodomzor villa", "Bobur", "Rahmat, yetib keldi, hammasi joyida", "text", ({POSITIVE}, I, False)),
    ("Yunusobod Bodomzor villa", "Bobur", "Armatura 12 lik bormi sizlarda?", "text", ({OTHER}, N, True)),
    ("Yunusobod Bodomzor villa", "Bobur", "Schet-faktura yuborib bera olasizmi?", "text", ({PRICE}, N, True)),
    ("Yunusobod Bodomzor villa", "Bobur", "To'lov qildim, tekshirib ko'ring", "text", ({PRICE}, N, True)),
    ("Yunusobod Bodomzor villa", "Jasur", "Juma muborak hammaga", "text", False),
    ("Yunusobod Bodomzor villa", "Jasur", "Arzon kredit! Hoziroq yozing @kredit_bot", "text", False),

    ("Qibray kottej 3", "Sherzod", "Sementingiz yarmi qotib qolgan, sifati past ekan", "text", ({QUALITY}, S, None)),
    ("Qibray kottej 3", "Sherzod", "Brak chiqsa almashtirasizmi?", "text", ({RETURNS}, N, True)),
    ("Qibray kottej 3", "Sherzod", "Доставка qachon bo'ladi, bugun yetkazasizmi?", "text", ({DELIVERY}, N, True)),
    ("Qibray kottej 3", X, "Bugun soat 5 da yetkazamiz", "text", None),
    ("Qibray kottej 3", "Sherzod", "Narxingiz boshqalardan qimmat ekan", "text", ({PRICE}, S, None)),
    ("Qibray kottej 3", "Sherzod", "Shanba kuni ishlaysizlarmi?", "text", ({OTHER}, N, True)),
    ("Qibray kottej 3", "Sherzod", "Tez yetkazdingiz, barakalla, xizmatingizdan mamnunman", "text", ({POSITIVE}, I, False)),
]

STAFF_ID, MANAGER_ID = 900, 901


async def _add(chat_id, message_id, sender, text, media_type, sent_at) -> None:
    is_staff = sender == X
    await monitor_db.save_message(
        chat_id=chat_id, message_id=message_id, sender_id=STAFF_ID if is_staff else abs(hash(sender)) % 10**6,
        sender_username=None, sender_name="Ali (xodim)" if is_staff else sender, is_staff=is_staff, text=text,
        media_type=media_type, file_id=None, reply_to_message_id=None, sent_at=sent_at,
    )


async def test_classification() -> None:
    print(f"=== 1. Tasniflash ({llm_client.model_name()}) ===\n")
    chats = {title: -1000 - index for index, title in enumerate(dict.fromkeys(s[0] for s in SAMPLES))}
    # Sinov xabarlari kechagi kunga yoziladi: 5 daqiqa qoidasi sinoviga aralashmaydi
    start = monitor_db.utc_now() - timedelta(days=1, hours=2)
    for title, chat_id in chats.items():
        await monitor_db.upsert_group(chat_id, title)
        await monitor_db.save_group_admins(chat_id, {STAFF_ID}, [])
    for index, (title, sender, text, media_type, _expected) in enumerate(SAMPLES, start=1):
        await _add(chats[title], index, sender, text, media_type, start + timedelta(minutes=index))

    started = time.monotonic()
    while await monitor_classifier.classify_pending():
        pass
    elapsed = time.monotonic() - started

    async with monitor_db.session_maker() as session:
        from sqlalchemy import select

        rows = (
            await session.execute(
                select(monitor_db.Message, monitor_db.Classification)
                .outerjoin(monitor_db.Classification, monitor_db.Classification.message_ref == monitor_db.Message.id)
                .order_by(monitor_db.Message.message_id)
            )
        ).all()
    correct = customer = 0
    for (message, result), (_title, sender, _text, _media, expected) in zip(rows, SAMPLES):
        if expected is None:
            continue
        customer += 1
        text = message.text if message.text else f"[{message.media_type}]"
        if result is None:
            print(f"XATO  {text}\n      tasniflanmadi")
            continue
        if expected is False:
            ok = not result.is_company_related
            got = "aloqasiz" if not result.is_company_related else f"{result.category} / {result.sentiment}"
        else:
            categories, sentiment, needs = expected
            ok = (
                result.is_company_related and result.category in categories and result.sentiment == sentiment
                and (needs is None or result.needs_manager == needs)
            )
            got = (
                f"{result.category} / {result.sentiment} / menejer: {'ha' if result.needs_manager else 'yoq'}"
                if result.is_company_related else "aloqasiz"
            )
        correct += ok
        rule = " (qoida)" if result.model == "rule" else ""
        print(f"{'OK  ' if ok else 'XATO'}  [{sender}] {text}\n      -> {got}{rule}" + (f" | {result.summary}" if result.summary else ""))
    print(f"\n--> {correct}/{customer} to'g'ri, {elapsed:.0f} soniya ({elapsed / customer:.1f} s/xabar)\n")


async def test_sla() -> None:
    print("=== 2. 5 daqiqa qoidasi (muddat vaqtincha 1 daqiqa) ===\n")
    groups = {
        -2001: ("A guruh: menejer 20 soniyada javob beradi", [(MANAGER_ID, "menejer_bek", "Bek")]),
        -2002: ("B guruh: menejer javob bermaydi", [(MANAGER_ID + 1, "menejer_ali", "Ali")]),
        -2003: ("C guruh: menejer belgilanmagan", []),
    }
    now = monitor_db.utc_now()
    for chat_id, (title, managers) in groups.items():
        await monitor_db.upsert_group(chat_id, title)
        await monitor_db.save_group_admins(chat_id, {STAFF_ID} | {m[0] for m in managers}, managers)
        await _add(chat_id, 1, "Mijoz", "Assalomu alaykum, armatura 12 lik bormi, narxi qancha?", "text", now)
    await monitor_classifier.classify_pending()

    async def show(label):
        async with monitor_db.session_maker() as session:
            from sqlalchemy import select

            checks = (
                await session.execute(
                    select(monitor_db.ManagerCheck)
                    .where(monitor_db.ManagerCheck.chat_id.in_(groups))
                    .order_by(monitor_db.ManagerCheck.chat_id.desc())
                )
            ).scalars().all()
        states = "  ".join(f"{groups[c.chat_id][0][0]}={c.status}" for c in checks)
        print(f"   {label:>5}  {states}")
        return checks

    await show("0 s")
    started = time.monotonic()
    replied = False
    while True:
        await asyncio.sleep(5)
        elapsed = time.monotonic() - started
        if not replied and elapsed >= 20:
            # A guruhda menejer javob yozdi
            await monitor_db.save_message(
                chat_id=-2001, message_id=2, sender_id=MANAGER_ID, sender_username="menejer_bek", sender_name="Bek",
                is_staff=True, text="Bor, 12 lik 9 800 so'm/metr", media_type="text", file_id=None,
                reply_to_message_id=1, sent_at=monitor_db.utc_now(),
            )
            replied = True
        await monitor_db.resolve_checks()
        checks = await show(f"{elapsed:.0f} s")
        if all(check.status != "pending" for check in checks) or elapsed > 120:
            break
    expected = {-2001: "answered", -2002: "missed", -2003: "missed"}
    ok = all(check.status == expected[check.chat_id] for check in checks)
    print(f"\n--> {'kutilgandek' if ok else 'KUTILGANDEK EMAS'}: A=answered, B=missed, C=missed\n")
    for request in await monitor_db.missed_requests(monitor_db.local_day(now)):
        print(f"   javobsiz: {request.at:%H:%M} | {request.group} | {request.summary} | menejer: {request.manager or 'menejer yoq'}")
    print()


async def test_report() -> None:
    print("=== 3. Excel hisobot (kechagi sinov kuni) ===\n")
    day = monitor_db.local_day(monitor_db.utc_now() - timedelta(days=1, hours=2))
    report = await monitor_report.build_report(day)
    path = monitor_report.write_excel(report)
    stats = report.stats
    for row in stats.categories:
        print(f"   {row.category:32s} jami {row.total:2d}  shikoyat {row.complaints}  ijobiy {row.positive}  neytral {row.neutral}  ulush {row.share}%")
    print(f"   Jami kompaniyaga oid: {stats.total}; aloqasiz: {stats.filtered}; tasniflanmagan: {stats.unclassified}\n")
    for category, text in report.category_summaries.items():
        print(f"   [{category}]\n   {text}\n")
    print(f"   [Umumiy xulosa]\n   {report.overall_summary}\n")
    for group in report.groups:
        print(f"   [{group.title}] jami {group.stats.total}, javobsiz {len(group.missed)}\n   {group.overall_summary}\n")
    print(f"Fayl: {path} (varaqlar: Umumiy + {len(report.groups)} ta guruh)")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-sla", action="store_true", help="5 daqiqa qoidasi sinovini o'tkazib yuborish")
    args = parser.parse_args()
    await monitor_db.init_db()
    await test_classification()
    if not args.no_sla:
        await test_sla()
    await test_report()
    await monitor_db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
