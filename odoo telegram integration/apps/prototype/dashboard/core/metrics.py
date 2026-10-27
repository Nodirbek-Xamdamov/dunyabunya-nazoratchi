"""Metric definitions -- the one place where every dashboard number is defined.

They mirror the bot's Excel report (app/monitor_db.py + app/monitor_report.py, re-implemented, not
imported):
* only CUSTOMER messages count (messages.is_staff = 0);
* "kompaniyaga oid" = classification.is_company_related = 1; an unknown/empty category counts as "Boshqa";
  neutral = related - complaints - positive (the report applies the same correction);
* "filtrlangan" = is_company_related = 0; "kutmoqda" / "tasniflab bo'lmadi" = message status pending / failed;
* requests = manager_checks by started_at; answered / missed / pending = check status;
* complaint rate = complaints / related; category share = category / related;
* SLA % = answered / (answered + missed) -- pending checks are excluded;
* response time = answer time - request time. "Answered" checks use the bot's answered_at; for missed
  checks the first later answer is found with the bot's "someone answered" rule (staff message, a reply
  to the customer, or another person's classified non-request message). Median / P90 use both.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache

import pandas as pd

from core.settings import DASHBOARD_DIR, RiskConfig, Settings

COMPLAINT, POSITIVE, NEUTRAL = "Shikoyat", "Ijobiy", "Neytral"
SENTIMENTS = (COMPLAINT, POSITIVE, NEUTRAL)
OTHER_CATEGORY = "Boshqa"
POSITIVE_CATEGORY = "Ijobiy Fikrlar"
CHECK_PENDING, CHECK_ANSWERED, CHECK_MISSED = "pending", "answered", "missed"
MSG_PENDING, MSG_DONE, MSG_STAFF, MSG_FAILED = "pending", "done", "staff", "failed"
SPEECH_MEDIA = ("voice", "audio", "video", "video_note")
RULE_MODEL = "rule"
SUMMARY_OVERALL = "umumiy"
STAT_TOTAL = "Jami kompaniyaga oid"
STAT_FILTERED = "Kompaniyaga aloqasiz (filtrlangan)"
STAT_MISSED = "Javobsiz so'rovlar"
SMALL_BASE = 5  # below this a change is shown as an absolute number, not a percentage

_DEFAULT_CATEGORIES = (
    ("Dastavka va Logistika", "#3B82F6", "Logistika bo'limi"),
    ("Mahsulot Sifati va Nuqsonlar", "#F97316", "Sifat nazorati / ombor"),
    ("Komplektatsiya va Hajm", "#A855F7", "Ombor (komplektatsiya)"),
    ("Xodimlar va Xizmat Ko'rsatish", "#EC4899", "Savdo bo'limi rahbari"),
    ("Narx va To'lovlar", "#EAB308", "Moliya / buxgalteriya"),
    ("Qaytarish va Kafolat", "#14B8A6", "Savdo bo'limi"),
    ("Ijobiy Fikrlar", "#22C55E", "Marketing"),
)  # prototip (D-33): "Boshqa" yo'q


@dataclass(frozen=True)
class Category:
    name: str
    color: str
    department: str


@lru_cache(maxsize=1)
def categories() -> tuple[Category, ...]:
    path = DASHBOARD_DIR / "config" / "categories.toml"
    try:
        items = tomllib.loads(path.read_text(encoding="utf-8")).get("category", [])
        loaded = tuple(Category(str(i["name"]), str(i.get("color", "#94A3B8")), str(i.get("department", "")))
                       for i in items if i.get("name"))
        if loaded:
            return loaded
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError):
        pass
    return tuple(Category(*item) for item in _DEFAULT_CATEGORIES)


def category_names() -> tuple[str, ...]:
    return tuple(item.name for item in categories())


def category_color(name: str) -> str:
    return next((item.color for item in categories() if item.name == name), "#94A3B8")


def category_department(name: str) -> str:
    return next((item.department for item in categories() if item.name == name), "")


# --------------------------------------------------------------------------- basic ratios


def ratio(numerator, denominator) -> float | None:
    if not denominator:
        return None
    return float(numerator) / float(denominator)


def complaint_rate(complaints, related) -> float | None:
    return ratio(complaints, related)


def sla_share(answered, missed) -> float | None:
    return ratio(answered, (answered or 0) + (missed or 0))


def percentile(values, q: float) -> float | None:
    series = pd.Series(values, dtype="float64").dropna()
    return None if series.empty else float(series.quantile(q))


@dataclass
class Kpis:
    customer: int = 0
    related: int = 0
    complaints: int = 0
    positive: int = 0
    filtered: int = 0
    pending: int = 0
    failed: int = 0
    answered: int = 0
    missed: int = 0
    waiting: int = 0
    late: int = 0  # missed requests that got an answer later
    median_response_s: float | None = None
    p90_response_s: float | None = None

    @property
    def neutral(self) -> int:
        return max(0, self.related - self.complaints - self.positive)

    @property
    def complaint_rate(self) -> float | None:
        return complaint_rate(self.complaints, self.related)

    @property
    def sla(self) -> float | None:
        return sla_share(self.answered, self.missed)

    @property
    def requests(self) -> int:
        return self.answered + self.missed + self.waiting

    @property
    def never_answered(self) -> int:
        return max(0, self.missed - self.late)


def response_durations(checks: pd.DataFrame) -> pd.Series:
    """Seconds from request to answer: in-time answers and late answers of missed requests."""
    if checks.empty:
        return pd.Series(dtype="float64")
    return pd.concat([checks["response_s"], checks["later_s"]]).dropna()


def build_kpis(totals: dict, checks: pd.DataFrame | None = None) -> Kpis:
    kpis = Kpis(**{key: int(totals.get(key) or 0) for key in (
        "customer", "related", "complaints", "positive", "filtered", "pending", "failed", "answered", "missed", "waiting")})
    if checks is not None and not checks.empty:
        durations = response_durations(checks)
        kpis.median_response_s = percentile(durations, 0.5)
        kpis.p90_response_s = percentile(durations, 0.9)
        kpis.late = int(((checks["status"] == CHECK_MISSED) & checks["later_s"].notna()).sum())
    return kpis


# --------------------------------------------------------------------------- changes


@dataclass(frozen=True)
class Change:
    text: str  # "+3", "-25%", "+4 p.p."
    sign: int  # -1, 0, 1


def change(current, previous, *, is_rate: bool = False) -> Change | None:
    """Change vs the previous period. Rates change in percentage points; for small bases (< 5)
    an absolute difference is shown instead of a misleading percentage."""
    if current is None or previous is None:
        return None
    if isinstance(current, float) and math.isnan(current):
        return None
    diff = current - previous
    sign = (diff > 0) - (diff < 0)
    if is_rate:
        return Change(f"{diff * 100:+.0f} p.p.".replace("+0 ", "0 ").replace("-0 ", "0 "), sign)
    if previous < SMALL_BASE:
        return Change(f"{diff:+.0f}" if diff else "0", sign)
    return Change(f"{diff / previous * 100:+.0f}%", sign)


# --------------------------------------------------------------------------- risk score


@dataclass
class Risk:
    score: int
    level: str  # "red" | "yellow" | "green"
    parts: dict[str, int] = field(default_factory=dict)  # points per component (sum = score)
    reasons: list[str] = field(default_factory=list)


def risk_level(score: float, cfg: RiskConfig) -> str:
    if score >= cfg.red:
        return "red"
    if score >= cfg.yellow:
        return "yellow"
    return "green"


def risk_score(*, missed: int, complaints: int, related: int, prev_complaints: int,
               median_response_s: float | None, cfg: RiskConfig, sla_minutes: int) -> Risk:
    """0-100 group risk: missed requests, complaint rate, complaint growth, slow answers.
    Each component is scaled to 0..1 by its cap, weighted, and the weights are normalised."""
    x_missed = min(missed / cfg.cap_missed, 1.0) if cfg.cap_missed else 0.0
    rate = complaints / related if related >= cfg.min_related else 0.0
    x_rate = min(rate / cfg.cap_rate, 1.0) if cfg.cap_rate else 0.0
    if prev_complaints >= SMALL_BASE:
        growth = (complaints - prev_complaints) / prev_complaints
    else:
        growth = (complaints - prev_complaints) / SMALL_BASE
    x_growth = min(max(growth, 0.0) / cfg.cap_growth, 1.0) if cfg.cap_growth else 0.0
    slow_minutes = 0.0 if median_response_s is None else max(median_response_s / 60 - sla_minutes, 0.0)
    x_slow = min(slow_minutes / cfg.cap_slow_minutes, 1.0) if cfg.cap_slow_minutes else 0.0
    weights = {"missed": cfg.w_missed, "rate": cfg.w_rate, "growth": cfg.w_growth, "slow": cfg.w_slow}
    total_weight = sum(weights.values()) or 1.0
    values = {"missed": x_missed, "rate": x_rate, "growth": x_growth, "slow": x_slow}
    parts = {key: round(100 * weights[key] * values[key] / total_weight) for key in weights}
    score = min(100, sum(parts.values()))
    reasons = []
    if missed:
        reasons.append(f"{missed} ta javobsiz")
    if complaints and complaints > prev_complaints:
        reasons.append(f"shikoyat {change(complaints, prev_complaints).text}")
    if x_rate >= 0.5:
        reasons.append(f"shikoyat {round(rate * 100)}%")
    if x_slow > 0 and median_response_s is not None:
        reasons.append(f"median javob {round(median_response_s / 60)} daq")
    return Risk(score, risk_level(score, cfg), parts, reasons)


PEERS = -1  # one "who" for all answering participants that are not staff


def staff_answers(checks: pd.DataFrame) -> pd.DataFrame:
    """One row per answered request: who answered (first answering message) and how fast; answers of
    non-staff participants are merged under ``who == PEERS``."""
    in_time = checks[(checks["status"] == CHECK_ANSWERED) & checks["resp_sender"].notna()]
    late = checks[(checks["status"] == CHECK_MISSED) & checks["later_sender"].notna()]
    parts = [
        pd.DataFrame({"who": in_time["resp_sender"], "name": in_time["resp_name"], "username": in_time["resp_username"],
                      "is_staff": in_time["resp_is_staff"], "seconds": in_time["response_s"],
                      "chat_id": in_time["chat_id"], "in_time": 1, "late": 0}),
        pd.DataFrame({"who": late["later_sender"], "name": late["later_name"], "username": late["later_username"],
                      "is_staff": late["later_is_staff"], "seconds": late["later_s"], "chat_id": late["chat_id"],
                      "in_time": 0, "late": 1}),
    ]
    answers = pd.concat(parts, ignore_index=True)
    if answers.empty:
        return answers
    answers["is_staff"] = answers["is_staff"].fillna(0).astype(int).astype(bool)
    answers.loc[~answers["is_staff"], "who"] = PEERS
    return answers


def group_median_response(checks: pd.DataFrame) -> dict[int, float]:
    if checks.empty:
        return {}
    frame = pd.concat([
        checks[["chat_id", "response_s"]].rename(columns={"response_s": "s"}),
        checks[["chat_id", "later_s"]].rename(columns={"later_s": "s"}),
    ]).dropna()
    return frame.groupby("chat_id")["s"].median().to_dict()


def add_risk(groups: pd.DataFrame, checks: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    """Adds risk, risk_level, risk_parts, risk_reasons, median_response_s to the group table."""
    frame = groups.copy()
    medians = group_median_response(checks)
    frame["median_response_s"] = frame["chat_id"].map(medians)
    risks = [
        risk_score(missed=int(row.missed), complaints=int(row.complaints), related=int(row.related),
                   prev_complaints=int(row.prev_complaints),
                   median_response_s=None if pd.isna(row.median_response_s) else float(row.median_response_s),
                   cfg=settings.risk, sla_minutes=settings.sla_minutes)
        for row in frame.itertuples()
    ]
    frame["risk"] = [risk.score for risk in risks]
    frame["risk_level"] = [risk.level for risk in risks]
    frame["risk_parts"] = [risk.parts for risk in risks]
    frame["risk_reasons"] = [risk.reasons for risk in risks]
    return frame


# --------------------------------------------------------------------------- overall status


LEVEL_ORDER = {"green": 0, "yellow": 1, "red": 2}


def freshness_level(age_minutes: float | None, settings: Settings) -> str:
    if age_minutes is None:
        return "red"
    if age_minutes >= settings.fresh_red_min:
        return "red"
    if age_minutes >= settings.fresh_yellow_min:
        return "yellow"
    return "green"


def overall_status(kpis: Kpis, previous: Kpis | None, freshness: str) -> tuple[str, list[str]]:
    """Is everything fine? Green / yellow / red with short reasons (missed requests, complaint
    growth, data delay)."""
    level, reasons = "green", []

    def raise_to(new_level: str, reason: str) -> None:
        nonlocal level
        reasons.append(reason)
        if LEVEL_ORDER[new_level] > LEVEL_ORDER[level]:
            level = new_level

    sla = kpis.sla
    if kpis.missed >= 5 or (sla is not None and sla < 0.7 and kpis.missed >= 2):
        raise_to("red", f"{kpis.missed} ta so'rov javobsiz qoldi")
    elif kpis.missed >= 1:
        raise_to("yellow", f"{kpis.missed} ta so'rov javobsiz qoldi")
    if previous is not None and kpis.complaints >= 3 and kpis.complaints > previous.complaints:
        delta = change(kpis.complaints, previous.complaints)
        growth = (kpis.complaints - previous.complaints) / max(previous.complaints, 1)
        if kpis.complaints >= 5 and growth >= 1.0:
            raise_to("red", f"shikoyatlar keskin ko'paydi ({delta.text})")
        elif growth >= 0.5:
            raise_to("yellow", f"shikoyatlar ko'paydi ({delta.text})")
    if freshness == "red":
        raise_to("red", "ma'lumot uzoq vaqtdan beri yangilanmagan")
    elif freshness == "yellow":
        raise_to("yellow", "ma'lumot kechikmoqda")
    return level, reasons
