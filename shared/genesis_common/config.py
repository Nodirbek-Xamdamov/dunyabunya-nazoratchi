"""Shared configuration loader.

Order (later wins): <root>/config.toml -> <root>/config.local.toml.
Window-specific settings live in apps/<app>/app.toml and are exposed as cfg["app"].
Secrets are never stored here; see genesis_common.secrets.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

ROOT_ENV = "GENESIS_ROOT"
CONFIG_FILE = "config.toml"
LOCAL_CONFIG_FILE = "config.local.toml"
APP_CONFIG_FILE = "app.toml"


class ConfigError(RuntimeError):
    pass


class Config(dict):
    """Plain dict of settings plus helpers for dotted keys and paths."""

    def __init__(self, data: dict[str, Any], root: Path, app: str | None):
        super().__init__(data)
        self.root = root
        self.app = app

    def get_dotted(self, key: str, default: Any = None) -> Any:
        node: Any = self
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, key: str) -> Any:
        value = self.get_dotted(key, _MISSING)
        if value is _MISSING:
            raise ConfigError(f"missing config key: {key}")
        return value

    def path(self, key: str) -> Path:
        """Return a configured path; relative values are resolved against the root."""
        p = Path(str(self.require(key)))
        return p if p.is_absolute() else self.root / p


_MISSING = object()


def find_root(start: Path | None = None) -> Path:
    env = os.environ.get(ROOT_ENV)
    if env:
        root = Path(env)
        if not (root / CONFIG_FILE).is_file():
            raise ConfigError(f"{ROOT_ENV} points to a folder without {CONFIG_FILE}: {root}")
        return root
    candidates = []
    if start is not None:
        candidates.append(Path(start).resolve())
    candidates.append(Path.cwd().resolve())
    candidates.append(Path(__file__).resolve().parents[2])
    for base in candidates:
        for folder in (base, *base.parents):
            if (folder / CONFIG_FILE).is_file() and (folder / ".genesis").is_dir():
                return folder
    raise ConfigError(f"project root not found (no {CONFIG_FILE} next to .genesis)")


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"bad TOML in {path}: {exc}") from exc


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(app: str | None = None, root: Path | None = None) -> Config:
    """Load the shared config; `app` is the folder name under apps/ (e.g. "w1-tg-ingest")."""
    root = Path(root) if root is not None else find_root()
    data = _read_toml(root / CONFIG_FILE)
    local = root / LOCAL_CONFIG_FILE
    if local.is_file():
        data = _merge(data, _read_toml(local))
    if app:
        if "app" in data:
            raise ConfigError('"app" is reserved for apps/<app>/app.toml')
        app_file = root / "apps" / app / APP_CONFIG_FILE
        data["app"] = _read_toml(app_file) if app_file.is_file() else {}
    return Config(data, root=root, app=app)
