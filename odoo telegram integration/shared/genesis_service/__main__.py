"""Genesis service tool (W0-07). See shared/SERVICES.md.

    python -m genesis_service list                 [--config FILE ...]
    python -m genesis_service status [NAME]
    python -m genesis_service install NAME          # register task genesis_<NAME> and start it
    python -m genesis_service uninstall NAME        # stop and remove the task (logs stay)
    python -m genesis_service start|stop|restart NAME
    python -m genesis_service logs NAME [--lines 40] [--stream stdout|stderr|runner]
    python -m genesis_service run NAME              # the runner itself (Task Scheduler calls this)
Default config: shared/genesis_service/services.toml (W0 services). Other windows pass their own file:
    --config apps\\w4-camera\\service.toml
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import deque
from pathlib import Path

from . import tasks
from .config import DEFAULT_FILE, Paths, Service, load, paths
from .runner import kill_tree, run


def read_status(p: Paths, name: str) -> dict:
    f = p.status_file(name)
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except ValueError:
        return {}


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    import ctypes
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(h)
    return code.value == 259  # STILL_ACTIVE


def do_stop(svc: Service, p: Paths) -> str:
    p.run.mkdir(parents=True, exist_ok=True)
    p.stop_file(svc.name).write_text("stop requested by genesis_service\n", encoding="utf-8")
    deadline = time.monotonic() + svc.stop_timeout_s + 20
    while time.monotonic() < deadline:
        st = read_status(p, svc.name)
        if not pid_alive(st.get("runner_pid")):
            return f"{svc.name}: to'xtadi"
        time.sleep(0.5)
    st = read_status(p, svc.name)  # the runner did not finish in time: end the task and the tree
    try:
        tasks.end(svc)
    except tasks.TaskError:
        pass
    if pid_alive(st.get("runner_pid")):
        kill_tree(st["runner_pid"])
    return f"{svc.name}: majburan to'xtatildi"


def do_start(svc: Service, p: Paths) -> str:
    p.stop_file(svc.name).unlink(missing_ok=True)
    if tasks.info(svc) is None:
        return f"{svc.name}: vazifa ro'yxatdan o'tmagan (avval install)"
    tasks.start(svc)
    for _ in range(40):
        st = read_status(p, svc.name)
        if st.get("state") == "running" and pid_alive(st.get("child_pid")):
            return f"{svc.name}: ishlayapti (runner {st['runner_pid']}, dastur {st['child_pid']})"
        time.sleep(0.5)
    return f"{svc.name}: ishga tushirildi, holat: {read_status(p, svc.name).get('state')}"


def status_line(svc: Service, p: Paths) -> str:
    st = read_status(p, svc.name)
    task = tasks.info(svc)
    alive = pid_alive(st.get("runner_pid"))
    child = pid_alive(st.get("child_pid"))
    stopped = p.stop_file(svc.name).exists()
    state = "to'xtatilgan (stop fayli)" if stopped else (st.get("state", "?") if alive else "runner yo'q")
    return json.dumps({"service": svc.name, "task": task["state"] if task else "ro'yxatda yo'q", "state": state,
                       "runner_pid": st.get("runner_pid") if alive else None,
                       "child_pid": st.get("child_pid") if child else None, "session_id": st.get("session_id"),
                       "restarts": st.get("restarts"), "last_exit_code": st.get("last_exit_code"),
                       "last_exit_at": st.get("last_exit_at"), "child_started_at": st.get("child_started_at"),
                       "autostart": svc.autostart}, ensure_ascii=False)


def tail(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return [f"(yo'q: {path})"]
    with path.open(encoding="utf-8", errors="replace") as fh:
        return list(deque(fh, maxlen=lines))


def main() -> int:
    ap = argparse.ArgumentParser(prog="genesis_service")
    ap.add_argument("command", choices=["list", "status", "install", "uninstall", "start", "stop", "restart", "logs", "run"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--config", action="append", type=Path)
    ap.add_argument("--lines", type=int, default=40)
    ap.add_argument("--stream", choices=["stdout", "stderr", "runner"], default="stderr")
    a = ap.parse_args()
    files = [f.resolve() for f in a.config] if a.config else [DEFAULT_FILE]
    services = load(files)
    p = paths()
    p.run.mkdir(parents=True, exist_ok=True)
    if a.command == "list":
        for s in services.values():
            print(f"{s.name:<14} {s.task_name:<24} autostart={s.autostart} {s.description}")
        return 0
    targets = [services[a.name]] if a.name else (list(services.values()) if a.command == "status" else [])
    if a.name and a.name not in services:
        ap.error(f"noma'lum servis {a.name!r}; bor: {', '.join(services)}")
    if not targets:
        ap.error(f"{a.command} uchun servis nomi kerak")
    svc = targets[0]
    if a.command == "run":
        return run(svc, p)
    if a.command == "status":
        for s in targets:
            print(status_line(s, p))
        return 0
    if a.command == "install":
        config_file = svc.source or files[0]
        tasks.register(svc, p, config_file)
        print(f"{svc.task_name}: ro'yxatdan o'tdi (S4U, autostart={svc.autostart}, config {config_file})")
        print(do_start(svc, p))
        return 0
    if a.command == "uninstall":
        if tasks.info(svc) is not None:
            print(do_stop(svc, p))
            tasks.unregister(svc)
        p.stop_file(svc.name).unlink(missing_ok=True)
        print(f"{svc.task_name}: o'chirildi (loglar qoldi: {p.log_dir(svc.name)})")
        return 0
    if a.command == "stop":
        print(do_stop(svc, p))
        return 0
    if a.command == "start":
        print(do_start(svc, p))
        return 0
    if a.command == "restart":
        print(do_stop(svc, p))
        print(do_start(svc, p))
        return 0
    if a.command == "logs":
        folder = p.log_dir(svc.name)
        path = folder / (f"svc-{svc.name}.jsonl" if a.stream == "runner" else f"{a.stream}.log")
        sys.stdout.writelines(tail(path, a.lines))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
