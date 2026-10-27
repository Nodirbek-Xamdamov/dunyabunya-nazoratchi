"""Service definitions (TOML): one [services.<name>] table per service, [defaults] for the rest.

    [services.gpu_gate]
    description = "..."
    command = ['{root}\\apps\\w0-core\\.venv\\Scripts\\python.exe', '-E', '-m', 'genesis_gpu', 'run']
    cwd = '{root}'
    env = { KEY = "value" }          # added to the runner's environment
    autostart = true                 # at boot + every minute if the runner is gone (Task Scheduler)
    restart_delay_s = 5              # first restart after a crash; doubles up to max_backoff_s
    max_backoff_s = 120
    reset_after_s = 300              # a run longer than this resets the backoff
    stop_timeout_s = 30              # graceful stop wait ("kutish vaqti"), then the process tree is killed
    log_max_mb = 20                  # stdout/stderr files rotate at this size
    log_keep = 5

Placeholders in command/cwd/env: {root} (project root), {logs} (paths.logs), {python} (W0 venv python).
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from genesis_common import load_config

DEFAULTS = {"autostart": True, "restart_delay_s": 5.0, "max_backoff_s": 120.0, "reset_after_s": 300.0,
            "stop_timeout_s": 30.0, "log_max_mb": 20, "log_keep": 5, "env": {}, "description": ""}
NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
DEFAULT_FILE = Path(__file__).with_name("services.toml")


class ServiceConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Service:
    name: str
    command: list[str]
    cwd: Path
    description: str = ""
    env: dict[str, str] = field(default_factory=dict)
    autostart: bool = True
    restart_delay_s: float = 5.0
    max_backoff_s: float = 120.0
    reset_after_s: float = 300.0
    stop_timeout_s: float = 30.0
    log_max_mb: int = 20
    log_keep: int = 5
    source: Path | None = None

    @property
    def task_name(self) -> str:
        return f"genesis_{self.name}"


@dataclass(frozen=True)
class Paths:
    root: Path
    logs: Path
    run: Path

    def log_dir(self, name: str) -> Path:
        return self.logs / f"svc-{name}"

    def stop_file(self, name: str) -> Path:
        return self.run / f"{name}.stop"

    def status_file(self, name: str) -> Path:
        return self.run / f"{name}.json"

    def lock_file(self, name: str) -> Path:
        return self.run / f"{name}.lock"


def paths() -> Paths:
    cfg = load_config()
    root = Path(cfg["project"]["root"])
    return Paths(root=root, logs=cfg.path("paths.logs"), run=root / "var" / "run" / "services")


def _expand(value: str, subs: dict[str, str]) -> str:
    for key, sub in subs.items():
        value = value.replace("{" + key + "}", sub)
    return value


def load(files: list[Path] | None = None) -> dict[str, Service]:
    """All services from the given TOML files (default: shared/genesis_service/services.toml)."""
    p = paths()
    subs = {"root": str(p.root), "logs": str(p.logs),
            "python": str(p.root / "apps" / "w0-core" / ".venv" / "Scripts" / "python.exe")}
    found: dict[str, Service] = {}
    for file in files or [DEFAULT_FILE]:
        data = tomllib.loads(Path(file).read_text(encoding="utf-8"))
        base = DEFAULTS | data.get("defaults", {})
        for name, raw in data.get("services", {}).items():
            if not NAME.match(name):
                raise ServiceConfigError(f"{file}: service name {name!r} must match {NAME.pattern}")
            if name in found:
                raise ServiceConfigError(f"{file}: service {name!r} is already defined in {found[name].source}")
            spec = base | raw
            unknown = set(spec) - set(DEFAULTS) - {"command", "cwd"}
            if unknown:
                raise ServiceConfigError(f"{file}: [services.{name}] unknown keys {sorted(unknown)}")
            if not spec.get("command") or not isinstance(spec["command"], list):
                raise ServiceConfigError(f"{file}: [services.{name}] command must be a non-empty list")
            found[name] = Service(
                name=name,
                command=[_expand(str(part), subs) for part in spec["command"]],
                cwd=Path(_expand(str(spec.get("cwd", "{root}")), subs)),
                description=str(spec["description"]),
                env={str(k): _expand(str(v), subs) for k, v in spec["env"].items()},
                autostart=bool(spec["autostart"]),
                restart_delay_s=float(spec["restart_delay_s"]),
                max_backoff_s=float(spec["max_backoff_s"]),
                reset_after_s=float(spec["reset_after_s"]),
                stop_timeout_s=float(spec["stop_timeout_s"]),
                log_max_mb=int(spec["log_max_mb"]),
                log_keep=int(spec["log_keep"]),
                source=Path(file),
            )
    return found
