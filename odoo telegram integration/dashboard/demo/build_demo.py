"""Builds the demo database (dashboard/demo/demo_monitor.db) for design and tests.

The schema is copied read-only from the real monitoring database (sqlite_master); when that file is
not available an embedded copy of the same schema is used. Every row is synthetic: by default 200
groups x 60 days of Uzbek/Russian construction-materials conversations with voice messages, missed
and late answers and several staff members. The real database is never written to.

    python demo/build_demo.py [--groups 200] [--days 60] [--seed 7] [--out demo/demo_monitor.db]
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sqlite3
import sys
import time as clock
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DASHBOARD_DIR = Path(__file__).resolve().parent.parent
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

from core.settings import load_settings  # noqa: E402
from demo import texts as T  # noqa: E402

DB_FMT = "%Y-%m-%d %H:%M:%S.%f"
MODEL = "ollama:gemma4:12b"
HOUR_WEIGHTS = [0.3, 0.2, 0.1, 0.1, 0.1, 0.2, 0.5, 1.2, 3, 6, 8, 8, 6, 6, 7, 7, 6, 5, 4, 3, 2, 1.5, 1, 0.5]
WEEKDAY_FACTOR = [1.0, 1.0, 1.0, 1.0, 1.0, 0.7, 0.35]

FALLBACK_SCHEMA = [
    """CREATE TABLE groups (chat_id BIGINT NOT NULL, title VARCHAR(255), admin_user_ids TEXT NOT NULL,
       manager_user_id BIGINT, manager_user_ids TEXT NOT NULL, manager_username VARCHAR(255),
       admins_checked_at DATETIME, updated_at DATETIME NOT NULL, is_present BOOLEAN NOT NULL DEFAULT 1,
       PRIMARY KEY (chat_id))""",
    """CREATE TABLE messages (id INTEGER NOT NULL, chat_id BIGINT NOT NULL, message_id BIGINT NOT NULL,
       sender_id BIGINT, sender_username VARCHAR(128), sender_name VARCHAR(255), is_staff BOOLEAN NOT NULL,
       text TEXT, media_type VARCHAR(16) NOT NULL, file_id VARCHAR(255), reply_to_message_id BIGINT,
       sent_at_utc DATETIME NOT NULL, local_date DATE NOT NULL, edited_at DATETIME, status VARCHAR(12) NOT NULL,
       attempts INTEGER NOT NULL, media_duration INTEGER, transcript TEXT, PRIMARY KEY (id),
       CONSTRAINT uq_messages_chat_message UNIQUE (chat_id, message_id),
       FOREIGN KEY(chat_id) REFERENCES groups (chat_id))""",
    """CREATE TABLE classifications (id INTEGER NOT NULL, message_ref INTEGER NOT NULL,
       is_company_related BOOLEAN NOT NULL, category VARCHAR(64), sentiment VARCHAR(16), summary TEXT,
       needs_manager BOOLEAN NOT NULL, model VARCHAR(64) NOT NULL, classified_at DATETIME NOT NULL,
       PRIMARY KEY (id), UNIQUE (message_ref), FOREIGN KEY(message_ref) REFERENCES messages (id))""",
    """CREATE TABLE manager_checks (id INTEGER NOT NULL, message_ref INTEGER NOT NULL, chat_id BIGINT NOT NULL,
       first_message_id BIGINT NOT NULL, manager_username VARCHAR(255), started_at DATETIME NOT NULL,
       deadline DATETIME NOT NULL, status VARCHAR(12) NOT NULL, answered_at DATETIME, local_date DATE NOT NULL,
       PRIMARY KEY (id), FOREIGN KEY(message_ref) REFERENCES messages (id))""",
    """CREATE TABLE daily_stats (id INTEGER NOT NULL, date DATE NOT NULL, category VARCHAR(64) NOT NULL,
       total INTEGER NOT NULL, complaints INTEGER NOT NULL, positive INTEGER NOT NULL, neutral INTEGER NOT NULL,
       share INTEGER, PRIMARY KEY (id), CONSTRAINT uq_daily_stats_date_category UNIQUE (date, category))""",
    """CREATE TABLE daily_summaries (id INTEGER NOT NULL, date DATE NOT NULL, category VARCHAR(64) NOT NULL,
       summary_text TEXT NOT NULL, report_sent_at DATETIME, PRIMARY KEY (id),
       CONSTRAINT uq_daily_summaries_date_category UNIQUE (date, category))""",
    "CREATE INDEX ix_classifications_category ON classifications (category)",
    "CREATE INDEX ix_daily_stats_category ON daily_stats (category)",
    "CREATE INDEX ix_daily_stats_date ON daily_stats (date)",
    "CREATE INDEX ix_daily_summaries_date ON daily_summaries (date)",
    "CREATE INDEX ix_manager_checks_chat_id ON manager_checks (chat_id)",
    "CREATE INDEX ix_manager_checks_deadline ON manager_checks (deadline)",
    "CREATE INDEX ix_manager_checks_local_date ON manager_checks (local_date)",
    "CREATE INDEX ix_manager_checks_status ON manager_checks (status)",
    "CREATE INDEX ix_messages_chat_id ON messages (chat_id)",
    "CREATE INDEX ix_messages_chat_sent ON messages (chat_id, sent_at_utc)",
    "CREATE INDEX ix_messages_date_staff ON messages (local_date, is_staff)",
    "CREATE INDEX ix_messages_sent ON messages (sent_at_utc)",
    "CREATE INDEX ix_messages_status ON messages (status)",
]


def read_schema(real_db: Path) -> tuple[list[str], str]:
    """CREATE statements of the real database, read through a read-only connection."""
    if real_db.is_file():
        try:
            conn = sqlite3.connect(real_db.as_uri() + "?mode=ro", uri=True, timeout=3)
            try:
                conn.execute("PRAGMA query_only=ON")
                rows = conn.execute("SELECT type, sql FROM sqlite_master WHERE sql IS NOT NULL "
                                    "AND name NOT LIKE 'sqlite_%'").fetchall()
            finally:
                conn.close()
            tables = [sql for kind, sql in rows if kind == "table"]
            if {"groups", "messages", "classifications", "manager_checks"} <= {
                    sql.split()[2].strip('"') for sql in tables}:
                return tables + [sql for kind, sql in rows if kind == "index"], "real"
        except sqlite3.Error:
            pass
    return FALLBACK_SCHEMA, "embedded"


def db_time(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(tzinfo=None).strftime(DB_FMT)


@dataclass
class Person:
    id: int
    name: str
    username: str | None
    lang: str = "uz"
    weight: float = 1.0


@dataclass
class Msg:
    at: datetime
    who: Person
    is_staff: bool
    text: str | None
    media: str = "text"
    transcript: str | None = None
    duration: int | None = None
    reply_to: "Msg | None" = None
    cls: tuple | None = None  # (related, category, sentiment, summary, needs_manager, model)
    status: str = "done"
    edited: datetime | None = None
    message_id: int = 0
    row_id: int = 0


@dataclass
class Group:
    chat_id: int
    title: str
    admins: list[Person]
    manager: Person | None
    customers: list[Person]
    weight: float
    complaint_factor: float
    rising: bool
    answer_in: float
    silent_from: date | None
    removed_on: date | None
    msgs: list[Msg] = field(default_factory=list)


class Builder:
    def __init__(self, groups: int, days: int, seed: int, now: datetime, tz: ZoneInfo, sla_minutes: int,
                 threads_per_day: float) -> None:
        self.rnd = random.Random(seed)
        self.n_groups, self.days, self.now, self.tz = groups, days, now, tz
        self.sla = timedelta(minutes=sla_minutes)
        self.threads_per_day = threads_per_day
        self.today = now.astimezone(tz).date()
        self.first_day = self.today - timedelta(days=days - 1)
        self.outage_from = datetime.combine(self.today - timedelta(days=2), time(11, 25), tzinfo=tz)
        self.incidents = {self.today - timedelta(days=9): "Dastavka va Logistika",
                          self.today - timedelta(days=23): "Mahsulot Sifati va Nuqsonlar"}
        self.staff = [Person(9_100_000 + i, f"{self.rnd.choice(T.UZ_FIRST)} {self.rnd.choice(T.SURNAMES)}",
                             f"db_staff_{i:02d}") for i in range(30)]
        self.company = [Person(9_200_000 + i, name, username) for i, (name, username) in enumerate(T.COMPANY_ACCOUNTS)]
        self.groups: list[Group] = []

    # ------------------------------------------------------------------ people and groups

    def _customer(self, group_index: int, slot: int) -> Person:
        lang = "ru" if self.rnd.random() < 0.25 else "uz"
        first = self.rnd.choice(T.RU_FIRST if lang == "ru" else T.UZ_FIRST)
        name = f"{first} {self.rnd.choice(T.SURNAMES)}" if self.rnd.random() < 0.6 else first
        username = f"client{group_index:03d}_{slot}" if self.rnd.random() < 0.6 else None
        return Person(7_000_000 + group_index * 20 + slot, name, username, lang, self.rnd.uniform(0.3, 2.0))

    def _title(self, index: int, used: set[str]) -> str:
        r = self.rnd
        styles = [
            lambda: f"{index}) {r.choice(T.UZ_FIRST).upper()} AKA {r.randint(1000, 9999)} dunyabunya",
            lambda: f"{r.choice(T.DISTRICTS)} {r.choice(T.OBJECTS)} — dunyabunya",
            lambda: f"\"{r.choice(T.SURNAMES)} Qurilish\" MChJ",
            lambda: f"ЖК {r.choice(T.RU_FIRST)}ский — поставки",
            lambda: f"{r.choice(T.DISTRICTS)} zakaz dunyabunya",
        ]
        title = r.choice(styles)()
        while title in used:
            title = f"{title} {r.randint(2, 99)}"
        used.add(title)
        return title

    def make_groups(self) -> None:
        r, used = self.rnd, set()
        for gi in range(self.n_groups):
            team = [self.staff[(gi * 3 + k) % len(self.staff)] for k in range(r.choice((1, 2, 3)))]
            manager = team[0] if r.random() < 0.55 else None
            customers = [self._customer(gi, slot) for slot in range(r.randint(2, 7))]
            slow = r.random() < 0.12
            removed = self.today - timedelta(days=r.randint(3, 30)) if r.random() < 0.03 else None
            silent = self.today - timedelta(days=r.randint(8, 20)) if (removed is None and r.random() < 0.05) else None
            self.groups.append(Group(
                chat_id=-1003_000_000_000 - gi, title=self._title(gi + 1, used), admins=team, manager=manager,
                customers=customers, weight=min(max(r.lognormvariate(0, 0.85), 0.15), 6.0),
                complaint_factor=1.9 if r.random() < 0.10 else 1.0, rising=r.random() < 0.15,
                answer_in=0.55 if slow else (0.93 if r.random() < 0.3 else 0.85),
                silent_from=silent, removed_on=removed))

    # ------------------------------------------------------------------ conversations

    def _fill(self, template: str, person: Person) -> tuple[str, str]:
        r = self.rnd
        uz, ru = r.choice(T.MATERIALS)
        q, n = r.choice((5, 10, 20, 30, 50, 100, 200)), r.randint(2, 5)
        d = r.choice(T.DISTRICTS)
        return template.format(m=uz, mr=ru, q=q, n=n, d=d), uz

    def _local(self, day: date, hour: int, minute: int, second: int) -> datetime:
        return datetime.combine(day, time(hour, minute, second), tzinfo=self.tz).astimezone(timezone.utc)

    def _thread(self, g: Group, day: date) -> None:
        r = self.rnd
        hour = r.choices(range(24), weights=HOUR_WEIGHTS)[0]
        t0 = self._local(day, hour, r.randint(0, 59), r.randint(0, 59))
        if t0 > self.now - timedelta(seconds=30):
            return
        customer = r.choices(g.customers, weights=[c.weight for c in g.customers])[0]
        if r.random() < 0.25:
            self._filtered(g, customer, t0 - timedelta(minutes=r.uniform(0.5, 3)))
        weights = dict(T.CATEGORY_WEIGHTS)
        incident = self.incidents.get(day)
        if incident:
            weights[incident] *= 3
        category = r.choices(list(weights), weights=list(weights.values()))[0]
        factor = g.complaint_factor
        if g.rising:
            factor *= 1.0 + 1.2 * max(0, 14 - (self.today - day).days) / 14
        if incident == category:
            factor *= 1.8
        if category == "Ijobiy Fikrlar":
            sentiment = "Ijobiy"
        else:
            sentiment = "Shikoyat" if r.random() < min(0.95, T.COMPLAINT_PROB[category] * factor) else "Neytral"
        options = T.TEMPLATES[category].get(sentiment) or T.TEMPLATES[category]["Neytral"]
        uz_t, ru_t, summary_t = r.choice(options)
        text, material = self._fill(ru_t if customer.lang == "ru" else uz_t, customer)
        summary = summary_t.format(m=material, d=r.choice(T.DISTRICTS))
        needs = sentiment != "Ijobiy" and r.random() < (0.9 if sentiment == "Shikoyat" else 0.8)
        media, transcript, duration, body = "text", None, None, text
        roll = r.random()
        if roll < 0.09:
            media, transcript, duration, body = "voice", text, r.randint(4, 45), None
        elif roll < 0.15:
            media = "photo"
        elif roll < 0.17:
            media = "document"
        cls = (1, category, sentiment, summary, int(needs), MODEL)
        if media == "voice" and t0 >= self.outage_from:
            transcript, cls, needs = "", (0, None, None, None, 0, "rule"), False
        request = Msg(t0, customer, False, body, media, transcript, duration, cls=cls)
        if media == "text" and r.random() < 0.01:
            request.edited = t0 + timedelta(minutes=r.uniform(1, 10))
        g.msgs.append(request)
        if needs and r.random() < 0.15:
            g.msgs.append(Msg(t0 + timedelta(minutes=r.uniform(2, 8)), customer, False, "?",
                              cls=(0, None, None, None, 0, "rule")))
        answered_at = self._answer(g, request, needs, sentiment)
        if answered_at and r.random() < 0.15:
            self._filtered(g, customer, answered_at + timedelta(minutes=r.uniform(1, 20)), thanks=True)
        if r.random() < 0.05:
            g.msgs.append(Msg(t0 + timedelta(minutes=r.uniform(0.2, 30)), customer, False, None, "sticker",
                              cls=(0, None, None, None, 0, "rule")))

    def _filtered(self, g: Group, who: Person, at: datetime, thanks: bool = False) -> None:
        uz, ru, summary, by_rule = T.FILTERED[3] if thanks else self.rnd.choice(T.FILTERED)
        if at > self.now - timedelta(seconds=30):
            return
        g.msgs.append(Msg(at, who, False, ru if who.lang == "ru" else uz,
                          cls=(0, None, None, summary, 0, "rule" if by_rule else MODEL)))

    def _answer(self, g: Group, request: Msg, needs: bool, sentiment: str) -> datetime | None:
        r = self.rnd
        if not needs:
            if sentiment == "Ijobiy" and r.random() < 0.3:
                return self._staff_reply(g, request, request.at + timedelta(minutes=r.uniform(1, 30)))
            return None
        roll = r.random()
        if roll < g.answer_in:
            return self._staff_reply(g, request, request.at + timedelta(minutes=r.uniform(0.3, 4.7)))
        if roll < g.answer_in + 0.13:
            delay = min(600.0, max(6.0, r.lognormvariate(3.3, 0.9)))
            return self._staff_reply(g, request, request.at + timedelta(minutes=delay))
        if roll < g.answer_in + 0.15:
            peers = [c for c in g.customers if c.id != request.who.id]
            if peers:
                uz, ru, summary = r.choice(T.PEER_REPLIES)
                peer = r.choice(peers)
                at = request.at + timedelta(minutes=r.uniform(1, 4))
                if at < self.now:
                    g.msgs.append(Msg(at, peer, False, ru if peer.lang == "ru" else uz,
                                      cls=(1, "Boshqa", "Neytral", summary, 0, MODEL)))
                    return at
        return None

    def _staff_reply(self, g: Group, request: Msg, at: datetime) -> datetime | None:
        if at > self.now - timedelta(seconds=20):
            return None
        r = self.rnd
        who = r.choice(self.company) if r.random() < 0.1 else r.choice(g.admins)
        uz, ru = r.choice(T.STAFF_REPLIES)
        text, _material = self._fill(ru if request.who.lang == "ru" else uz, request.who)
        g.msgs.append(Msg(at, who, True, text, reply_to=request if r.random() < 0.6 else None, status="staff"))
        return at

    def generate(self) -> None:
        r = self.rnd
        for g in self.groups:
            for offset in range(self.days):
                day = self.first_day + timedelta(days=offset)
                if (g.removed_on and day >= g.removed_on) or (g.silent_from and day >= g.silent_from):
                    continue
                lam = self.threads_per_day * g.weight * WEEKDAY_FACTOR[day.weekday()]
                for _ in range(_poisson(r, lam)):
                    self._thread(g, day)
                if r.random() < 0.1:
                    staff = r.choice(g.admins)
                    at = self._local(day, r.randint(8, 18), r.randint(0, 59), 0)
                    if at < self.now:
                        g.msgs.append(Msg(at, staff, True, "Hurmatli mijozlar, bugun ombor 18:00 gacha ishlaydi",
                                          status="staff"))
            g.msgs.sort(key=lambda msg: msg.at)
            next_id = r.randint(1000, 5000)
            for msg in g.msgs:
                msg.message_id = next_id
                next_id += r.choice((1, 1, 1, 2))
                if not msg.is_staff and msg.at > self.now - timedelta(minutes=6):
                    msg.status, msg.cls = "pending", None
                elif not msg.is_staff and msg.cls is not None and r.random() < 0.003:
                    msg.status, msg.cls = "failed", None

    # ------------------------------------------------------------------ the bot's answer rule

    @staticmethod
    def _answers(asker: Person, candidate: Msg) -> bool:
        if candidate.who.id == asker.id:
            return False
        if candidate.is_staff:
            return True
        if candidate.reply_to is not None and candidate.reply_to.who.id == asker.id:
            return True
        cls = candidate.cls
        return cls is not None and cls[5] != "rule" and not cls[4]

    def checks_for(self, g: Group) -> list[tuple]:
        rows, open_check = [], None
        for index, msg in enumerate(g.msgs):
            cls = msg.cls
            if msg.is_staff or msg.status != "done" or cls is None or not cls[0] or not cls[4]:
                continue
            if open_check is not None:
                o_deadline, o_answer_index = open_check
                if o_deadline >= msg.at and (o_answer_index is None or o_answer_index > index):
                    continue  # merged into the open check, as the bot does
            deadline = msg.at + self.sla
            answer_index = next((j for j in range(index + 1, len(g.msgs)) if self._answers(msg.who, g.msgs[j])), None)
            answer = g.msgs[answer_index] if answer_index is not None else None
            if answer is not None and answer.at <= deadline:
                status, answered_at = "answered", db_time(answer.at)
            elif deadline + timedelta(seconds=15) <= self.now:
                status, answered_at = "missed", None
            else:
                status, answered_at = "pending", None
            manager = f"@{g.manager.username}" if g.manager else None
            rows.append((msg.row_id, g.chat_id, msg.message_id, manager, db_time(msg.at), db_time(deadline), status,
                         answered_at, msg.at.astimezone(self.tz).date().isoformat()))
            open_check = (deadline, answer_index)
        return rows


def _poisson(rnd: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rnd.random()
        if p <= limit:
            return k
        k += 1


def _write(builder: Builder, out: Path, schema: list[str], report_time: time) -> dict:
    conn = sqlite3.connect(out)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    for statement in schema:
        conn.execute(statement)
    now_text = db_time(builder.now)
    group_rows, msg_rows, cls_rows, check_rows = [], [], [], []
    row_id = 0
    for g in builder.groups:
        manager = g.manager
        group_rows.append((g.chat_id, g.title, ",".join(str(p.id) for p in g.admins),
                           manager.id if manager else None, str(manager.id) if manager else "",
                           f"@{manager.username}" if manager else None, now_text, now_text,
                           0 if g.removed_on else 1))
        for msg in g.msgs:
            row_id += 1
            msg.row_id = row_id
            local_date = msg.at.astimezone(builder.tz).date().isoformat()
            msg_rows.append((row_id, g.chat_id, msg.message_id, msg.who.id, msg.who.username, msg.who.name,
                             int(msg.is_staff), msg.text, msg.media, f"demo-{row_id}" if msg.media != "text" else None,
                             msg.reply_to.message_id if msg.reply_to else None, db_time(msg.at), local_date,
                             db_time(msg.edited) if msg.edited else None, msg.status,
                             5 if msg.status == "failed" else (0 if msg.status != "done" else 1),
                             msg.duration, msg.transcript))
            if msg.cls is not None and msg.status == "done":
                related, category, sentiment, summary, needs, model = msg.cls
                classified = msg.at + timedelta(seconds=builder.rnd.uniform(5, 90))
                cls_rows.append((row_id, related, category, sentiment, summary, needs, model, db_time(classified)))
        check_rows.extend(builder.checks_for(g))
    conn.executemany("INSERT INTO groups (chat_id, title, admin_user_ids, manager_user_id, manager_user_ids, "
                     "manager_username, admins_checked_at, updated_at, is_present) VALUES (?,?,?,?,?,?,?,?,?)",
                     group_rows)
    conn.executemany("INSERT INTO messages (id, chat_id, message_id, sender_id, sender_username, sender_name, "
                     "is_staff, text, media_type, file_id, reply_to_message_id, sent_at_utc, local_date, edited_at, "
                     "status, attempts, media_duration, transcript) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     msg_rows)
    cls_rows.sort(key=lambda row: row[-1])  # the bot classifies in time order: id order = time order
    check_rows.sort(key=lambda row: row[4])
    conn.executemany("INSERT INTO classifications (message_ref, is_company_related, category, sentiment, summary, "
                     "needs_manager, model, classified_at) VALUES (?,?,?,?,?,?,?,?)", cls_rows)
    conn.executemany("INSERT INTO manager_checks (message_ref, chat_id, first_message_id, manager_username, "
                     "started_at, deadline, status, answered_at, local_date) VALUES (?,?,?,?,?,?,?,?,?)", check_rows)
    _write_daily(conn, builder, report_time)
    conn.commit()
    counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in ("groups", "messages", "classifications", "manager_checks", "daily_stats", "daily_summaries")}
    conn.close()
    return counts


def _write_daily(conn: sqlite3.Connection, builder: Builder, report_time: time) -> None:
    """daily_stats / daily_summaries for every complete report window (same definitions as the bot)."""
    categories = list(T.CATEGORY_WEIGHTS)
    order = ["Dastavka va Logistika", "Mahsulot Sifati va Nuqsonlar", "Komplektatsiya va Hajm",
             "Xodimlar va Xizmat Ko'rsatish", "Narx va To'lovlar", "Qaytarish va Kafolat", "Ijobiy Fikrlar", "Boshqa"]
    assert set(order) == set(categories)
    day = builder.first_day
    while True:
        start = datetime.combine(day, report_time, tzinfo=builder.tz)
        end = start + timedelta(days=1)
        if end > builder.now:
            break
        s, e = db_time(start), db_time(end)
        rows = conn.execute("""
            SELECT c.category, COUNT(*), SUM(c.sentiment = 'Shikoyat'), SUM(c.sentiment = 'Ijobiy')
            FROM messages m JOIN classifications c ON c.message_ref = m.id
            WHERE m.sent_at_utc >= ? AND m.sent_at_utc < ? AND m.is_staff = 0 AND c.is_company_related = 1
            GROUP BY c.category""", (s, e)).fetchall()
        stats = {name: [0, 0, 0] for name in order}
        for category, total, complaints, positive in rows:
            target = stats.get(category) or stats["Boshqa"]
            target[0] += total
            target[1] += complaints or 0
            target[2] += positive or 0
        grand = sum(v[0] for v in stats.values())
        filtered = conn.execute("""SELECT COUNT(*) FROM messages m JOIN classifications c ON c.message_ref = m.id
            WHERE m.sent_at_utc >= ? AND m.sent_at_utc < ? AND m.is_staff = 0 AND c.is_company_related = 0""",
                                (s, e)).fetchone()[0]
        missed = conn.execute("SELECT COUNT(*) FROM manager_checks WHERE started_at >= ? AND started_at < ? "
                              "AND status = 'missed'", (s, e)).fetchone()[0]
        out = []
        for name in order:
            total, complaints, positive = stats[name]
            share = round(total * 100 / grand) if grand else None
            out.append((day.isoformat(), name, total, complaints, positive, total - complaints - positive, share))
        complaints_all = sum(v[1] for v in stats.values())
        positive_all = sum(v[2] for v in stats.values())
        out.append((day.isoformat(), "Jami kompaniyaga oid", grand, complaints_all, positive_all,
                    grand - complaints_all - positive_all, 100 if grand else None))
        out.append((day.isoformat(), "Kompaniyaga aloqasiz (filtrlangan)", filtered, 0, 0, 0, None))
        out.append((day.isoformat(), "Javobsiz so'rovlar", missed, 0, 0, 0, None))
        conn.executemany("INSERT INTO daily_stats (date, category, total, complaints, positive, neutral, share) "
                         "VALUES (?,?,?,?,?,?,?)", out)
        sent = db_time(end + timedelta(seconds=40))
        summaries = []
        for name in order:
            total, complaints, _positive = stats[name]
            text = ("Bugun bu toifada xabar bo'lmadi" if not total else
                    f"Demo xulosa: {name.lower()} bo'yicha {total} ta murojaat, ulardan {complaints} tasi shikoyat. "
                    "Mijozlar asosan narx, mavjudlik va yetkazib berish muddatlarini so'rashdi.")
            summaries.append((day.isoformat(), name, text, None))
        top = max(order, key=lambda name: stats[name][1])
        summaries.append((day.isoformat(), "umumiy",
                          f"Demo umumiy xulosa: kun davomida {grand} ta kompaniyaga oid xabar keldi, {complaints_all} tasi "
                          f"shikoyat. Eng ko'p shikoyat «{top}» toifasida. Javobsiz qolgan so'rovlar: {missed} ta.", sent))
        conn.executemany("INSERT INTO daily_summaries (date, category, summary_text, report_sent_at) VALUES (?,?,?,?)",
                         summaries)
        day += timedelta(days=1)


def build(out: Path, groups: int = 200, days: int = 60, seed: int = 7, threads_per_day: float = 3.0,
          now: datetime | None = None) -> dict:
    settings = load_settings()
    tz = settings.tz
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    schema, source = read_schema(settings.real_db_path)
    builder = Builder(groups, days, seed, now, tz, settings.sla_minutes, threads_per_day)
    builder.make_groups()
    builder.generate()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    counts = _write(builder, tmp, schema, settings.report_time)
    os.replace(tmp, out)
    counts["schema"] = source
    return counts


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--groups", type=int, default=200)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--threads", type=float, default=3.0, help="average conversations per group per day")
    parser.add_argument("--out", type=Path, default=DASHBOARD_DIR / "demo" / "demo_monitor.db")
    args = parser.parse_args()
    out = args.out if args.out.is_absolute() else (Path.cwd() / args.out)
    if out.resolve() == settings.real_db_path:
        raise SystemExit("Refusing to write to the real monitoring database")
    started = clock.monotonic()
    counts = build(out, args.groups, args.days, args.seed, args.threads)
    print(f"demo database: {out} ({clock.monotonic() - started:.1f}s)")
    print(", ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()
