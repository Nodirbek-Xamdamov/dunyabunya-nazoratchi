"""Logging setup: JSON lines in <paths.logs>/<app>/<app>.jsonl, daily rotation, redaction."""
from __future__ import annotations

import json
import logging
import logging.handlers
import re
from datetime import datetime, timezone

from .config import Config, load_config
from .secrets import known_secrets

_PATTERNS = [
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1***@"),  # rtsp://user:pass@
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"), "***"),  # Telegram bot token
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "***"),  # API keys of the sk-... form
    (re.compile(r"(?i)\b(password|passwd|pwd|token|api[_-]?key|secret)(\s*[=:]\s*)([^\s,;&\"']+)"), r"\1\2***"),
]

# Attributes every LogRecord has; anything else came from `extra=`.
_STD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


def redact(text: str) -> str:
    for value in known_secrets():
        text = text.replace(value, "***")
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class JsonFormatter(logging.Formatter):
    def __init__(self, app: str):
        super().__init__()
        self.app = app

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "app": self.app,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STD_ATTRS and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(entry, ensure_ascii=False, default=str))


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(app: str, cfg: Config | None = None) -> logging.Logger:
    """Configure the root logger once per process and return the `app` logger."""
    cfg = cfg or load_config(app)
    log_cfg = cfg.get("logging", {})
    folder = cfg.path("paths.logs") / app
    folder.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_genesis", False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(str(log_cfg.get("level", "INFO")).upper())

    file_handler = logging.handlers.TimedRotatingFileHandler(
        folder / f"{app}.jsonl",
        when=str(log_cfg.get("rotate", "midnight")),
        backupCount=int(log_cfg.get("keep_days", 30)),
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonFormatter(app))
    handlers: list[logging.Handler] = [file_handler]
    if log_cfg.get("console", True):
        console = logging.StreamHandler()
        console.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handlers.append(console)
    for handler in handlers:
        handler._genesis = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    return logging.getLogger(app)
