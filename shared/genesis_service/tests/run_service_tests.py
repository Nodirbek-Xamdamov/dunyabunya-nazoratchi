"""W0-07 tests for genesis_service on two throwaway services (test_services.toml): real Task Scheduler tasks
(S4U), real processes. Registers genesis_svctest_long / genesis_svctest_crash, breaks them, removes them.

    apps\\w0-core\\.venv\\Scripts\\python.exe -E shared\\genesis_service\\tests\\run_service_tests.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from genesis_service import load, paths
from genesis_service.__main__ import pid_alive, read_status
from genesis_service import tasks

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "test_services.toml"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""), flush=True)


def cli(*args: str) -> str:
    r = subprocess.run([sys.executable, "-E", "-m", "genesis_service", *args, "--config", str(CONFIG)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout + r.stderr).strip()


def wait(cond, timeout: float, step: float = 0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = cond()
        if value:
            return value
        time.sleep(step)
    return None


def session_of(pid: int) -> int | None:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).SessionId"],
                         capture_output=True, text=True).stdout.strip()
    return int(out) if out.isdigit() else None


def main() -> int:
    services = load([CONFIG])
    p = paths()
    long_svc, crash_svc = services["svctest_long"], services["svctest_crash"]
    for s in services.values():  # leftovers from an aborted run
        if tasks.info(s) is not None:
            cli("uninstall", s.name)
    pid_file = p.run / "svctest_long.pids"
    pid_file.unlink(missing_ok=True)

    # 1. install: S4U task, runner and program in session 0, logs written
    print(cli("install", "svctest_long"))
    st = wait(lambda: (s := read_status(p, "svctest_long")).get("state") == "running" and pid_alive(s.get("child_pid")) and s, 30)
    info = tasks.info(long_svc)
    child, grandchild = (int(x) for x in wait(lambda: pid_file.exists() and pid_file.read_text().split(), 15))
    time.sleep(3)
    out_log = (p.log_dir("svctest_long") / "stdout.log").read_text(encoding="utf-8")
    runner_log = (p.log_dir("svctest_long") / "svc-svctest_long.jsonl").read_text(encoding="utf-8")
    check("install: task S4U (autostart: boot + every minute), runner and program run in session 0 (not the RDP session)",
          st and info["logon"] == "S4U" and set(info["triggers"]) == {"MSFT_TaskBootTrigger", "MSFT_TaskTimeTrigger"}
          and st["session_id"] == 0 and session_of(st["child_pid"]) == 0 and session_of(grandchild) == 0,
          f"task={info}; runner {st and st['runner_pid']} session {st and st['session_id']}, child {child}, grandchild {grandchild}")
    check("logs: program stdout captured with secrets redacted, runner JSON log written",
          "tick 2" in out_log and "hunter2" not in out_log and "password=***" in out_log and "runner started" in runner_log,
          f"stdout lines={out_log.count(chr(10))}")

    # 2. the program dies -> the runner restarts it after restart_delay_s
    subprocess.run(["taskkill", "/PID", str(child), "/T", "/F"], capture_output=True)
    t0 = time.monotonic()
    st2 = wait(lambda: (s := read_status(p, "svctest_long")).get("restarts", 0) >= 1 and s.get("state") == "running"
               and pid_alive(s.get("child_pid")) and s["child_pid"] != st["child_pid"] and s, 30)
    check("program killed -> runner restarts it (restart_delay_s 2)", st2 and st2["runner_pid"] == st["runner_pid"],
          f"new child {st2 and st2['child_pid']} after {time.monotonic() - t0:.1f} s, last_exit_code {st2 and st2['last_exit_code']}")

    # 3. the runner dies -> its job object kills the program tree; the minute trigger starts a new runner
    time.sleep(1)
    child, grandchild = (int(x) for x in pid_file.read_text().split())
    runner = st2["runner_pid"]
    subprocess.run(["taskkill", "/PID", str(runner), "/F"], capture_output=True)  # the runner only, not its tree
    orphans = wait(lambda: not pid_alive(child) and not pid_alive(grandchild) and "gone", 10)
    t0 = time.monotonic()
    st3 = wait(lambda: (s := read_status(p, "svctest_long")).get("runner_pid") not in (None, runner)
               and s.get("state") == "running" and pid_alive(s.get("child_pid")) and s, 90, 1)
    check("runner killed -> program and grandchild die with it (job object), task revives the runner within a minute",
          orphans and st3, f"old runner {runner}; new runner {st3 and st3['runner_pid']} after {time.monotonic() - t0:.0f} s")

    # 4. stop: graceful (the program sees GENESIS_STOP_FILE), stays stopped even when the minute trigger fires
    child, grandchild = (int(x) for x in pid_file.read_text().split())
    out = cli("stop", "svctest_long")
    stopped = read_status(p, "svctest_long")
    gone = not pid_alive(child) and not pid_alive(grandchild) and not pid_alive(stopped.get("runner_pid"))
    graceful = "stop file seen" in (p.log_dir("svctest_long") / "stdout.log").read_text(encoding="utf-8")
    time.sleep(65)
    later = read_status(p, "svctest_long")
    check("stop: graceful exit (code 0), nothing left running, still stopped after the minute trigger",
          gone and graceful and stopped.get("last_exit_code") == 0 and later.get("state") == "stopped"
          and not pid_alive(later.get("child_pid")), f"{out}; status {later.get('state')}")
    out = cli("start", "svctest_long")
    check("start: running again", "ishlayapti" in out, out)

    # 5. a program that keeps crashing: restarts with growing delay (1, 2, 4, 4 ... s)
    print(cli("install", "svctest_crash"))
    time.sleep(22)
    stc = read_status(p, "svctest_crash")
    delays = [json.loads(line).get("restart_in_s") for line in
              (p.log_dir("svctest_crash") / "svc-svctest_crash.jsonl").read_text(encoding="utf-8").splitlines()
              if '"program exited' in line]
    err = (p.log_dir("svctest_crash") / "stderr.log").read_text(encoding="utf-8")
    check("crashing program: restarted again and again with backoff 1 -> 2 -> 4 (max_backoff_s), exit code 3 recorded",
          stc.get("restarts", 0) >= 3 and delays[:3] == [1.0, 2.0, 4.0] and stc.get("last_exit_code") == 3
          and "simulated crash" in err, f"restarts {stc.get('restarts')}, delays {delays}")

    # 6. uninstall: tasks gone, no process left
    status_before = {s: read_status(p, s) for s in services}
    for name in services:
        print(cli("uninstall", name))
    leftovers = [s for s, stt in status_before.items() if pid_alive(stt.get("runner_pid")) or pid_alive(stt.get("child_pid"))]
    check("uninstall: tasks removed, no runner or program left",
          all(tasks.info(s) is None for s in services.values()) and not leftovers, f"leftovers={leftovers}")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
