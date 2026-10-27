"""W0-08 tests (genesis_test only; destructive for jobs/alerts/scheduler_runs there): health signals with a moved
clock, a real fake W1 listener process, retention dry-run on a throwaway folder tree (keep folders protected), the
scheduler's once-per-day jobs. Afterwards: python -E shared/fixtures/load_fixtures.py

    apps\\w0-core\\.venv\\Scripts\\python.exe -E shared\\genesis_ops\\tests\\run_ops_tests.py [--db genesis_test]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from collections import namedtuple
from datetime import date, datetime, timedelta
from pathlib import Path

from psycopg.types.json import Jsonb

from genesis_common import load_config
from genesis_gpu import TASHKENT
from genesis_ops import health, retention
from genesis_ops.scheduler import Scheduler
from genesis_queue import JobQueue, connect

HERE = Path(__file__).resolve().parent
ROOT = Path(load_config()["project"]["root"])
Usage = namedtuple("Usage", "total used free")
results: list[tuple[str, bool, str]] = []


def check(name: str, ok, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""), flush=True)


def main(db: str) -> int:
    if db == "genesis":
        raise SystemExit("refusing to run destructive W0-08 tests on the production database 'genesis'")
    admin = connect("w0_admin", db)

    def sql(text, params=None):
        with admin.cursor() as cur:
            cur.execute(text, params)
            return cur.fetchall() if cur.description else cur.rowcount

    def open_alerts() -> dict:
        return {k: (s, m) for k, s, m in sql("SELECT key, severity, message FROM core.alerts WHERE resolved_at IS NULL")}

    ok_disk = lambda p: Usage(100, 40, 60)

    def checks(now, usage=ok_disk):
        f, c = health.run_checks(admin, now, ROOT, usage)
        return health.sync_alerts(admin, f, c, now)

    sql("DELETE FROM core.alerts"); sql("DELETE FROM core.scheduler_runs"); sql("DELETE FROM core.jobs")
    sql("DELETE FROM core.heartbeats WHERE component IN ('w1_ingest', 'w4_recorder')")

    # A. ingest silence: real listener process, killed; the signal appears after 10 minutes (alerts.ingest_silence_minutes)
    listener = subprocess.Popen([sys.executable, "-E", str(HERE / "fake_listener.py"), db])
    time.sleep(8)
    now = datetime.now(TASHKENT)
    checks(now)
    quiet_while_alive = "ingest_silence:w1_ingest/test" not in open_alerts()
    subprocess.run(["taskkill", "/PID", str(listener.pid), "/T", "/F"], capture_output=True)
    last = sql("SELECT last_beat_at FROM core.heartbeats WHERE component = 'w1_ingest' AND instance = 'test'")[0][0]
    time.sleep(6)
    checks(last + timedelta(minutes=9, seconds=30))
    at_9 = "ingest_silence:w1_ingest/test" in open_alerts()
    checks(last + timedelta(minutes=10, seconds=30))
    al = open_alerts().get("ingest_silence:w1_ingest/test")
    with connect("w3_dashboard", db).cursor() as cur:
        cur.execute("SELECT count(*) FROM api.v_alerts WHERE key = 'ingest_silence:w1_ingest/test' AND is_open")
        w3_sees = cur.fetchone()[0]
        cur.execute("SELECT open_alerts, critical_alerts FROM api.v_system_health")
        health_row = cur.fetchone()
        cur.execute("UPDATE core.alerts SET notified_at = now(), notified_by = 'w3-test' WHERE key = 'ingest_silence:w1_ingest/test' AND resolved_at IS NULL")
        notified = cur.rowcount
    check("ingest: listener alive -> quiet; killed -> no signal at 9.5 min, critical signal at 10.5 min; W3 sees it "
          "(v_alerts, v_system_health) and can mark it sent",
          quiet_while_alive and not at_9 and al and al[0] == "critical" and w3_sees == 1 and health_row[0] >= 1
          and health_row[1] >= 1 and notified == 1, f"{al and al[1]}; health open/critical {health_row}")
    listener = subprocess.Popen([sys.executable, "-E", str(HERE / "fake_listener.py"), db])
    time.sleep(8)
    out = checks(datetime.now(TASHKENT))
    subprocess.run(["taskkill", "/PID", str(listener.pid), "/T", "/F"], capture_output=True)
    check("ingest: listener back -> signal resolved", "ingest_silence:w1_ingest/test" in out["resolved"], f"{out}")
    sql("UPDATE core.heartbeats SET status = 'stopped' WHERE component = 'w1_ingest'")

    # B. queue backlog (only job types with a working consumer) and dead jobs
    adm_q, w2 = JobQueue(admin, worker="t"), JobQueue(connect("w2_analyst", db), worker="w2")
    j0 = adm_q.enqueue("transcribe", {"n": 0}, key="ops:0").id
    w2.claim(["transcribe"]); w2.complete(j0)                       # transcribe has a consumer
    for i in (1, 2):
        adm_q.enqueue("transcribe", {"n": i}, key=f"ops:{i}")
    adm_q.enqueue("build_excel_tg", {"n": 1}, key="ops:x")          # no W3 consumer yet -> no signal for it
    now = datetime.now(TASHKENT)
    checks(now + timedelta(minutes=30))
    q30 = "queue_backlog" in open_alerts()
    checks(now + timedelta(minutes=61))
    qa = open_alerts().get("queue_backlog")
    det = sql("SELECT details FROM core.alerts WHERE key = 'queue_backlog' AND resolved_at IS NULL")[0][0]
    check("queue: claimable transcribe jobs waiting > 60 min -> signal; type without a consumer is ignored",
          not q30 and qa and set(det["types"]) == {"transcribe"} and det["ready"] == 2, f"{qa and qa[1]}")
    sql("UPDATE core.jobs SET status = 'dead' WHERE idempotency_key = 'transcribe:ops:1'")
    checks(now)
    check("dead jobs above alerts.dead_jobs_max -> signal", "dead_jobs" in open_alerts(), open_alerts().get("dead_jobs", ("", ""))[1])

    # C. GPU gate error / silence
    sql("UPDATE core.gpu_gate_state SET phase = 'error', note = 'sinov: Ollama javob bermadi', valid_until = now() + interval '90 s'")
    checks(datetime.now(TASHKENT))
    g1 = open_alerts().get("gpu_gate")
    sql("UPDATE core.gpu_gate_state SET phase = 'steady', note = NULL, valid_until = now() - interval '10 minutes'")
    sql("DELETE FROM core.heartbeats WHERE component = 'w0_gpu_gate'")
    checks(datetime.now(TASHKENT))
    g2 = open_alerts().get("gpu_gate")
    sql("UPDATE core.gpu_gate_state SET valid_until = now() + interval '90 s'")
    out = checks(datetime.now(TASHKENT))
    check("gpu gate: error -> critical; stale publication -> critical; fresh again -> resolved",
          g1 and "xato" in g1[1] and g2 and "jim" in g2[1] and "gpu_gate" in out["resolved"], f"{g1} | {g2}")

    # D. disk E: 80 % / 90 %
    now = datetime.now(TASHKENT)
    checks(now, lambda p: Usage(1000, 850, 150)); d85 = open_alerts().get("disk:E:\\")
    checks(now, lambda p: Usage(1000, 920, 80)); d92 = open_alerts().get("disk:E:\\")
    out = checks(now, lambda p: Usage(1000, 500, 500))
    check("disk: 85% -> warning, 92% -> critical (same alert row), 50% -> resolved",
          d85 and d85[0] == "warning" and d92 and d92[0] == "critical" and "disk:E:\\" in out["resolved"]
          and sql("SELECT count(*) FROM core.alerts WHERE key = 'disk:E:\\'")[0][0] == 1, f"{d85} | {d92}")

    # E. W4 recorder heartbeat (file) inside / outside the recording window
    tmp = Path(tempfile.mkdtemp(prefix="w0ops_"))
    hb = tmp / "recorder_heartbeat.json"
    sql("UPDATE core.config SET value = %s WHERE key = 'alerts.recorder_heartbeat_file'", (Jsonb(str(hb)),))
    day = datetime.now(TASHKENT).date()
    noon = datetime.combine(day, datetime.min.time(), TASHKENT).replace(hour=12)
    hb.write_text(json.dumps({"ts_utc": (noon - timedelta(minutes=12)).astimezone().isoformat(), "camera": "camera1",
                              "state": "recording"}), encoding="utf-8")
    checks(noon); r1 = open_alerts().get("recorder_heartbeat:camera1")
    hb.write_text(json.dumps({"ts_utc": (noon - timedelta(seconds=30)).astimezone().isoformat(), "camera": "camera1",
                              "state": "recording"}), encoding="utf-8")
    out1 = checks(noon)
    hb.unlink()
    out2 = checks(noon.replace(hour=23))
    mirrored = sql("SELECT count(*) FROM core.heartbeats WHERE component = 'w4_recorder' AND instance = 'camera1'")[0][0]
    check("W4 recorder: heartbeat 12 min old inside 07:00-22:00 -> critical; fresh -> resolved; no file at 23:00 -> quiet; "
          "mirrored to core.heartbeats", r1 and r1[0] == "critical" and "recorder_heartbeat:camera1" in out1["resolved"]
          and "recorder_heartbeat:camera1" not in open_alerts() and mirrored == 1, f"{r1 and r1[1]}")
    sql("UPDATE core.config SET value = %s WHERE key = 'alerts.recorder_heartbeat_file'",
        (Jsonb("{root}\\var\\w4-camera\\recorder_heartbeat.json"),))

    # F. retention plan on a throwaway tree: what goes, what is protected
    vroot = tmp / "video"
    old, recent = "2026-10-01", "2026-10-20"
    for rel in [f"camera1/{old}", f"camera1/{recent}", "camera1/2026-09-30", "camera1/2026-10-02", "camera1/2026-10-05",
                "camera1/backup/2026-01-01", "camera2/2026-10-03", "Kamera raw data/2025-12-31"]:
        (vroot / rel).mkdir(parents=True, exist_ok=True)
        (vroot / rel / "a.mkv").write_bytes(b"x" * 1000)
    (vroot / "camera1/2026-09-30/KEEP").write_text("haqiqat to'plami", encoding="utf-8")
    keep_root = tmp / "keep"
    (keep_root / "2026-01-01").mkdir(parents=True)
    protect = [str(vroot / "Kamera raw data"), str(vroot / "camera1" / "2026-10-02"), str(keep_root)]
    plan = retention.plan_video(vroot, 14, date(2026, 10, 27), protect, {("camera1", "2026-10-05")})
    cands = sorted(c["path"].replace(str(vroot), "").replace("\\", "/") for c in plan.candidates)
    prot = {p["path"].replace(str(vroot), "").replace("\\", "/"): p["reason"] for p in plan.protected}
    check("retention dry-run: only old day folders are candidates; KEEP file, protect_paths, is_keep, 'backup' and the "
          "old project's folder are never listed",
          cands == ["/camera1/2026-10-01", "/camera2/2026-10-03"] and set(prot) == {"/camera1/2026-09-30", "/camera1/2026-10-02",
          "/camera1/2026-10-05", "/Kamera raw data"} and plan.total_bytes == 2000, f"candidates {cands}; protected {prot}")
    refused = retention.plan_video(keep_root, 14, date(2026, 10, 27), protect)
    check("retention: a video_root inside a protected folder is refused entirely", refused.refused and not refused.candidates,
          refused.refused)
    deleted = retention.delete_video(plan, vroot, protect)
    left = sorted(str(p.relative_to(vroot)).replace("\\", "/") for p in vroot.glob("*/*") if p.is_dir())
    check("retention delete (throwaway tree only): candidates gone, every protected folder still there",
          all(d["deleted"] for d in deleted) and left == ["Kamera raw data/2025-12-31", "camera1/2026-09-30", "camera1/2026-10-02",
          "camera1/2026-10-05", "camera1/2026-10-20", "camera1/backup"] and (keep_root / "2026-01-01").exists(), f"left {left}")
    due_now = retention.text_due(admin, datetime.now(TASHKENT))["episodes_due"]
    due_later = retention.text_due(admin, datetime.now(TASHKENT) + timedelta(days=5))["episodes_due"]
    check("retention text: unrelated episodes found by retain_until (72 h), nothing purged in dry-run",
          due_later >= 1 and due_later >= due_now, f"due now {due_now}, in 5 days {due_later}")

    # G. scheduler: once-per-day jobs with an accelerated clock; retention stays a dry-run
    clock = {"now": datetime(2026, 10, 27, 22, 15, tzinfo=TASHKENT)}
    sched = Scheduler(admin, ROOT, clock=lambda: clock["now"], disk_usage=ok_disk, report_dir=tmp / "reports")
    sql("UPDATE core.config SET value = %s WHERE key = 'retention.video_root'", (Jsonb(str(vroot)),))
    sql("UPDATE core.config SET value = '\"2026-10-01\"' WHERE key = 'schedule.start_day'")
    first = sched.tick(force_health=True)
    second = sched.tick()
    jobs = sql("SELECT type, idempotency_key, target_role FROM core.jobs WHERE type IN ('camera_day_batch', 'daily_close') ORDER BY 2")
    rep = json.loads(Path(first["ran"]["retention"]["report"]).read_text(encoding="utf-8"))
    check("scheduler 22:15: camera_day_batch for today + daily_close (W2, W3) for yesterday + partitions + retention dry-run; "
          "second tick does nothing",
          set(first["ran"]) == {"partitions", "daily_close", "camera_batch", "retention"} and second["ran"] == {}
          and ("camera_day_batch", "camera_day_batch:camera1:2026-10-27", None) in jobs
          and ("daily_close", "daily_close:2026-10-26:w2_analyst", "w2_analyst") in jobs
          and ("daily_close", "daily_close:2026-10-26:w3_dashboard", "w3_dashboard") in jobs
          and rep["mode"] == "dry-run" and first["ran"]["camera_batch"]["inside_window"] is True
          and (vroot / "camera1" / "2026-10-20").exists(), f"jobs {jobs}; retention {rep['mode']}")
    clock["now"] = datetime(2026, 10, 28, 0, 10, tzinfo=TASHKENT)
    third = sched.tick()
    n_batch = sql("SELECT count(*) FROM core.jobs WHERE type = 'camera_day_batch'")[0][0]
    check("scheduler 00:10 next day: daily_close for 10-27; camera batch not duplicated", set(third["ran"]) == {"daily_close"}
          and n_batch == 1 and third["ran"]["daily_close"]["w2_analyst"]["created"], f"{third['ran']}")
    sql("UPDATE core.config SET value = %s WHERE key = 'retention.video_root'", (Jsonb("E:\\data\\video"),))

    # H. failing task -> scheduler alert, retried later
    sql("UPDATE core.config SET value = '\"2026-10-29\"' WHERE key = 'schedule.start_day'")
    sql("UPDATE core.config SET value = '\"25:99\"' WHERE key = 'schedule.partitions_time'")
    clock["now"] = datetime(2026, 10, 29, 2, 0, tzinfo=TASHKENT)
    try:
        sched.tick(force_health=True)
        broke = False
    except ValueError:
        broke = True
    sql("UPDATE core.config SET value = '\"01:00\"' WHERE key = 'schedule.partitions_time'")
    sched_ok = sched.tick(force_health=True)
    sql("UPDATE core.config SET value = '\"2026-10-27\"' WHERE key = 'schedule.start_day'")
    check("bad schedule setting: tick raises (the service loop logs it and retries); fixed -> runs again; "
          "schedule.start_day 10-29 -> no daily_close/camera batch for 10-28",
          broke and "partitions" in sched_ok["ran"] and "daily_close" not in sched_ok["ran"]
          and "camera_batch" not in sched_ok["ran"], f"{sched_ok['ran'].keys()}")

    # real dry-run against the production layout (read-only listing)
    real = retention.plan_video(Path("E:\\data\\video"), 14, datetime.now(TASHKENT).date(),
                                ["E:\\Kamera raw data", "E:\\data\\video_keep", "F:\\genesis\\keep"])
    print("REAL E:\\data\\video dry-run:", json.dumps({"cutoff": real.cutoff, "candidates": real.candidates,
                                                      "protected": real.protected, "refused": real.refused}, ensure_ascii=False))
    sql("DELETE FROM core.jobs"); sql("DELETE FROM core.alerts"); sql("DELETE FROM core.scheduler_runs")
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="genesis_test")
    sys.exit(main(ap.parse_args().db))
