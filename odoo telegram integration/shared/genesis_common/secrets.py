"""Secret files from <root>/secrets/. Values are registered for log redaction.

An environment variable GENESIS_SECRET_<NAME> (non-alphanumerics -> "_", upper case)
overrides the file, so tests and services can inject a value without touching secrets/.
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

from .config import Config, ConfigError, load_config

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_lock = threading.Lock()
_known_values: set[str] = set()

MIN_REDACT_LEN = 4  # shorter values would mask ordinary words


class SecretError(ConfigError):
    pass


def register_secret(value: str) -> None:
    if value and len(value) >= MIN_REDACT_LEN:
        with _lock:
            _known_values.add(value)


def known_secrets() -> list[str]:
    with _lock:
        return sorted(_known_values, key=len, reverse=True)


def _env_name(name: str) -> str:
    return "GENESIS_SECRET_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def _secret_path(name: str, cfg: Config | None) -> Path:
    if not _NAME_RE.match(name) or ".." in name:
        raise SecretError(f"invalid secret name: {name!r}")
    cfg = cfg or load_config()
    return cfg.path("paths.secrets") / name


def read_secret(name: str, cfg: Config | None = None) -> str:
    """Return the stripped text of secrets/<name> (or its env override)."""
    env = os.environ.get(_env_name(name))
    if env is not None:
        value = env.strip()
    else:
        path = _secret_path(name, cfg)
        if not path.is_file():
            # Only the name is reported, never any content.
            raise SecretError(f"secret not found: {name}")
        value = path.read_text(encoding="utf-8-sig").strip()
    register_secret(value)
    return value


def read_secret_env(name: str, cfg: Config | None = None) -> dict[str, str]:
    """Parse secrets/<name> as KEY=VALUE lines (# comments, optional quotes)."""
    out: dict[str, str] = {}
    for raw in read_secret(name, cfg).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
        register_secret(value)
    return out
