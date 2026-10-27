"""Known data issues (config/known_issues.toml) that overlap a period."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from core.settings import DASHBOARD_DIR


@dataclass(frozen=True)
class Issue:
    title: str
    text: str
    severity: str
    start: date | None
    end: date | None


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


@lru_cache(maxsize=1)
def known_issues() -> tuple[Issue, ...]:
    path = DASHBOARD_DIR / "config" / "known_issues.toml"
    try:
        items = tomllib.loads(path.read_text(encoding="utf-8")).get("issue", [])
    except (OSError, tomllib.TOMLDecodeError):
        return ()
    return tuple(Issue(str(i.get("title", "")), str(i.get("text", "")), str(i.get("severity", "info")),
                       _day(i.get("start")), _day(i.get("end"))) for i in items if i.get("title"))


def issues_for(first: date, last: date) -> list[Issue]:
    return [issue for issue in known_issues()
            if (issue.start is None or issue.start <= last) and (issue.end is None or issue.end >= first)]
