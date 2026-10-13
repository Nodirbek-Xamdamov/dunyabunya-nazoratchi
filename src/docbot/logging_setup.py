import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from docbot.redaction import redact

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# Chatty libraries: their DEBUG output would drown ours (and echo request details).
_QUIET_LOGGERS = ("aiosqlite", "asyncio", "urllib3", "httpx", "httpcore")


class RedactingFormatter(logging.Formatter):
    """Masks secrets in the final text, so messages, arguments, tracebacks and stack info are all covered."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(
    level: str = "INFO",
    *,
    log_dir: Path | None = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 10,
    console: bool = True,
) -> None:
    """(Re)configure the root logger. Safe to call more than once.

    ``console=False`` (background service with a log file) keeps redirected stderr small: it then only
    receives what bypasses logging, such as a crash traceback.
    """
    formatter = RedactingFormatter(LOG_FORMAT)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)] if console or log_dir is None else []
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(log_dir / "docbot.log", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
        )

    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
        old.close()
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(level.upper())

    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
