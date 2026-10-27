"""The supervisor ("runner") of one service. Task Scheduler starts it (at boot and every minute; a second copy
exits at once because of the lock), it starts the program and keeps it alive:

- crash/exit -> restart after restart_delay_s, doubling up to max_backoff_s (reset after a long run);
- stdout/stderr -> var/logs/svc-<name>/stdout.log, stderr.log (size rotation, secrets redacted);
- runner events -> var/logs/svc-<name>/svc-<name>.jsonl (genesis_common JSON log);
- status -> var/run/services/<name>.json (pids, state, restarts, last exit) every few seconds;
- stop file var/run/services/<name>.stop -> graceful stop: the program sees GENESIS_STOP_FILE (genesis-aware
  programs exit by themselves), after stop_timeout_s the whole process tree is killed; the runner exits and
  stays stopped (also after a reboot) until `genesis_service start` removes the file.
The runner puts itself in a Windows job object with KILL_ON_JOB_CLOSE, so if the runner dies (killed, task
ended) its children die with it and the next runner never finds an orphan holding the port.
"""
from __future__ import annotations

import ctypes
import json
import logging
import logging.handlers
import msvcrt
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

from genesis_common import setup_logging
from genesis_common.logs import redact

from .config import Paths, Service

TICK_S = 1.0
STATUS_EVERY_S = 5.0


# -- Windows job object: children die with the runner ----------------------------------------------------
class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("r_ops", "w_ops", "o_ops", "r_bytes", "w_bytes", "o_bytes")]


class _BasicLimit(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimit), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def kill_children_with_me() -> bool:
    """Put this process into a new job that kills every member when its last handle (ours) closes."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return False
    info = _ExtendedLimit()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
        return False
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    return bool(k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(k32.GetCurrentProcess())))
    # the handle is intentionally never closed: it closes when this process ends


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


# -- output capture ------------------------------------------------------------------------------------
def _pump(stream, path: Path, max_mb: int, keep: int) -> None:
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=max_mb * 2**20, backupCount=keep, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    try:
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line:
                handler.emit(logging.makeLogRecord({"msg": redact(line), "levelno": logging.INFO}))
    finally:
        handler.close()


class Lock:
    """One runner per service: an exclusive byte lock on var/run/services/<name>.lock."""

    def __init__(self, path: Path):
        self.path = path
        self.fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+b")
        try:
            msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            self.fh.close()
            self.fh = None
            return False


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Runner:
    def __init__(self, svc: Service, p: Paths):
        self.svc, self.p = svc, p
        self.log = setup_logging(f"svc-{svc.name}")
        self.status = {"service": svc.name, "runner_pid": os.getpid(), "state": "starting", "child_pid": None,
                       "started_at": _now(), "child_started_at": None, "restarts": 0, "last_exit_code": None,
                       "last_exit_at": None, "next_start_at": None, "session_id": None, "updated_at": None}
        self._last_status = 0.0

    # status file, written atomically
    def save(self, **changes) -> None:
        self.status.update(changes, updated_at=_now())
        path = self.p.status_file(self.svc.name)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.status, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        self._last_status = time.monotonic()

    def stopping(self) -> bool:
        return self.p.stop_file(self.svc.name).exists()

    def start_child(self) -> subprocess.Popen:
        logs = self.p.log_dir(self.svc.name)
        logs.mkdir(parents=True, exist_ok=True)
        env = os.environ | self.svc.env | {"GENESIS_SERVICE": self.svc.name,
                                           "GENESIS_STOP_FILE": str(self.p.stop_file(self.svc.name))}
        proc = subprocess.Popen(self.svc.command, cwd=self.svc.cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        for stream, name in ((proc.stdout, "stdout.log"), (proc.stderr, "stderr.log")):
            threading.Thread(target=_pump, args=(stream, logs / name, self.svc.log_max_mb, self.svc.log_keep),
                             daemon=True).start()
        return proc

    def stop_child(self, proc: subprocess.Popen) -> int | None:
        deadline = time.monotonic() + self.svc.stop_timeout_s
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        if proc.poll() is None:
            self.log.warning("graceful stop timed out, killing the process tree",
                             extra={"pid": proc.pid, "stop_timeout_s": self.svc.stop_timeout_s})
            kill_tree(proc.pid)
            proc.wait(10)
        return proc.returncode

    def run(self) -> int:
        lock = Lock(self.p.lock_file(self.svc.name))
        if not lock.acquire():
            return 0  # another runner owns this service (Task Scheduler fires every minute)
        if self.stopping():
            self.save(state="stopped", child_pid=None, note="stop fayli bor: `genesis_service start` kerak")
            return 0
        job = kill_children_with_me()
        session = ctypes.c_ulong()
        ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
        self.log.info("runner started", extra={"pid": os.getpid(), "session_id": session.value, "job_object": job,
                                               "command": redact(" ".join(self.svc.command))})
        self.save(state="starting", session_id=session.value, job_object=job)
        delay = self.svc.restart_delay_s
        while not self.stopping():
            try:
                proc = self.start_child()
            except OSError as exc:
                self.log.error("cannot start the program", extra={"error": repr(exc)})
                proc = None
            started = time.monotonic()
            if proc is not None:
                self.log.info("program started", extra={"child_pid": proc.pid})
                self.save(state="running", child_pid=proc.pid, child_started_at=_now(), next_start_at=None)
                while proc.poll() is None:
                    if self.stopping():
                        code = self.stop_child(proc)
                        self.log.info("program stopped by stop file", extra={"exit_code": code})
                        self.save(state="stopped", child_pid=None, last_exit_code=code, last_exit_at=_now())
                        return 0
                    if time.monotonic() - self._last_status >= STATUS_EVERY_S:
                        self.save()
                    time.sleep(TICK_S)
                code = proc.returncode
                if self.stopping():  # a genesis-aware program saw the stop file and exited by itself
                    self.log.info("program exited after stop file", extra={"exit_code": code})
                    self.save(state="stopped", child_pid=None, last_exit_code=code, last_exit_at=_now())
                    return 0
            else:
                code = None
            ran = time.monotonic() - started
            if ran >= self.svc.reset_after_s:
                delay = self.svc.restart_delay_s
            self.log.warning("program exited, restarting", extra={"exit_code": code, "ran_s": round(ran, 1),
                                                                   "restart_in_s": delay})
            self.save(state="restarting", child_pid=None, last_exit_code=code, last_exit_at=_now(),
                      restarts=self.status["restarts"] + 1,
                      next_start_at=datetime.fromtimestamp(time.time() + delay).astimezone().isoformat(timespec="seconds"))
            waited = 0.0
            while waited < delay and not self.stopping():
                time.sleep(TICK_S)
                waited += TICK_S
            delay = min(delay * 2, self.svc.max_backoff_s)
        self.save(state="stopped", child_pid=None)
        self.log.info("runner stopped (stop file)")
        return 0


def run(svc: Service, p: Paths) -> int:
    return Runner(svc, p).run()


if __name__ == "__main__":  # pragma: no cover - use `python -m genesis_service run NAME`
    sys.exit("use: python -m genesis_service run NAME")
