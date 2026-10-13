r"""Measure how well the bot's photo reader reads the order number and the customer from real document photos.

    venv\Scripts\python.exe scripts\bench_reader.py 60             the model from .env
    venv\Scripts\python.exe scripts\bench_reader.py 60 MODEL ...   other OpenAI models, to compare

It runs app.extract.read_photo, the very thing the bot runs, turning of sideways photos included. The photos and
the truth about them come from the document bot on this server (D:\Production\telegram-odoo-bot): the order number
is the Odoo record a photo was attached to, the customer is the name that matched that record. The photos are
chosen once and kept in data/bench/cases_N.json, so later runs compare on the same photos; what was read goes to
data/bench/result_N.json. Every read is a paid OpenAI call (a sideways photo takes two or three).

While it runs, the watchdog takes this venv python for the bot and would not restart a bot that died.
"""
import asyncio
import difflib
import json
import random
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import extract  # noqa: E402
from app.config import settings  # noqa: E402

DOCBOT = Path("D:/Production/telegram-odoo-bot/data")
OUT = ROOT / "data" / "bench"
# a customer read with a slipped letter or digit still counts as read at this likeness
CLOSE = 0.8


def cases(count: int) -> list[dict]:
    kept = OUT / f"cases_{count}.json"
    if kept.exists():
        return json.loads(kept.read_text(encoding="utf-8"))
    db = sqlite3.connect(f"file:{DOCBOT.as_posix()}/docbot.db?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "select id, path, odoo_record_name, extracted_data from jobs where status = 'attached'"
        " and odoo_model = 'sale.order' and mime_type = 'image/jpeg' order by id"
    ).fetchall()
    found = []
    for row in rows:
        data = json.loads(row["extracted_data"] or "{}")
        ai = data.get("ai") or {}
        details = (data.get("check") or {}).get("details") or {}
        path = DOCBOT / "images" / row["path"]
        # only a first page has the header with the customer and the number; the customer must have matched Odoo
        if (ai.get("partner") and details.get("partner") == 1.0 and ai.get("first_item") in (None, 1)
                and ai.get("page_number") in (None, 1) and path.exists()):
            found.append({"job": row["id"], "path": str(path), "order": row["odoo_record_name"],
                          "customer": ai["partner"], "sideways": bool(data.get("rotate_cw"))})
    random.Random(7).shuffle(found)
    OUT.mkdir(parents=True, exist_ok=True)
    kept.write_text(json.dumps(found[:count], ensure_ascii=False, indent=1), encoding="utf-8")
    return found[:count]


def _order(value: str | None) -> str:
    return re.sub(r"[\s№#]", "", value or "").upper()


def _name(value: str | None) -> str:
    return re.sub(r"[^\w]+", " ", (value or "").lower()).strip()


def likeness(read: str | None, customer: str) -> float:
    """How close the customer that was read is to the customer on the document (1.0 = the same)."""
    return difflib.SequenceMatcher(None, _name(read), _name(customer)).ratio() if read else 0.0


async def main() -> None:
    count, models = int(sys.argv[1]), sys.argv[2:] or [settings.openai_model]
    chosen = cases(count)
    out = OUT / f"result_{count}.json"
    result = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    print(f"{len(chosen)} photos, {sum(case['sideways'] for case in chosen)} of them not upright", flush=True)

    spent = {"calls": 0, "in": 0, "out": 0}
    real_ask = extract.ask

    async def counting_ask(session, body):
        payload = await real_ask(session, body)
        usage = payload.get("usage") or {}
        spent["calls"] += 1
        spent["in"] += usage.get("input_tokens") or 0
        spent["out"] += usage.get("output_tokens") or 0
        return payload

    extract.ask = counting_ask
    for model in models:
        rows = []
        spent.update(calls=0)
        spent["in"] = spent["out"] = 0
        for case in chosen:
            started, calls = time.monotonic(), spent["calls"]
            customer = order = error = None
            try:
                found = await extract.read_photo(Path(case["path"]).read_bytes(), "image/jpeg", model)
                customer, order = found.customer, found.order_number
            except extract.ExtractError as exc:
                error = str(exc)[:200]
            rows.append({
                "job": case["job"], "sideways": case["sideways"], "real_order": case["order"], "order": order,
                "real_customer": case["customer"], "customer": customer, "error": error,
                "order_ok": _order(order) == _order(case["order"]),
                "likeness": round(likeness(customer, case["customer"]), 2),
                "seconds": round(time.monotonic() - started, 1), "calls": spent["calls"] - calls,
            })
        result[model] = rows
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        n, seconds = len(rows), sorted(row["seconds"] for row in rows)
        print(
            f"{model}: order {sum(row['order_ok'] for row in rows)}/{n}; "
            f"customer {sum(row['likeness'] >= CLOSE for row in rows)}/{n} "
            f"({sum(row['likeness'] == 1.0 for row in rows)} letter for letter); "
            f"errors {sum(1 for row in rows if row['error'])}; median {seconds[n // 2]}s, max {seconds[-1]}s; "
            f"{spent['calls']} calls, per photo {spent['in'] // n} tokens in, {spent['out'] // n} out",
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
