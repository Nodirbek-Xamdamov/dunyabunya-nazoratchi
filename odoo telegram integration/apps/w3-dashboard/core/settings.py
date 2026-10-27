"""Dashboard settings.

Values come from the shared Genesis config (config.toml: postgres host/port, media path) and the window's
own apps/w3-dashboard/app.toml (cfg["app"]), read through genesis_common.load_config. No secrets live here:
the database password is read only when a connection is opened (core.db, genesis_common.read_secret_env).
Any app.toml key can be overridden by an OS environment variable ``W3_<KEY>`` (for example
``W3_DATABASE=genesis``); the tests use this. Relative paths are resolved against the dashboard folder.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone, tzinfo
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from genesis_common import find_root, load_config

DASHBOARD_DIR = Path(__file__).resolve().parent.parent
APP_NAME = "w3-dashboard"
OVERRIDE_PREFIX = "W3_"
REAL_DATABASE = "genesis"


@lru_cache(maxsize=8)
def get_tz(name: str) -> tzinfo:
    """IANA zone (needs the tzdata package on Windows). Without it Asia/Tashkent falls back to its
    fixed UTC+5 offset (no daylight saving time there); other names fall back to the machine zone."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        if name == "Asia/Tashkent":
            return timezone(timedelta(hours=5), name)
        return datetime.now().astimezone().tzinfo


def _resolve(value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else DASHBOARD_DIR / path).resolve()


def _hhmm(value: str, default: time) -> time:
    try:
        hours, minutes = str(value).strip().split(":")
        return time(int(hours), int(minutes))
    except (ValueError, AttributeError):
        return default


@dataclass(frozen=True)
class RiskConfig:
    w_missed: float
    w_rate: float
    w_growth: float
    w_slow: float
    cap_missed: float
    cap_rate: float
    cap_growth: float
    cap_slow_minutes: float
    min_related: int
    red: int
    yellow: int


@dataclass(frozen=True)
class DbConfig:
    """Connection to the Genesis PostgreSQL (SCHEMA.md §1). The password is not kept here."""

    host: str
    port: int
    database: str
    role: str

    @property
    def label(self) -> str:
        return f"{self.database}@{self.host}:{self.port}"


@dataclass(frozen=True)
class Settings:
    db: DbConfig
    demo: bool  # True on a test database (genesis_test): the page shows the "test data" banner
    reports_dir: Path
    backup_dir: Path
    media_dir: Path
    tz_name: str
    report_time: time
    sla_minutes: int
    work_start: time
    work_end: time
    host: str
    port: int
    ttl_open: int
    ttl_closed: int
    query_timeout: float
    result_limit: int
    feed_limit: int
    silent_days: int
    fresh_yellow_min: int
    fresh_red_min: int
    pending_stale_min: int
    risk: RiskConfig
    state_db_path: Path
    frames_dir: Path  # W3-04: camera event frames (D-29 layout); synthetic ones on the test database
    show_frames: bool  # W3-04: frames on the camera page; W3-05: only for users with the camera right
    access_log: Path  # W3-05: who saw what (JSON lines) until W0 offers a function for core.audit_log
    password: str = field(default="", repr=False)

    @property
    def tz(self) -> tzinfo:
        return get_tz(self.tz_name)

    @property
    def is_local_host(self) -> bool:
        return self.host in ("127.0.0.1", "localhost", "::1")


def _override(key: str, value):
    name = OVERRIDE_PREFIX + key.upper()
    return os.environ[name] if name in os.environ else value


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    cfg = load_config(APP_NAME, root=find_root(DASHBOARD_DIR))
    app = cfg.get("app", {})
    db, server, dash, risk, cam = (app.get(part, {}) for part in ("db", "server", "dashboard", "risk", "camera"))
    pg = cfg["postgres"]
    database = str(_override("database", db.get("database", "genesis_test")))
    frames_key = "frames_dir" if database == REAL_DATABASE else "fixture_frames_dir"
    work_start, _, work_end = str(dash.get("work_hours", "09:00-19:00")).partition("-")
    num = lambda section, key, default: float(_override(key, section.get(key, default)))  # noqa: E731
    return Settings(
        db=DbConfig(host=str(pg["host"]), port=int(pg["port"]), database=database,
                    role=str(db.get("role", "w3_dashboard"))),
        demo=database != REAL_DATABASE,
        reports_dir=_resolve(str(_override("reports_dir", dash.get("reports_dir", "reports")))),
        backup_dir=cfg.path("paths.backup"),
        media_dir=cfg.path("paths.media"),
        tz_name=str(cfg.get_dotted("project.timezone", "Asia/Tashkent")),
        report_time=_hhmm(dash.get("report_time", "00:00"), time(0, 0)),
        sla_minutes=int(num(dash, "sla_minutes", 5)),
        work_start=_hhmm(work_start, time(9, 0)),
        work_end=_hhmm(work_end, time(19, 0)),
        host=str(_override("host", server.get("host", "127.0.0.1"))),
        port=int(num(server, "port", 8503)),
        ttl_open=int(num(dash, "cache_ttl_open", 60)),
        ttl_closed=int(num(dash, "cache_ttl_closed", 600)),
        query_timeout=num(db, "query_timeout_seconds", 10),
        result_limit=int(num(db, "result_limit", 1000)),
        feed_limit=int(num(db, "feed_limit", 300)),
        silent_days=int(num(dash, "silent_days", 7)),
        fresh_yellow_min=int(num(dash, "freshness_yellow_minutes", 120)),
        fresh_red_min=int(num(dash, "freshness_red_minutes", 720)),
        pending_stale_min=int(num(dash, "pending_stale_minutes", 30)),
        risk=RiskConfig(
            w_missed=num(risk, "w_missed", 0.35), w_rate=num(risk, "w_rate", 0.25),
            w_growth=num(risk, "w_growth", 0.20), w_slow=num(risk, "w_slow", 0.20),
            cap_missed=num(risk, "cap_missed", 5), cap_rate=num(risk, "cap_rate", 0.4),
            cap_growth=num(risk, "cap_growth", 1.0), cap_slow_minutes=num(risk, "cap_slow_minutes", 30),
            min_related=int(num(risk, "min_related", 3)), red=int(num(risk, "red", 70)),
            yellow=int(num(risk, "yellow", 40)),
        ),
        state_db_path=_resolve(str(_override("state_db", dash.get("state_db", "data/state.db")))),
        frames_dir=_resolve(str(_override("frames_dir", cam.get(frames_key, "data/fixture_frames")))),
        show_frames=str(_override("show_frames", cam.get("show_frames", True))).lower() in ("true", "1", "yes"),
        access_log=_resolve(str(_override("access_log", dash.get("access_log", "logs/access.jsonl")))),
        password=str(server.get("password", "")),
    )
