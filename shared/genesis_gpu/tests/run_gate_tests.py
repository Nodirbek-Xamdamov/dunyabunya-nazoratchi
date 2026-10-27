"""W0-06 acceptance tests for the GPU gate (D-30). Real Ollama, real nvidia-smi, real jobs; simulated (accelerated) clock.
DESTRUCTIVE for core.jobs / gate state: only on a test database.

    apps\\w0-core\\.venv\\Scripts\\python.exe -E shared\\genesis_gpu\\tests\\run_gate_tests.py [--db genesis_test]
Afterwards: python -E shared/fixtures/load_fixtures.py
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from genesis_gpu import TASHKENT, GpuGate, Ollama, decide, device_for, gpu_memory
from genesis_queue import JobQueue, connect

ROOT = Path(__file__).resolve().parents[3]
CAM_PY = ROOT / "camera_service" / ".venv" / "Scripts" / "python.exe"
YOLO_W = ROOT / "camera_service" / "models" / "yolo26m.pt"
IMAGE = ROOT / "apps" / "w0-core" / "smoke" / "test_image.png"
YOLO_LOOP = ROOT / "apps" / "w0-core" / "smoke" / "yolo_loop.py"
MODEL = "gemma4:12b"
LLM_W, VIS_W = "10:00-22:00", "22:00-10:00"  # D-30 starting windows
results: list[tuple[str, bool, str]] = []


def check(name: str, ok, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""), flush=True)


class SimClock:
    def __init__(self):
        self.now = datetime(2026, 10, 26, 21, 30, tzinfo=TASHKENT)

    def set(self, day: int, hh: int, mm: int) -> None:
        self.now = datetime(2026, 10, day, hh, mm, tzinfo=TASHKENT)

    def __call__(self) -> datetime:
        return self.now


def cli(db: str, *args: str) -> str:
    r = subprocess.run([sys.executable, "-E", "-m", "genesis_gpu", *args, "--db", db], capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()


def main(db: str) -> int:
    if db == "genesis":
        raise SystemExit("refusing to run destructive gate tests on the production database 'genesis'")
    ollama = Ollama()
    loaded_before = ollama.loaded()
    admin = connect("w0_admin", db)

    def sql(text, params=None):
        with admin.cursor() as cur:
            cur.execute(text, params)
            return cur.fetchall() if cur.description else cur.rowcount

    def last_event(event: str) -> dict:
        return sql("SELECT details FROM core.gpu_gate_log WHERE event = %s AND id > %s ORDER BY id DESC LIMIT 1",
                   (event, log_start))[0][0]

    def count_events(event: str) -> int:
        return sql("SELECT count(*) FROM core.gpu_gate_log WHERE event = %s AND id > %s", (event, log_start))[0][0]

    sql("DELETE FROM core.jobs")
    log_start = sql("SELECT coalesce(max(id), 0) FROM core.gpu_gate_log")[0][0]
    cfg = dict(sql("SELECT key, value FROM core.config WHERE key IN ('gpu.mode', 'gpu.llm_window', 'gpu.vision_window', "
                   "'gpu.vision_idle_to_llm', 'gpu.devices')"))
    check("settings after V009 (D-30): timeslice, Telegram 10:00-22:00, camera 22:00-10:00, idle camera window -> Gemma",
          cfg == {"gpu.mode": "timeslice", "gpu.llm_window": LLM_W, "gpu.vision_window": VIS_W,
                  "gpu.vision_idle_to_llm": True, "gpu.devices": {"llm": 0, "vision": 0}}, f"{cfg}")

    # 0. pure schedule (any clock)
    def at(h, m, **kw):
        d = decide(datetime(2026, 10, 27, h, m, tzinfo=TASHKENT), "timeslice", LLM_W, VIS_W, 15, **kw)
        return d.window, d.guard, set(d.target), d.want_llm

    cases = [  # (h, m, kwargs) -> (window, guard, target, want_llm)
        ((21, 44, {}), ("llm", False, {"none", "llm"}, True)),
        ((21, 45, {}), ("llm", True, {"none"}, True)),
        ((22, 0, {}), ("vision", False, {"none", "vision"}, False)),
        ((9, 44, {}), ("vision", False, {"none", "vision"}, False)),
        ((9, 45, {}), ("vision", True, {"none"}, False)),
        ((10, 0, {}), ("llm", False, {"none", "llm"}, True)),
        ((21, 50, {"idle_to_llm": True, "vision_wanted": False}), ("llm", False, {"none", "llm"}, True)),
        ((21, 50, {"idle_to_llm": True, "vision_wanted": True}), ("llm", True, {"none"}, True)),
        ((23, 0, {"idle_to_llm": True, "vision_wanted": False}), ("llm_idle", False, {"none", "llm"}, True)),
        ((23, 0, {"idle_to_llm": True, "vision_wanted": True}), ("vision", False, {"none", "vision"}, False)),
        ((9, 50, {"idle_to_llm": True, "vision_wanted": True, "vision_active": False}), ("llm_idle", False, {"none", "llm"}, True)),
        ((9, 50, {"idle_to_llm": True, "vision_wanted": True, "vision_active": True}), ("vision", True, {"none"}, False)),
    ]
    bad = [(hm, at(hm[0], hm[1], **hm[2]), want) for hm, want in cases if at(hm[0], hm[1], **hm[2]) != want]
    others = {m: set(decide(datetime(2026, 10, 27, 3, 0, tzinfo=TASHKENT), m, LLM_W, VIS_W, 15).target)
              for m in ("coresident", "dedicated")}
    check("schedule: 21:45 guard, 22:00 camera, 09:45 guard, 10:00 Telegram; idle camera window -> Gemma, a camera job "
          "takes it back (not in the last 15 min); coresident/dedicated = all",
          not bad and all(t == {"none", "llm", "vision"} for t in others.values()), f"bad={bad}")

    clock = SimClock()
    gate = GpuGate(admin, ollama, clock=clock, simulated=True)
    print(cli(db, "mode", "timeslice", "--reason", "W0-06 sinov (D-30): tezlatilgan soat"))
    w2 = JobQueue(connect("w2_analyst", db), worker="w2-worker")
    w4 = JobQueue(connect("w4_camera", db), worker="w4-worker")
    adm_q = JobQueue(admin, worker="scheduler")
    base = gpu_memory().used_mib

    # A. 22:00 switch Telegram -> camera
    clock.set(26, 21, 30)
    s = gate.tick()
    vram_llm = gpu_memory().used_mib
    check("21:30 timeslice: Gemma loaded by the gate, llm allowed", MODEL in s["loaded"] and s["allowed"] == ["llm", "none"],
          f"vram {base} -> {vram_llm} MiB; allowed={s['allowed']} loaded={s['loaded']} phase={s['phase']} note={s['note']}")
    L = [w2.enqueue("analyze_episode", {"n": i}, key=f"gate:{i}").id for i in (1, 2, 3)]
    V1 = adm_q.enqueue("camera_day_batch", {"day": "2026-10-26"}, key="gate:v1").id
    clock.set(26, 21, 40)
    gate.tick()
    l1 = w2.claim(["analyze_episode"])
    gen = ollama.generate(MODEL, "Bir so'z bilan javob ber: sement nima?", num_predict=16)
    check("21:40 worker takes L1 and Gemma answers", [j.id for j in l1] == [L[0]] and gen.get("eval_count", 0) > 0,
          f"tokens={gen.get('eval_count')}")
    clock.set(26, 21, 44)
    gate.tick()
    dying = JobQueue(connect("w2_analyst", db), worker="w2-dies")
    l2 = dying.claim(["analyze_episode"], lease_s=4)  # this worker 'dies': never heart-beats or completes
    clock.set(26, 21, 46)
    s = gate.tick()
    l3 = w2.claim(["analyze_episode"])
    v_early = w4.claim(["camera_day_batch"])
    check("21:46 guard (a camera job is waiting): no new llm job (L3 waits), no vision yet",
          s["allowed"] == ["none"] and l3 == [] and v_early == [] and [j.id for j in l2] == [L[1]], f"phase={s['phase']}")
    clock.set(26, 22, 0)
    w2.heartbeat(L[0])
    s = gate.tick()
    check("22:00 L1 still running: gate drains, Gemma stays, nothing allowed", s["phase"] == "draining" and MODEL in s["loaded"]
          and s["allowed"] == ["none"], s["note"])
    w2.complete(L[0], {"ok": True})
    time.sleep(4.5)  # the dying worker's lease (4 s) runs out: the gate waits for it too, then switches
    clock.set(26, 22, 1)
    s = gate.tick()
    after = gpu_memory().used_mib
    ev = last_event("model_unloaded")
    check("22:01 L1 done -> Gemma unloaded, VRAM freed (nvidia-smi), vision allowed",
          MODEL not in s["loaded"] and s["allowed"] == ["none", "vision"] and ev["freed_mib"] > 5000 and after < vram_llm - 5000,
          f"nvidia-smi {ev['vram_before_mib']} -> {ev['vram_after_mib']} MiB (freed {ev['freed_mib']}), now {after}")
    v1 = w4.claim(["camera_day_batch"])
    l_night = w2.claim(["analyze_episode"])
    l2_row = sql("SELECT status, attempts, last_error FROM core.jobs WHERE id = %s", (L[1],))[0]
    check("camera window: V1 runs; the dead worker's L2 is returned but no llm job is handed out",
          [j.id for j in v1] == [V1] and l_night == [] and l2_row[0] == "failed" and "lease expired" in l2_row[2],
          f"L2={l2_row}")

    # A2. D-30: camera window with no camera job -> GPU back to Gemma
    w4.complete(V1, {"ok": True})
    clock.set(26, 22, 5)
    s = gate.tick()
    check("22:05 V1 done, 4 min idle (< gpu.vision_idle_minutes 5): GPU stays with the camera",
          s["window"] == "vision" and MODEL not in s["loaded"] and s["allowed"] == ["none", "vision"], f"window={s['window']}")
    clock.set(26, 22, 7)
    s = gate.tick()
    ev = last_event("model_loaded")
    lent = count_events("camera_window_lent_to_llm")
    check("22:07 no camera job -> GPU given back to Gemma (loaded, llm allowed, journaled)",
          s["window"] == "llm_idle" and MODEL in s["loaded"] and s["allowed"] == ["llm", "none"] and lent == 1,
          f"nvidia-smi {ev['vram_before_mib']} -> {ev['vram_after_mib']} MiB in {ev['seconds']} s; note={s['note']}")
    got = w2.claim(["analyze_episode"], limit=5)
    for j in got:
        w2.complete(j.id)
    check("22:07 Telegram work continues in the idle camera window (L3 done)", L[2] in [j.id for j in got],
          f"claimed {[j.id for j in got]}")

    # A3. a camera job arrives -> the camera takes the GPU again
    L4 = w2.enqueue("analyze_episode", key="gate:4").id
    V2 = adm_q.enqueue("camera_day_batch", {"day": "x2"}, key="gate:v2").id
    clock.set(26, 23, 0)
    s = gate.tick()
    ev = last_event("model_unloaded")
    taken = count_events("camera_window_taken")
    v2 = w4.claim(["camera_day_batch"])
    l4_now = w2.claim(["analyze_episode"])
    check("23:00 camera job arrives -> Gemma unloaded (VRAM freed), vision allowed, V2 runs, L4 waits",
          s["window"] == "vision" and MODEL not in s["loaded"] and s["allowed"] == ["none", "vision"] and taken == 1
          and [j.id for j in v2] == [V2] and l4_now == [] and ev["freed_mib"] > 5000,
          f"nvidia-smi {ev['vram_before_mib']} -> {ev['vram_after_mib']} MiB (freed {ev['freed_mib']})")

    # B. 10:00 switch camera -> Telegram (camera busy until the end of its window)
    V3 = adm_q.enqueue("camera_day_batch", {"day": "x3"}, key="gate:v3").id
    V4 = adm_q.enqueue("camera_day_batch", {"day": "x4"}, key="gate:v4").id
    clock.set(27, 9, 30)
    w4.complete(V2)
    s = gate.tick()
    v3 = w4.claim(["camera_day_batch"])
    clock.set(27, 9, 46)
    s = gate.tick()
    v4 = w4.claim(["camera_day_batch"])
    check("09:46 guard: running V3 continues, V4 not handed out", [j.id for j in v3] == [V3] and v4 == [] and s["allowed"] == ["none"],
          f"window={s['window']} phase={s['phase']}")
    clock.set(27, 10, 0)
    w4.heartbeat(V3)
    s = gate.tick()
    check("10:00 V3 still running: Gemma not loaded yet (VRAM kept for the camera)", s["phase"] == "draining"
          and MODEL not in s["loaded"], s["note"])
    w4.complete(V3)
    clock.set(27, 10, 1)
    s = gate.tick()
    ev = last_event("model_loaded")
    check("10:01 V3 done -> Gemma loaded, llm allowed", MODEL in s["loaded"] and s["allowed"] == ["llm", "none"],
          f"nvidia-smi {ev['vram_before_mib']} -> {ev['vram_after_mib']} MiB in {ev['seconds']} s")
    for _ in range(40):  # L2's retry backoff (30 s, real time) must pass before it can be claimed
        if not sql("SELECT 1 FROM core.jobs WHERE id = %s AND status = 'failed' AND run_after > now()", (L[1],)):
            break
        time.sleep(1)
    got = w2.claim(["analyze_episode"], limit=5)
    for j in got:
        w2.complete(j.id)
    states = dict(sql("SELECT id, status || '/' || attempts FROM core.jobs"))
    lost = sql("SELECT count(*) FROM core.jobs WHERE status IN ('dead')")[0][0]
    want = {L[0]: "done/1", L[1]: "done/2", L[2]: "done/1", L4: "done/1", V1: "done/1", V2: "done/1", V3: "done/1",
            V4: "queued/0"}
    check("no job lost over all switches: L2 (dead worker) done on 2nd attempt, V4 waits for the next camera window",
          states == want and lost == 0, f"{ {k: v for k, v in sorted(states.items())} }")

    # C. coresident (fallback) without restart: both models work, VRAM stays under the limit
    print(cli(db, "mode", "coresident", "--reason", "W0-06 sinov: zaxira rejim (D-14)"))
    clock.set(27, 10, 30)
    s = gate.tick()
    limit = int(sql("SELECT value FROM core.config WHERE key = 'gpu.vram_limit_mib'")[0][0])
    v4 = w4.claim(["camera_day_batch"])
    peak = [0]
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            peak[0] = max(peak[0], gpu_memory().used_mib)
            time.sleep(0.3)

    th = threading.Thread(target=sample, daemon=True)
    th.start()
    yolo = subprocess.Popen([str(CAM_PY), "-I", str(YOLO_LOOP), str(YOLO_W), str(IMAGE), "20"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    time.sleep(6)
    gen = ollama.generate(MODEL, "Qurilish mollari do'koni uchun 5 ta maslahat yoz.", num_predict=300)
    mid = gate.tick()
    out, err = yolo.communicate(timeout=180)
    stop.set()
    th.join()
    w4.complete(v4[0].id) if v4 else None
    yolo_res = out.strip().splitlines()[-1] if out.strip() else err[-300:]
    check("coresident (mode changed with no restart): all classes allowed, vision job claimed in daytime",
          s["mode"] == "coresident" and s["allowed"] == ["llm", "none", "vision"] and [j.id for j in v4] == [V4])
    check("coresident: YOLO on GPU and Gemma together, VRAM peak under the limit",
          '"device": "cuda:0"' in yolo_res and gen.get("eval_count", 0) > 100 and 0 < peak[0] <= limit and mid["phase"] != "over_vram_limit",
          f"YOLO {yolo_res}; Gemma {gen.get('eval_count')} tokens {round(gen.get('eval_count', 0) / max(gen.get('eval_duration', 1) / 1e9, 1e-9), 1)} tok/s; "
          f"peak {peak[0]} MiB <= limit {limit}")

    # D. manual override + journal
    L5 = w2.enqueue("analyze_episode", key="gate:5").id
    print(cli(db, "override", "--allow", "none", "--minutes", "5", "--reason", "W0-06 sinov: qo'lda to'xtatish"))
    s = gate.tick()
    blocked = w2.claim(["analyze_episode"])
    oid = sql("SELECT max(id) FROM core.gpu_overrides")[0][0]
    print(cli(db, "cancel", str(oid)))
    s2 = gate.tick()
    freed = w2.claim(["analyze_episode"])
    w2.complete(L5) if freed else None
    journal = [r[0] for r in sql("SELECT event FROM core.gpu_gate_log WHERE event LIKE 'override%' OR event = 'mode_set' ORDER BY id")]
    check("override: --allow none blocks llm; cancel restores; both journaled",
          s["phase"] == "override" and blocked == [] and [j.id for j in freed] == [L5] and "override_set" in journal
          and "override_cancelled" in journal, f"journal={journal}")

    # E. safety: VRAM over limit stops new GPU work; stale gate = only 'none'
    sql("UPDATE core.config SET value = '5000' WHERE key = 'gpu.vram_limit_mib'")
    s = gate.tick()
    sql("UPDATE core.config SET value = %s WHERE key = 'gpu.vram_limit_mib'", (str(limit),))
    check("VRAM above gpu.vram_limit_mib -> only 'none' allowed and journaled", s["phase"] == "over_vram_limit" and s["allowed"] == ["none"],
          s["note"])
    gate.tick()
    sql("UPDATE core.gpu_gate_state SET valid_until = now() - interval '1 second'")
    with connect("w2_analyst", db).cursor() as cur:
        cur.execute("SELECT core.gpu_allowed_classes()")
        stale = cur.fetchone()[0]
    check("gate silent (publication expired) -> workers get only 'none'", stale == ["none"], f"{stale}")
    procs = last_event("gpu_processes")
    names = sorted({p["name"] for p in procs["processes"]})
    check("other GPU processes detected and journaled, not touched", len(names) > 0, f"{names}")

    # F. dedicated (D-30): settings and logic only; one GPU here, so it falls back to timeslice
    print(cli(db, "mode", "dedicated", "--reason", "W0-06 sinov: ikkinchi GPU yo'q"))
    clock.set(27, 23, 0)
    s = gate.tick()
    e1 = last_event("dedicated_unavailable")
    print(cli(db, "devices", "llm=0,vision=1", "--reason", "W0-06 sinov: ikkinchi GPU xaritasi"))
    s2 = gate.tick()
    e2 = last_event("dedicated_unavailable")
    w4_conn = connect("w4_camera", db)
    dev_vision, dev_llm = device_for(w4_conn, "vision"), device_for(connect("w2_analyst", db), "llm")
    ov = cli(db, "override", "--mode", "dedicated", "--minutes", "1", "--reason", "W0-06 sinov: CHECK dedicated")
    oid = sql("SELECT max(id) FROM core.gpu_overrides")[0][0]
    print(cli(db, "cancel", str(oid)))
    print(cli(db, "devices", "llm=0,vision=0", "--reason", "sinovdan keyin: bitta GPU"))
    check("dedicated on one GPU: falls back to timeslice with a note, journaled; gpu.devices -> device for workers; "
          "override --mode dedicated accepted",
          s["requested_mode"] == "dedicated" and s["mode"] == "timeslice" and "dedicated mavjud emas" in s["note"]
          and "bir xil qurilmada" in (e1["reason"] or "") and "1 ta GPU" in (e2["reason"] or "") and s2["mode"] == "timeslice"
          and (dev_vision, dev_llm) == (1, 0) and "faol" in ov,
          f"{e1['reason']} | {e2['reason']} | vision->{dev_vision} llm->{dev_llm} | {ov}")

    # cleanup: timeslice is the configured standard (D-30); Ollama back to how it was; no test jobs left
    print(cli(db, "mode", "timeslice", "--reason", "sinovdan keyin D-30 holati"))
    if MODEL not in loaded_before:
        ollama.unload(MODEL)
    sql("DELETE FROM core.jobs")
    print(f"Ollama loaded now: {sorted(ollama.loaded())}, VRAM {gpu_memory().used_mib} MiB")
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="genesis_test")
    sys.exit(main(ap.parse_args().db))
