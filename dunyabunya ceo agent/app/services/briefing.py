"""Kunlik xulosa (brifing): bugun va kechagi sotuv, to'lovlar, qarzdorlik va vazifalar —
bitta qisqa xabarda.

Uch yo'l bilan ishlatiladi:
- `/brifing` buyrug'i — CEO istalgan payt so'raydi (AI'siz, bir necha soniyada);
- yordamchining `get_daily_briefing` tool'i — "bugun nima gap?" kabi savollarga;
- `.env`da `DAILY_BRIEFING_TIME=08:30` berilsa — har kuni shu vaqtda CEO'larga o'zi yuboriladi.

Barcha sonlarni Odoo'ning o'zi jamlaydi (read_group), valyutalar alohida ko'rsatiladi.
"""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app import ceo_state, scheduler_state
from app.config import settings
from app.services import odoo_tools as t
from app.services import report_html as rh

log = logging.getLogger(__name__)

_TZ = ZoneInfo("Asia/Tashkent")
_WEEKDAYS = ("dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba")
_OPEN_TASKS = [["state", "not in", ["1_done", "1_canceled"]]]


def _by_currency(groups: list[dict], measure: str) -> list[dict]:
    return [
        {"valyuta": t._m2o_name(g.get("currency_id"), ""), "summa": g.get(measure) or 0,
         "soni": t._group_count(g, "currency_id")}
        for g in groups if g.get(measure) or t._group_count(g, "currency_id")
    ]


def _payments(groups: list[dict]) -> list[dict]:
    by_currency: dict[str, dict] = {}
    for g in groups:
        item = by_currency.setdefault(t._m2o_name(g.get("currency_id"), ""), {"kirim": 0.0, "chiqim": 0.0})
        item["kirim" if g.get("payment_type") == "inbound" else "chiqim"] += g.get("amount") or 0
    return [{"valyuta": currency, **sums} for currency, sums in sorted(by_currency.items())]


async def _sales(day_from: date, day_to: date) -> list[dict]:
    domain = t._SALE_DONE + t._dt_bounds("date_order", day_from.isoformat(), day_to.isoformat())
    return _by_currency(await t._rg("sale.order", domain, ["amount_total"], "currency_id"), "amount_total")


async def _paid(day: date) -> list[dict]:
    domain = [["state", "in", t._PAYMENT_DONE_STATES], ["date", "=", day.isoformat()]]
    return _payments(await t._rg("account.payment", domain, ["amount"], ["payment_type", "currency_id"]))


async def collect_briefing(today: date | None = None) -> dict:
    """Xulosa uchun barcha ko'rsatkichlarni Odoo'dan parallel o'qiydi."""
    today = today or t._today()
    yesterday = today - timedelta(days=1)
    month_start = today.replace(day=1)
    # O'tgan oyning aynan shu kunigacha bo'lgan davri (taqqoslash teng davrlar o'rtasida bo'lishi uchun)
    prev_end = month_start - timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    prev_same_day = min(prev_start + timedelta(days=today.day - 1), prev_end)
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    (sales_today, sales_yesterday, sales_month, sales_prev, paid_today, paid_yesterday,
     debt, overdue_debt, open_tasks, overdue_tasks) = await asyncio.gather(
        _sales(today, today),
        _sales(yesterday, yesterday),
        _sales(month_start, today),
        _sales(prev_start, prev_same_day),
        _paid(today),
        _paid(yesterday),
        t._rg("account.move", t._UNPAID_INVOICE_DOMAIN, ["amount_residual"], "currency_id"),
        t._rg("account.move", t._UNPAID_INVOICE_DOMAIN + [["invoice_date_due", "<", today.isoformat()]],
              ["amount_residual"], "currency_id"),
        t._count("project.task", _OPEN_TASKS),
        t._count("project.task", _OPEN_TASKS + [["date_deadline", "<", now_utc]]),
    )
    return {
        "sana": today.isoformat(),
        "hafta_kuni": _WEEKDAYS[today.weekday()],
        "sotuv": {
            "bugun": sales_today, "kecha": sales_yesterday,
            "oy_boshidan": sales_month, "otgan_oy_shu_davrda": sales_prev,
            "otgan_oy_davri": f"{prev_start.isoformat()} — {prev_same_day.isoformat()}",
        },
        "tolovlar": {"bugun": paid_today, "kecha": paid_yesterday},
        "qarzdorlik": {"jami": _by_currency(debt, "amount_residual"),
                       "muddati_otgan": _by_currency(overdue_debt, "amount_residual")},
        "vazifalar": {"ochiq": open_tasks, "muddati_otgan": overdue_tasks},
    }


def _money(items: list[dict], count_word: str = "") -> str:
    """"2.62 mlrd UZS (1 281 ta buyurtma)" — valyutalar vergul bilan; bo'sh bo'lsa "yo'q"."""
    parts = []
    for item in items:
        text = f"{rh.fmt_num(item['summa'], 2)} {item['valyuta']}".strip()
        if count_word and item.get("soni"):
            text += f" ({rh.fmt_int(item['soni'])} ta {count_word})"
        parts.append(text)
    return ", ".join(parts) or "yo'q"


def _change(current: list[dict], previous: list[dict]) -> str:
    """Oy boshidan o'sish/kamayish foizi (faqat bitta va bir xil valyutada bo'lsa)."""
    if len(current) != 1 or len(previous) != 1 or current[0]["valyuta"] != previous[0]["valyuta"]:
        return ""
    before = previous[0]["summa"]
    if not before:
        return ""
    pct = (current[0]["summa"] - before) / before * 100
    return f" — {'+' if pct >= 0 else '−'}{abs(pct):.0f}%"


def _payment_lines(label: str, items: list[dict]) -> list[str]:
    if not items:
        return [f"{label}: yo'q"]
    return [f"{label}: kirim {rh.fmt_num(i['kirim'], 2)}, chiqim {rh.fmt_num(i['chiqim'], 2)} {i['valyuta']}"
            for i in items]


def format_briefing(data: dict) -> str:
    """Xulosani Telegram (HTML) xabariga aylantiradi."""
    e = lambda value: html.escape(str(value), quote=False)  # noqa: E731
    sales, pay, debt, tasks = data["sotuv"], data["tolovlar"], data["qarzdorlik"], data["vazifalar"]
    lines = [
        f"<b>Kunlik xulosa</b> — {e(data['sana'])}, {e(data['hafta_kuni'])}",
        "",
        "<b>Sotuv</b>",
        f"Bugun: {e(_money(sales['bugun'], 'buyurtma'))}",
        f"Kecha: {e(_money(sales['kecha'], 'buyurtma'))}",
        f"Oy boshidan: {e(_money(sales['oy_boshidan']))}"
        + (f" (o'tgan oyning shu davrida {e(_money(sales['otgan_oy_shu_davrda']))}"
           f"{e(_change(sales['oy_boshidan'], sales['otgan_oy_shu_davrda']))})"
           if sales["otgan_oy_shu_davrda"] else ""),
        "",
        "<b>To'lovlar</b>",
        *[e(line) for line in _payment_lines("Bugun", pay["bugun"])],
        *[e(line) for line in _payment_lines("Kecha", pay["kecha"])],
        "",
        "<b>Mijozlar qarzi</b>",
        f"Jami: {e(_money(debt['jami'], 'faktura'))}",
        f"Muddati o'tgan: {e(_money(debt['muddati_otgan'], 'faktura'))}",
        "",
        "<b>Vazifalar</b>",
        f"Ochiq: {rh.fmt_int(tasks['ochiq'])} ta, shundan muddati o'tgani: {rh.fmt_int(tasks['muddati_otgan'])} ta",
    ]
    return "\n".join(lines)


def briefing_time() -> time | None:
    """`.env`dagi DAILY_BRIEFING_TIME ("HH:MM") — noto'g'ri yoki bo'sh bo'lsa None."""
    raw = settings.DAILY_BRIEFING_TIME.strip()
    if not raw:
        return None
    try:
        hour, minute = raw.split(":", 1)
        return time(hour=int(hour), minute=int(minute))
    except ValueError:
        log.error("briefing: DAILY_BRIEFING_TIME=%r noto'g'ri (kerakli ko'rinish HH:MM) — brifing yuborilmaydi", raw)
        return None


async def send_briefing(bot) -> int:
    """Xulosani barcha ma'lum CEO'larning shaxsiy chatiga yuboradi; nechtasiga borgani qaytadi."""
    ceo_ids = await ceo_state.get_ceo_telegram_user_ids()
    if not ceo_ids:
        log.warning("briefing: CEO chat_id hali ma'lum emas (CEO botga biror marta yozishi kerak) — yuborilmadi")
        return 0
    text = format_briefing(await collect_briefing())
    sent = 0
    for ceo_id in ceo_ids:
        try:
            await bot.send_message(chat_id=ceo_id, text=text, parse_mode="HTML")
            sent += 1
        except Exception:
            log.exception("briefing: CEO %s ga yuborishda xato", ceo_id)
    return sent


async def _loop(bot, at: time) -> None:
    log.info("briefing: kunlik xulosa har kuni %s da yuboriladi (Asia/Tashkent)", at.strftime("%H:%M"))
    while True:
        try:
            now = datetime.now(_TZ)
            today = now.date().isoformat()
            # Bot kechikib tursa ham (20:00 gacha) o'sha kungi xulosa yuboriladi
            if now.time() >= at and now.hour < 20 and scheduler_state.last_run("briefing") != today:
                scheduler_state.mark_run("briefing", today)
                await send_briefing(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("briefing: kunlik xulosani yuborishda xato")
        await asyncio.sleep(60)


def start_briefing_scheduler(bot) -> asyncio.Task | None:
    """main.py lifespan ichida chaqiriladi. DAILY_BRIEFING_TIME berilmagan bo'lsa None."""
    at = briefing_time()
    return asyncio.create_task(_loop(bot, at)) if at else None
