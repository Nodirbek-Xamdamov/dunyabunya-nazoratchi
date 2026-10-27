"""W0-07 acceptance on the REAL W0 services (genesis_ollama, genesis_gpu_gate): kill the program, kill the runner,
stop/start, and check that they come back, keep logs, run in session 0 and that the gate reloads Gemma after an
Ollama restart. Short outages of the W0 services only (no other window is touched).

    apps\\w0-core\\.venv\\Scripts\\python.exe -E shared\\genesis_service\\tests\\run_w0_acceptance.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request

from genesis_gpu import gpu_memory
from genesis_queue import connect
from genesis_service import load, paths
from genesis_service.__main__ import pid_alive, read_status

MODEL = "gemma4:12b"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""), flush=True)


def wait(cond, timeout: float, step: float = 1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = cond()
        except Exception:
            value = None
        if value:
            return value
        time.sleep(step)
    return None


def cli(*args: str) -> str:
    r = subprocess.run([sys.executable, "-E", "-m", "genesis_service", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (r.stdout + r.stderr).strip()


def ollama_up() -> bool:
    with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=3) as r:
        return r.status == 200


def processes(name: str) -> list[tuple[int, int]]:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          f"Get-Process -Name '{name}' -ErrorAction SilentlyContinue | ForEach-Object {{ \"$($_.Id) $($_.SessionId)\" }}"],
                         capture_output=True, text=True).stdout.split()
    return [(int(out[i]), int(out[i + 1])) for i in range(0, len(out), 2)]


def main() -> int:
    p = paths()
    svcs = load()
    db = connect("w0_admin")

    def gate() -> dict:
        with db.cursor() as cur:
            cur.execute("SELECT row_to_json(v) FROM api.v_gpu_gate v")
            return cur.fetchone()[0]

    def gemma_back() -> bool:
        """Really resident (Ollama /api/ps + nvidia-smi), and the gate publishes it with 'llm' allowed."""
        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=3) as r:
            resident = MODEL in [m["name"] for m in json.loads(r.read())["models"]]
        g = gate()
        return (resident and gpu_memory().used_mib > 8000 and g["fresh"] and g["phase"] != "error"
                and MODEL in [m["name"] for m in g["loaded_models"]] and "llm" in g["allowed_classes"])

    def withdrawn() -> bool:
        g = gate()
        return g["phase"] == "error" and g["allowed_classes"] == ["none"]

    # A. both running, everything in session 0 (independent of the RDP session)
    st = {n: read_status(p, n) for n in svcs}
    llama = processes("llama-server")
    check("both W0 services run; runner, program and Ollama's llama-server are in session 0",
          all(s.get("state") == "running" and pid_alive(s.get("child_pid")) and s.get("session_id") == 0 for s in st.values())
          and llama and all(sess == 0 for _, sess in llama) and gemma_back(),
          f"{ {n: (s.get('runner_pid'), s.get('child_pid'), s.get('session_id')) for n, s in st.items()} }, llama-server {llama}")

    # B. the gate program dies -> restarted within seconds, publication fresh again
    old = st["gpu_gate"]["child_pid"]
    subprocess.run(["taskkill", "/PID", str(old), "/T", "/F"], capture_output=True)
    t0 = time.monotonic()
    new = wait(lambda: (s := read_status(p, "gpu_gate")).get("child_pid") not in (None, old) and pid_alive(s["child_pid"]) and s, 30)
    fresh = wait(lambda: gate()["fresh"] and gate()["updated_at"], 40)
    check("gate program killed -> runner restarts it, gate publishes again",
          new and fresh, f"restarted in {time.monotonic() - t0:.0f} s, child {old} -> {new and new['child_pid']}")

    # C. Ollama dies -> restarted within seconds; the gate reloads Gemma by itself
    old = read_status(p, "ollama")["child_pid"]
    before = gpu_memory().used_mib
    subprocess.run(["taskkill", "/PID", str(old), "/T", "/F"], capture_output=True)
    freed = wait(lambda: gpu_memory().used_mib < before - 5000 and gpu_memory().used_mib, 15)
    t0 = time.monotonic()
    up = wait(lambda: (s := read_status(p, "ollama")).get("child_pid") not in (None, old) and ollama_up() and s, 40)
    back = wait(gemma_back, 90, 2)
    check("Ollama killed -> VRAM freed, Ollama restarted by the runner, gate reloads Gemma (real /api/ps + nvidia-smi)",
          freed and up and back, f"nvidia-smi {before} -> {freed} MiB; Ollama back in {time.monotonic() - t0:.0f} s; "
          f"Gemma resident, VRAM {gpu_memory().used_mib} MiB")

    # D/E. a runner dies -> its program dies with it (job object); the task revives the runner within a minute
    for name in ("gpu_gate", "ollama"):
        s = read_status(p, name)
        runner, child = s["runner_pid"], s["child_pid"]
        subprocess.run(["taskkill", "/PID", str(runner), "/F"], capture_output=True)
        died = wait(lambda: not pid_alive(child) and "gone", 10, 0.5)
        t0 = time.monotonic()
        revived = wait(lambda: (n := read_status(p, name)).get("runner_pid") not in (None, runner)
                       and n.get("state") == "running" and pid_alive(n.get("child_pid")) and n, 90)
        ok_after = wait(gemma_back, 90, 2)
        check(f"{name}: runner killed -> program died with it, task revived the runner, Gemma resident again",
              died and revived and ok_after, f"runner {runner} -> {revived and revived['runner_pid']} after "
              f"{time.monotonic() - t0:.0f} s")

    # F/G. stop / start through the tool
    out_stop = cli("stop", "gpu_gate")
    graceful = "stop fayli: gate to'xtadi" in (p.log_dir("gpu_gate") / "stdout.log").read_text(encoding="utf-8")
    gst = read_status(p, "gpu_gate")
    out_start = cli("start", "gpu_gate")
    check("gpu_gate stop/start: graceful stop (exit 0 via stop file), starts again",
          graceful and gst.get("state") == "stopped" and gst.get("last_exit_code") == 0 and "ishlayapti" in out_start
          and wait(lambda: gate()["fresh"], 40), f"{out_stop} | {out_start}")
    out_stop = cli("stop", "ollama")
    down = not wait(ollama_up, 3)
    t0 = time.monotonic()
    safe = wait(withdrawn, 40, 0.5)  # next gate tick (<= 15 s) fails -> every GPU class withdrawn
    took = time.monotonic() - t0
    out_start = cli("start", "ollama")
    check("ollama stop/start: port closed, gate withdraws llm (only 'none') while Ollama is down, starts again, Gemma back",
          down and safe and "ishlayapti" in out_start and wait(ollama_up, 30) and wait(gemma_back, 90, 2),
          f"{out_stop}; 'none only' after {took:.0f} s; {out_start}")

    # H. logs
    sizes = {f"{n}/{f}": (p.log_dir(n) / f).stat().st_size for n in svcs
             for f in (f"svc-{n}.jsonl", "stdout.log", "stderr.log") if (p.log_dir(n) / f).exists()}
    events = [json.loads(line)["msg"] for line in
              (p.log_dir("gpu_gate") / "svc-gpu_gate.jsonl").read_text(encoding="utf-8").splitlines()]
    check("logs written: runner JSON events + program stdout/stderr for both services",
          all(sizes.get(k, 0) > 0 for k in ("ollama/svc-ollama.jsonl", "ollama/stderr.log", "gpu_gate/svc-gpu_gate.jsonl",
                                             "gpu_gate/stdout.log")) and "program exited, restarting" in events,
          f"{sizes}")
    print(cli("status"))
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
