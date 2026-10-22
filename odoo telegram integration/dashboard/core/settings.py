"""Dashboard settings.

Values come from dashboard/.env (the dashboard's own file; the bot's .env is never opened) on top of
the defaults below. Any key can be overridden by an OS environment variable prefixed with
``DASHBOARD_`` (for example ``DASHBOARD_DEMO_MODE=1``); run_dashboard.ps1 -Demo and the tests use this.
Relative paths are resolved against the dashboard folder (the project path contains spaces).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone, tzinfo
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DASHBOARD_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = DASHBOARD_DIR / ".env"
OVERRIDE_PREFIX = "DASHBOARD_"

DEFAULTS: dict[str, str] = {
    "DB_PATH": "../data/monitor.db",
    "REPORTS_DIR": "../reports",
    "BACKUP_DIR": "../backups",
    # pictures and GIFs the bot saved from company-related conversations (read-only here)
    "MEDIA_DIR": "../data/media",
    "DEMO_MODE": "0",
    "DEMO_DB_PATH": "demo/demo_monitor.db",
    "TIMEZONE": "Asia/Tashkent",
    "REPORT_TIME": "11:00",
    "SLA_MINUTES": "5",
    "WORK_HOURS": "09:00-19:00",
    "DASHBOARD_PASSWORD": "",
    "DASHBOARD_HOST": "127.0.0.1",
    "DASHBOARD_PORT": "8502",
    "CACHE_TTL_OPEN": "60",
    "CACHE_TTL_CLOSED": "600",
    "QUERY_TIMEOUT_SECONDS": "10",
    "RESULT_LIMIT": "1000",
    "FEED_LIMIT": "300",
    "SILENT_DAYS": "7",
    "FRESHNESS_YELLOW_MINUTES": "120",
    "FRESHNESS_RED_MINUTES": "720",
    "PENDING_STALE_MINUTES": "30",
    "RISK_W_MISSED": "0.35",
    "RISK_W_RATE": "0.25",
    "RISK_W_GROWTH": "0.20",
    "RISK_W_SLOW": "0.20",
    "RISK_CAP_MISSED": "5",
    "RISK_CAP_RATE": "0.4",
    "RISK_CAP_GROWTH": "1.0",
    "RISK_CAP_SLOW_MINUTES": "30",
    "RISK_MIN_RELATED": "3",
    "RISK_RED": "70",
    "RISK_YELLOW": "40",
    # the dashboard's own read-write file: layout changes and chat history (never the bot's database)
    "STATE_DB_PATH": "data/state.db",
    # AI assistant (chat button in the bottom-right corner): provider openai | anthropic
    "CHAT_ENABLED": "1",
    "CHAT_PROVIDER": "openai",
    "OPENAI_API_KEY": "",
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_WORKSPACE_ID": "",
    "CHAT_MODEL": "",
    "CHAT_EFFORT": "low",
    "CHAT_FALLBACKS": "1",
    "CHAT_MAX_STEPS": "6",
    "CHAT_CONTEXT_MESSAGES": "20",
    "CHAT_DAILY_LIMIT": "200",
    # Agents created on the «AI agentlar» page
    "AGENT_BUILDER_MODEL": "",
    "AGENT_MAX": "20",
    "AGENT_ODOO_MODELS": ("res.partner,sale.order,sale.order.line,account.move,crm.lead,product.product,"
                          "product.template,stock.quant,purchase.order,project.task,res.users"),
    "AGENT_BOT_DAILY_LIMIT": "200",
    "AGENT_BOT_HISTORY": "12",
    "PROTECTED_BOT_USERNAMES": "dunyabunya_assistant_bot,db_hisobchi_bot,david_prototype_bot",
}


def parse_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser (comments, blank lines and optional quotes)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


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
        hours, minutes = value.strip().split(":")
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


DEFAULT_MODELS = {"openai": "gpt-5.4-mini", "anthropic": "claude-opus-5-5"}


@dataclass(frozen=True)
class ChatConfig:
    """AI assistant of the chat (assistant package). ``model`` empty in .env = the provider's default."""

    enabled: bool
    provider: str
    model: str
    effort: str
    fallbacks: bool
    max_steps: int
    context_messages: int
    daily_limit: int
    workspace_id: str = ""
    openai_api_key: str = field(default="", repr=False)
    anthropic_api_key: str = field(default="", repr=False)

    @property
    def api_key(self) -> str:
        return self.anthropic_api_key if self.provider == "anthropic" else self.openai_api_key

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.api_key)


@dataclass(frozen=True)
class AgentsConfig:
    """Agents made on the «AI agentlar» page. ``builder_model`` empty = models.builder_default()."""

    builder_model: str
    max_agents: int
    odoo_models: tuple[str, ...]
    bot_daily_limit: int
    bot_history: int
    protected_bots: tuple[str, ...]


def _list(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))


@dataclass(frozen=True)
class Settings:
    db_path: Path
    real_db_path: Path
    demo: bool
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
    chat: ChatConfig
    agents: AgentsConfig
    password: str = field(default="", repr=False)

    @property
    def tz(self) -> tzinfo:
        return get_tz(self.tz_name)

    @property
    def is_local_host(self) -> bool:
        return self.host in ("127.0.0.1", "localhost", "::1")


def _raw_values() -> dict[str, str]:
    values = dict(DEFAULTS)
    values.update(parse_env_file(ENV_FILE))
    for key in DEFAULTS:
        env_name = key if key.startswith(OVERRIDE_PREFIX) else OVERRIDE_PREFIX + key
        if env_name in os.environ:
            values[key] = os.environ[env_name]
    return values


def _flag(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    v = _raw_values()
    num = lambda key: float(v[key])  # noqa: E731
    demo = _flag(v["DEMO_MODE"])
    real_db = _resolve(v["DB_PATH"])
    work_start, _, work_end = v["WORK_HOURS"].partition("-")
    provider = v["CHAT_PROVIDER"].strip().lower()
    provider = provider if provider in DEFAULT_MODELS else "openai"
    return Settings(
        db_path=_resolve(v["DEMO_DB_PATH"]) if demo else real_db,
        real_db_path=real_db,
        demo=demo,
        reports_dir=_resolve(v["REPORTS_DIR"]),
        backup_dir=_resolve(v["BACKUP_DIR"]),
        media_dir=_resolve(v["MEDIA_DIR"]),
        tz_name=v["TIMEZONE"].strip() or "Asia/Tashkent",
        report_time=_hhmm(v["REPORT_TIME"], time(0, 0)),
        sla_minutes=int(num("SLA_MINUTES")),
        work_start=_hhmm(work_start, time(9, 0)),
        work_end=_hhmm(work_end, time(19, 0)),
        host=v["DASHBOARD_HOST"].strip() or "127.0.0.1",
        port=int(num("DASHBOARD_PORT")),
        ttl_open=int(num("CACHE_TTL_OPEN")),
        ttl_closed=int(num("CACHE_TTL_CLOSED")),
        query_timeout=num("QUERY_TIMEOUT_SECONDS"),
        result_limit=int(num("RESULT_LIMIT")),
        feed_limit=int(num("FEED_LIMIT")),
        silent_days=int(num("SILENT_DAYS")),
        fresh_yellow_min=int(num("FRESHNESS_YELLOW_MINUTES")),
        fresh_red_min=int(num("FRESHNESS_RED_MINUTES")),
        pending_stale_min=int(num("PENDING_STALE_MINUTES")),
        risk=RiskConfig(
            w_missed=num("RISK_W_MISSED"), w_rate=num("RISK_W_RATE"), w_growth=num("RISK_W_GROWTH"),
            w_slow=num("RISK_W_SLOW"), cap_missed=num("RISK_CAP_MISSED"), cap_rate=num("RISK_CAP_RATE"),
            cap_growth=num("RISK_CAP_GROWTH"), cap_slow_minutes=num("RISK_CAP_SLOW_MINUTES"),
            min_related=int(num("RISK_MIN_RELATED")), red=int(num("RISK_RED")), yellow=int(num("RISK_YELLOW")),
        ),
        state_db_path=_resolve(v["STATE_DB_PATH"]),
        chat=ChatConfig(
            enabled=_flag(v["CHAT_ENABLED"]), provider=provider,
            model=v["CHAT_MODEL"].strip() or DEFAULT_MODELS[provider],
            effort=v["CHAT_EFFORT"].strip() or DEFAULTS["CHAT_EFFORT"], fallbacks=_flag(v["CHAT_FALLBACKS"]),
            max_steps=max(2, int(num("CHAT_MAX_STEPS"))), context_messages=max(0, int(num("CHAT_CONTEXT_MESSAGES"))),
            daily_limit=max(0, int(num("CHAT_DAILY_LIMIT"))), workspace_id=v["ANTHROPIC_WORKSPACE_ID"].strip(),
            openai_api_key=v["OPENAI_API_KEY"].strip(), anthropic_api_key=v["ANTHROPIC_API_KEY"].strip(),
        ),
        agents=AgentsConfig(
            builder_model=v["AGENT_BUILDER_MODEL"].strip(), max_agents=max(0, int(num("AGENT_MAX"))),
            odoo_models=_list(v["AGENT_ODOO_MODELS"]), bot_daily_limit=max(0, int(num("AGENT_BOT_DAILY_LIMIT"))),
            bot_history=max(0, int(num("AGENT_BOT_HISTORY"))),
            protected_bots=tuple(name.lstrip("@").lower() for name in _list(v["PROTECTED_BOT_USERNAMES"])),
        ),
        password=v["DASHBOARD_PASSWORD"],
    )
