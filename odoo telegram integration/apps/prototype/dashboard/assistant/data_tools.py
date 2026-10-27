"""Read-only tools of the data agent. They reuse the dashboard's own queries (core.queries) through the
read-only session (core.db: mode=ro, query_only, time limit) and the same metric definitions
(core.metrics), and return compact JSON: at most MAX_ROWS rows, texts cut to SNIPPET characters.
No new SQL and nothing is ever written."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from assistant.llm import Tool, ToolError, nullable, obj
from core import db, queries
from core import metrics as m_
from core import timeutil as tu
from core.settings import Settings

PERIODS = ("selected", "today", "yesterday", "last24h", "last7", "last30", "this_month", "last_month", "report", "custom")
MAX_ROWS = 30
SNIPPET = 200
MAX_TREND_DAYS = 92
MAX_SUMMARY_DAYS = 14
OTHERS = "other participants (not staff)"


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _snippet(value, limit: int = SNIPPET) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _limit(value, default: int = 10) -> int:
    try:
        number = int(value) if value is not None else default
    except (TypeError, ValueError):
        number = default
    return max(1, min(MAX_ROWS, number))


def _person(name, username) -> str:
    if isinstance(name, str) and name.strip():
        return name.strip()
    if isinstance(username, str) and username.strip():
        return "@" + username.strip()
    return "—"


def _seconds(value) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


def _text(row) -> str:
    parts = [part for part in (getattr(row, "text", None), getattr(row, "transcript", None)) if isinstance(part, str) and part]
    return "\n".join(parts)


class DataTools:
    def __init__(self, settings: Settings, context: dict) -> None:
        self.settings = settings
        self.context = context
        self._titles: list[tuple[int, str]] | None = None

    # ------------------------------------------------------------------ helpers

    def _query(self, name: str, *args, **kwargs):
        with db.session(self.settings) as session:
            return getattr(queries, name)(session, *args, **kwargs)

    def _now(self) -> datetime:
        return tu.now_local(self.settings.tz)

    def _local(self, value) -> str | None:
        moment = tu.from_db(value, self.settings.tz)
        return moment.strftime("%Y-%m-%d %H:%M") if moment else None

    def period(self, args: dict) -> tu.Period:
        key = args.get("period") or "selected"
        if key == "selected":
            selected = self.context.get("period")
            if isinstance(selected, tu.Period):
                return selected
            key = tu.DEFAULT_PRESET
        if key == "custom":
            first, last = _day(args.get("date_from")), _day(args.get("date_to"))
            if not first or not last:
                raise ToolError("period=custom needs date_from and date_to as YYYY-MM-DD.")
            return tu.resolve_period("custom", self._now(), self.settings.report_time, (first, last))
        if key not in tu.PRESETS:
            raise ToolError(f"Unknown period {key!r}. Use one of: {', '.join(PERIODS)}.")
        return tu.resolve_period(key, self._now(), self.settings.report_time)

    def titles(self) -> list[tuple[int, str]]:
        if self._titles is None:
            frame = self._query("group_titles")
            self._titles = [(int(chat), str(title)) for chat, title in zip(frame["chat_id"], frame["title"])]
        return self._titles

    def group(self, name) -> tuple[int, str] | None:
        if not name or not str(name).strip():
            return None
        needle = str(name).strip().lower()
        titles = self.titles()
        exact = [item for item in titles if item[1].lower() == needle]
        if exact:
            return exact[0]
        found = [item for item in titles if needle in item[1].lower()]
        if len(found) == 1:
            return found[0]
        if not found:
            raise ToolError(f"No group matches {name!r}. Call find_group to see the group names.")
        raise ToolError(f"{len(found)} groups match {name!r}: " + "; ".join(title for _chat, title in found[:12])
                        + ". Pass the exact title, or ask the CEO which one.")

    @staticmethod
    def category(name) -> str | None:
        if not name:
            return None
        names = m_.category_names()
        for item in names:
            if item.lower() == str(name).strip().lower():
                return item
        raise ToolError(f"Unknown category {name!r}. Categories: {', '.join(names)}.")

    def _kpis(self, period: tu.Period, chat_id: int | None) -> m_.Kpis:
        totals = self._query("totals", period.start_db, period.end_db, chat_id)
        checks = self._query("checks", period.start_db, period.end_db, chat_id)
        return m_.build_kpis(totals, checks)

    def _scope(self, args: dict) -> tuple[tu.Period, tuple[int, str] | None]:
        return self.period(args), self.group(args.get("group"))

    # ------------------------------------------------------------------ tools

    def overview(self, args: dict) -> dict:
        period, group = self._scope(args)
        chat_id = group[0] if group else None
        cur = self._kpis(period, chat_id)
        previous = period.previous(self._now())
        prev = self._kpis(previous, chat_id)

        def change(current, before, rate: bool = False):
            if current is None or before is None:
                return None
            result = m_.change(current, before, is_rate=rate)
            return result.text if result else None

        return {
            "period": tu.fmt_period(period),
            "group": group[1] if group else "all groups",
            "company_related_messages": cur.related,
            "not_yet_classified": cur.pending,
            "complaints": cur.complaints,
            "complaint_rate": tu.fmt_pct(cur.complaint_rate),
            "positive_messages": cur.positive,
            "neutral_messages": cur.neutral,
            "requests": {"total": cur.requests, "answered_in_time": cur.answered, "missed": cur.missed,
                         "missed_but_answered_later": cur.late, "never_answered": cur.never_answered,
                         "still_waiting": cur.waiting},
            "answered_in_time_share": tu.fmt_pct(cur.sla),
            "median_answer_time": tu.fmt_duration(cur.median_response_s),
            "p90_answer_time": tu.fmt_duration(cur.p90_response_s),
            "previous_period": {"period": tu.fmt_period(previous), "complaints": prev.complaints,
                                "missed": prev.missed, "company_related_messages": prev.related,
                                "answered_in_time_share": tu.fmt_pct(prev.sla)},
            "change_vs_previous": {"complaints": change(cur.complaints, prev.complaints),
                                   "missed": change(cur.missed, prev.missed),
                                   "answered_in_time_share": change(cur.sla, prev.sla, rate=True)},
        }

    def groups_ranking(self, args: dict) -> dict:
        period = self.period(args)
        previous = period.previous(self._now())
        frame = self._query("groups_overview", period.start_db, period.end_db, previous.start_db, previous.end_db)
        frame = m_.add_risk(frame, self._query("checks", period.start_db, period.end_db), self.settings)
        total, active = len(frame), int((frame["related"] > 0).sum())
        frame["requests"] = frame["answered"] + frame["missed"]
        frame["sla"] = frame["answered"] / frame["requests"].where(frame["requests"] > 0)
        orders = {
            "risk": (["risk", "missed", "complaints"], [False, False, False]),
            "complaints": (["complaints", "risk"], [False, False]),
            "missed": (["missed", "risk"], [False, False]),
            "messages": (["related", "complaints"], [False, False]),
            "answer_rate_worst": (["sla", "missed"], [True, False]),
        }
        sort_by = args.get("sort_by") or "risk"
        if sort_by not in orders:
            raise ToolError(f"sort_by must be one of {', '.join(orders)}.")
        if sort_by == "answer_rate_worst":
            frame = frame[frame["requests"] > 0]
        columns, ascending = orders[sort_by]
        ranked = frame.sort_values(columns, ascending=ascending, na_position="last")
        rows = [{
            "group": str(row.title), "risk_score": int(row.risk), "risk_level": row.risk_level,
            "risk_reasons": list(row.risk_reasons), "complaints": int(row.complaints),
            "complaint_rate": tu.fmt_pct(m_.complaint_rate(row.complaints, row.related)),
            "missed_requests": int(row.missed), "answered_in_time_share": tu.fmt_pct(_seconds(row.sla)),
            "median_answer_time": tu.fmt_duration(_seconds(row.median_response_s)),
            "company_related_messages": int(row.related),
            "manager": row.manager_username if isinstance(row.manager_username, str) else "none",
            "bot_in_group": bool(row.is_present),
        } for row in ranked.head(_limit(args.get("limit"))).itertuples()]
        return {"period": tu.fmt_period(period), "sorted_by": sort_by, "groups_total": total,
                "groups_with_company_messages": active, "rows": rows}

    def categories(self, args: dict) -> dict:
        period, group = self._scope(args)
        frame = self._query("by_category", period.start_db, period.end_db, group[0] if group else None)
        total = int(frame["related"].sum())
        rows = [{"category": row.category, "responsible_department": m_.category_department(row.category),
                 "company_related_messages": int(row.related), "complaints": int(row.complaints),
                 "positive": int(row.positive), "share_of_company_related": tu.fmt_pct(m_.ratio(row.related, total))}
                for row in frame.itertuples()]
        return {"period": tu.fmt_period(period), "group": group[1] if group else "all groups",
                "company_related_total": total, "rows": rows}

    def missed_requests(self, args: dict) -> dict:
        period, group = self._scope(args)
        checks = self._query("checks", period.start_db, period.end_db, group[0] if group else None)
        missed = checks[checks["status"] == m_.CHECK_MISSED].sort_values("started_at", ascending=False)
        rows = [{
            "time": self._local(row.started_at), "group": str(row.title),
            "customer": _person(row.asker_name, row.asker_username), "request": _snippet(row.request_text),
            "answered_later_by": _person(row.later_name, row.later_username) if pd.notna(row.later_at) else "never answered",
            "minutes_until_later_answer": None if pd.isna(row.later_s) else round(float(row.later_s) / 60),
        } for row in missed.head(_limit(args.get("limit"))).itertuples()]
        return {"period": tu.fmt_period(period), "group": group[1] if group else "all groups",
                "missed_total": len(missed), "never_answered": int(missed["later_at"].isna().sum()),
                "answered_later": int(missed["later_at"].notna().sum()), "rows": rows}

    def waiting_now(self, _args: dict) -> dict:
        frame = self._query("waiting_now")
        now = datetime.now(self.settings.tz)
        rows = []
        for row in frame.head(MAX_ROWS).itertuples():
            deadline = tu.from_db(row.deadline, self.settings.tz)
            left = (deadline - now).total_seconds() / 60 if deadline else None
            rows.append({"group": str(row.title), "customer": _person(row.sender_name, row.sender_username),
                         "since": self._local(row.started_at), "request": _snippet(row.request_text),
                         "deadline": f"{int(left) + 1} min left" if left is not None and left > 0
                         else "deadline passed (the bot closes it within a minute)"})
        return {"now": now.strftime("%Y-%m-%d %H:%M"), "waiting_total": len(frame), "rows": rows}

    def complaints(self, args: dict) -> dict:
        period, group = self._scope(args)
        category = self.category(args.get("category"))
        frame = self._query("messages_list", period.start_db, period.end_db, _limit(args.get("limit")),
                            chat_ids=(group[0],) if group else (), category=category, sentiment=m_.COMPLAINT)
        rows = [{"time": self._local(row.sent_at_utc), "group": str(row.title), "category": row.category,
                 "customer": _person(row.sender_name, row.sender_username), "summary": _snippet(row.summary),
                 "text": _snippet(_text(row))} for row in frame.itertuples()]
        return {"period": tu.fmt_period(period), "group": group[1] if group else "all groups",
                "category": category or "all", "newest_first": rows, "more_exist": bool(frame.attrs.get("truncated"))}

    def search_messages(self, args: dict) -> dict:
        text = str(args.get("text") or "").strip()
        if not text:
            raise ToolError("text is required.")
        period, group = self._scope(args)
        category = self.category(args.get("category"))
        sentiment = args.get("sentiment") or None
        if sentiment is not None and sentiment not in m_.SENTIMENTS:
            raise ToolError(f"sentiment must be one of {', '.join(m_.SENTIMENTS)}.")
        frame = self._query("messages_list", period.start_db, period.end_db, _limit(args.get("limit")), text=text,
                            chat_ids=(group[0],) if group else (), category=category, sentiment=sentiment)
        rows = [{"time": self._local(row.sent_at_utc), "group": str(row.title),
                 "author": _person(row.sender_name, row.sender_username), "category": row.category,
                 "sentiment": row.sentiment, "summary": _snippet(row.summary), "text": _snippet(_text(row))}
                for row in frame.itertuples()]
        return {"period": tu.fmt_period(period), "searched_for": text, "newest_first": rows,
                "more_exist": bool(frame.attrs.get("truncated"))}

    def staff(self, args: dict) -> dict:
        period = self.period(args)
        answers = m_.staff_answers(self._query("checks", period.start_db, period.end_db))
        activity = self._query("staff_activity", period.start_db, period.end_db)
        people: dict[int, dict] = {}
        for row in activity.itertuples():
            people[int(row.sender_id)] = {"name": _person(row.name, row.username), "messages_written": int(row.messages),
                                          "groups": int(row.groups), "answered_in_time": 0, "answered_late": 0,
                                          "median_answer_time": "—"}
        if not answers.empty:
            for who, part in answers.groupby("who"):
                entry = people.setdefault(int(who), {
                    "name": OTHERS if who == m_.PEERS else _person(part["name"].iloc[-1], part["username"].iloc[-1]),
                    "messages_written": None, "groups": int(part["chat_id"].nunique())})
                entry["answered_in_time"] = int(part["in_time"].sum())
                entry["answered_late"] = int(part["late"].sum())
                entry["median_answer_time"] = tu.fmt_duration(m_.percentile(part["seconds"], 0.5))
        rows = sorted(people.values(), key=lambda item: (item.get("answered_in_time", 0) + item.get("answered_late", 0),
                                                         item.get("messages_written") or 0), reverse=True)
        return {"period": tu.fmt_period(period), "active_staff": len(activity), "answered_requests": len(answers),
                "rows": rows[:_limit(args.get("limit"), MAX_ROWS)]}

    def daily_trend(self, args: dict) -> dict:
        period, group = self._scope(args)
        if period.days > MAX_TREND_DAYS:
            raise ToolError(f"At most {MAX_TREND_DAYS} days; use a shorter period.")
        frame = self._query("daily", period.start_db, period.end_db, period.offset_modifier(),
                            group[0] if group else None)
        rows = [{"day": row.day, "company_related": int(row.related),
                 "complaints": int(row.complaints), "positive": int(row.positive),
                 "answered_in_time": int(row.answered), "missed": int(row.missed)} for row in frame.itertuples()]
        return {"period": tu.fmt_period(period), "group": group[1] if group else "all groups", "days": rows}

    def report_summaries(self, args: dict) -> dict:
        first, last = _day(args.get("date_from")), _day(args.get("date_to"))
        if not first or not last:
            period = self.period({"period": "selected"})
            report_time = self.settings.report_time
            first = tu.report_day_of(period.start, report_time)
            last = max(first, tu.report_day_of(min(period.end, self._now()) - timedelta(microseconds=1), report_time))
        first, last = min(first, last), max(first, last)
        first = max(first, last - timedelta(days=MAX_SUMMARY_DAYS - 1))
        category = self.category(args.get("category")) if args.get("category") else m_.SUMMARY_OVERALL
        frame = self._query("stored_summaries", first, last, category)
        rows = []
        for row in frame.head(MAX_SUMMARY_DAYS).itertuples():
            start, end = tu.report_bounds(date.fromisoformat(str(row.date)[:10]), self.settings.report_time,
                                          self.settings.tz)
            rows.append({"report_window": f"{start:%Y-%m-%d %H:%M} — {end:%Y-%m-%d %H:%M}",
                         "category": "overall" if row.category == m_.SUMMARY_OVERALL else row.category,
                         "summary": _snippet(row.summary_text, 900)})
        return {"from_report_day": first.isoformat(), "to_report_day": last.isoformat(), "summaries": rows}

    def find_group(self, args: dict) -> dict:
        needle = str(args.get("name") or "").strip().lower()
        titles = [title for _chat, title in self.titles()]
        found = [title for title in titles if needle in title.lower()] if needle else titles
        return {"total_matches": len(found), "matches": found[:40]}


# --------------------------------------------------------------------------- tool definitions

_PERIOD = {
    "period": {"type": "string", "enum": list(PERIODS),
               "description": "selected = the period chosen in the dashboard sidebar (use it when the question names "
                              "no period); today, yesterday, last24h, last7, last30, this_month, last_month; report = "
                              "the bot's last daily report window; custom = date_from..date_to (inclusive)."},
    "date_from": nullable({"type": "string"}, "YYYY-MM-DD, only for period=custom"),
    "date_to": nullable({"type": "string"}, "YYYY-MM-DD, only for period=custom"),
}
_GROUP = {"group": nullable({"type": "string"}, "Group title or a unique part of it; null = all groups")}
_LIMIT = {"limit": nullable({"type": "integer"}, "Rows to return (1-30, default 10)")}


def _category_field() -> dict:
    return {"category": nullable({"type": "string", "enum": list(m_.category_names())}, "null = all categories")}


def build(settings: Settings, context: dict) -> list[Tool]:
    tools = DataTools(settings, context)
    return [
        Tool("overview", "Key numbers for a period, for all groups or one group (messages not about the company "
                         "are never counted): company-related messages, complaints and complaint rate, positive, requests answered in time / missed / "
                         "answered later / waiting, answered-in-time share, median and P90 answer time, and the same "
                         "numbers for the previous period of equal length with the changes.",
             obj({**_PERIOD, **_GROUP}), tools.overview),
        Tool("groups_ranking", "Groups ranked by risk score (default), complaints, missed requests, message volume or "
                               "worst answered-in-time share, with each group's numbers, manager and risk reasons.",
             obj({**_PERIOD, "sort_by": {"type": "string", "enum": ["risk", "complaints", "missed", "messages",
                                                                    "answer_rate_worst"]}, **_LIMIT}),
             tools.groups_ranking),
        Tool("categories", "Company-related messages per category (8 categories) with complaints, positive and share; "
                           "for all groups or one group.", obj({**_PERIOD, **_GROUP}), tools.categories),
        Tool("missed_requests", "Requests nobody answered within the deadline, newest first: time, group, customer, "
                                "what they asked, who answered later and after how many minutes.",
             obj({**_PERIOD, **_GROUP, **_LIMIT}), tools.missed_requests),
        Tool("waiting_now", "Requests waiting for an answer right now (live).", obj({}), tools.waiting_now),
        Tool("complaints", "Latest complaint messages, newest first, optionally for one group and/or category: "
                           "time, group, customer, one-sentence summary and the original text.",
             obj({**_PERIOD, **_GROUP, **_category_field(), **_LIMIT}), tools.complaints),
        Tool("search_messages", "Search customer messages (text, voice transcript, summary) for a word or phrase.",
             obj({"text": {"type": "string"}, **_PERIOD, **_GROUP, **_category_field(),
                  "sentiment": nullable({"type": "string", "enum": list(m_.SENTIMENTS)}), **_LIMIT}),
             tools.search_messages),
        Tool("staff", "Who answers customers: per staff member answers in time, late answers, median answer time, "
                      "groups and messages written; non-staff answerers are one line.",
             obj({**_PERIOD, **_LIMIT}), tools.staff),
        Tool("daily_trend", "Per-day numbers (company-related messages, complaints, positive, answered in "
                            "time, missed) for a period of at most 92 days.",
             obj({**_PERIOD, **_GROUP}), tools.daily_trend),
        Tool("report_summaries", "The AI summaries the bot stored with its daily Excel reports (overall or one "
                                 "category). Without dates: the report days of the sidebar period.",
             obj({"date_from": nullable({"type": "string"}, "YYYY-MM-DD report day"),
                  "date_to": nullable({"type": "string"}, "YYYY-MM-DD report day"), **_category_field()}),
             tools.report_summaries),
        Tool("find_group", "Find group titles containing a text (empty text lists all groups).",
             obj({"name": {"type": "string"}}), tools.find_group),
    ]
