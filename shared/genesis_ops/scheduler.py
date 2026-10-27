"""W0 scheduler (W0-08), runs as service genesis_scheduler (shared/genesis_service). Every tick:
  - health checks -> core.alerts (every alerts interval);
  - daily tasks, each at most once per day (core.scheduler_runs; a missed one runs when the scheduler is back):
      partitions    ensure_month_partitions()                         at schedule.partitions_time
      daily_close   two daily_close jobs for yesterday (W2, W3)        at schedule.daily_close_time
      camera_batch  camera_day_batch job per enabled camera for today  at schedule.camera_batch_time (D-30 camera window)
      retention     video day folders + unrelated text (dry-run until retention.delete_enabled)  at schedule.retention_time
A failing task raises a 'scheduler' alert and is retried after RETRY_MINUTES.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Callable

from psycopg.types.json import Jsonb

from genesis_gpu import TASHKENT
from genesis_gpu.policy import in_window, parse_window
from genesis_queue import JobQueue

from . import health, retention

RETRY_MINUTES = 15
HEALTH_EVERY_S = 60


def _q(conn, sql: str, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _at(now: datetime, hhmm: str) -> datetime:
    return datetime.combine(now.astimezone(TASHKENT).date(), dtime.fromisoformat(hhmm), TASHKENT)


class Scheduler:
    def __init__(self, conn, root: Path, clock: Callable[[], datetime] | None = None,
                 disk_usage: Callable = shutil.disk_usage, report_dir: Path | None = None):
        self.conn = conn
        self.root = root
        self.clock = clock or (lambda: datetime.now(TASHKENT))
        self.disk_usage = disk_usage
        self.report_dir = report_dir or root / "var" / "run" / "retention"
        self.queue = JobQueue(conn, worker="w0_scheduler")
        self._last_health = 0.0

    # -- run-once bookkeeping ------------------------------------------------------------------------
    def _claim_run(self, task: str, run_key: str, now: datetime) -> bool:
        """True if this task/run_key should run now (never ran, or failed long enough ago)."""
        row = _q(self.conn, "SELECT ok, finished_at, started_at FROM core.scheduler_runs WHERE task = %s AND run_key = %s",
                 (task, run_key))
        if not row:
            _q(self.conn, "INSERT INTO core.scheduler_runs (task, run_key, started_at) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
               (task, run_key, now))
            return True
        ok, finished, started = row[0]
        if ok is False and finished is not None and now - finished >= timedelta(minutes=RETRY_MINUTES):
            _q(self.conn, "UPDATE core.scheduler_runs SET started_at = %s, finished_at = NULL, ok = NULL WHERE task = %s AND run_key = %s",
               (now, task, run_key))
            return True
        return False

    def _finish(self, task: str, run_key: str, now: datetime, ok: bool, result: dict) -> None:
        _q(self.conn, "UPDATE core.scheduler_runs SET finished_at = %s, ok = %s, result = %s WHERE task = %s AND run_key = %s",
           (now, ok, Jsonb(result), task, run_key))
        health.raise_scheduler_alert(self.conn, f"scheduler:{task}",
                                     f"Scheduler vazifasi '{task}' ({run_key}) xato: {result.get('error', '')[:200]}",
                                     {"task": task, "run_key": run_key, **result}, now, resolve=ok)

    def _run(self, task: str, run_key: str, now: datetime, fn: Callable[[], dict]) -> dict | None:
        if not self._claim_run(task, run_key, now):
            return None
        try:
            result = fn()
            self._finish(task, run_key, now, True, result)
        except Exception as exc:
            result = {"error": repr(exc), "trace": traceback.format_exc()[-1500:]}
            self._finish(task, run_key, now, False, result)
        return result

    # -- tasks ---------------------------------------------------------------------------------------
    def task_partitions(self) -> dict:
        return {"ensure_month_partitions": _q(self.conn, "SELECT core.ensure_month_partitions()")[0][0]}

    def task_daily_close(self, day: str) -> dict:
        out = {}
        for role in ("w2_analyst", "w3_dashboard"):
            e = self.queue.enqueue("daily_close", {"day": day, "for": role}, key=f"{day}:{role}", target_role=role)
            out[role] = {"job_id": e.id, "created": e.created}
        return out

    def task_camera_batch(self, day: str, cfg: dict, now: datetime) -> dict:
        gpu = dict(_q(self.conn, "SELECT key, value FROM core.config WHERE key IN ('gpu.mode', 'gpu.vision_window')"))
        local = now.astimezone(TASHKENT)
        inside = in_window(local.hour * 60 + local.minute, parse_window(gpu["gpu.vision_window"]))
        out = {"camera_window": gpu["gpu.vision_window"], "inside_window": inside, "jobs": {}}
        for (cam,) in _q(self.conn, "SELECT id FROM core.cameras WHERE enabled ORDER BY id"):
            e = self.queue.enqueue("camera_day_batch", {"camera_id": cam, "day": day}, key=f"{cam}:{day}")
            out["jobs"][cam] = {"job_id": e.id, "created": e.created}
        return out

    def task_retention(self, cfg: dict, now: datetime) -> dict:
        result = retention.run(self.conn, now, cfg)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        report = self.report_dir / f"retention_{now.astimezone(TASHKENT):%Y-%m-%d}_{result['mode']}.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return {"mode": result["mode"], "report": str(report), "video_candidates": len(result["video_candidates"]),
                "video_bytes": result["video_bytes"], "video_protected": len(result["video_protected"]),
                "refused": result["refused"], "text": result["text"], "text_purged": result.get("text_purged")}

    # -- one step ------------------------------------------------------------------------------------
    def tick(self, force_health: bool = False) -> dict:
        now = self.clock()
        cfg = health.config(self.conn)
        local = now.astimezone(TASHKENT)
        today, yesterday = local.date(), local.date() - timedelta(days=1)
        start_day = cfg.get("schedule.start_day")  # no jobs for days before the scheduler existed
        allowed = lambda d: start_day is None or d.isoformat() >= start_day
        ran = {}
        if now >= _at(now, cfg["schedule.partitions_time"]):
            ran["partitions"] = self._run("partitions", today.isoformat(), now, self.task_partitions)
        if now >= _at(now, cfg["schedule.daily_close_time"]) and allowed(yesterday):
            ran["daily_close"] = self._run("daily_close", yesterday.isoformat(), now,
                                           lambda: self.task_daily_close(yesterday.isoformat()))
        batch_at = _at(now, cfg["schedule.camera_batch_time"])
        # today's batch after its time; before it (night/morning) yesterday's, if it was missed
        batch_day = today if now >= batch_at else yesterday
        if allowed(batch_day):
            ran["camera_batch"] = self._run("camera_batch", batch_day.isoformat(), now,
                                            lambda: self.task_camera_batch(batch_day.isoformat(), cfg, now))
        if now >= _at(now, cfg["schedule.retention_time"]):
            ran["retention"] = self._run("retention", today.isoformat(), now, lambda: self.task_retention(cfg, now))
        alerts = None
        if force_health or time.monotonic() - self._last_health >= HEALTH_EVERY_S:
            try:
                findings, checked = health.run_checks(self.conn, now, self.root, self.disk_usage)
                alerts = health.sync_alerts(self.conn, findings, checked, now)
                health.raise_scheduler_alert(self.conn, "scheduler:health", "", {}, now, resolve=True)
            except Exception as exc:
                health.raise_scheduler_alert(self.conn, "scheduler:health", f"Signal tekshiruvi xato: {exc!r}"[:300],
                                             {"error": repr(exc)}, now)
                alerts = {"error": repr(exc)}
            self._last_health = time.monotonic()
        _q(self.conn, """INSERT INTO core.heartbeats (component, instance, last_beat_at, started_at, status, version, details)
                         VALUES ('w0_scheduler', 'main', now(), now(), 'ok', 'v1', %s)
                         ON CONFLICT (component, instance) DO UPDATE SET last_beat_at = now(), status = 'ok',
                             details = EXCLUDED.details""",
           (Jsonb({"at": local.isoformat(timespec="minutes"), "ran": [k for k, v in ran.items() if v is not None]}),))
        return {"at": local.isoformat(timespec="minutes"), "ran": {k: v for k, v in ran.items() if v is not None},
                "alerts": alerts}


def run_loop(conn_factory: Callable, root: Path, interval: float = 30) -> int:
    """Service loop: survives database errors (reconnects), exits on GENESIS_STOP_FILE."""
    stop_file = os.environ.get("GENESIS_STOP_FILE")
    conn = conn_factory()
    sched = Scheduler(conn, root)
    while True:
        try:
            out = sched.tick()
            health.raise_scheduler_alert(conn, "scheduler:tick", "", {}, sched.clock(), resolve=True)
            if out["ran"] or (out["alerts"] and (out["alerts"].get("opened") or out["alerts"].get("resolved"))):
                print(json.dumps(out, ensure_ascii=False, default=str), flush=True)
        except Exception as exc:
            print(f"tick xatosi: {exc!r}", file=sys.stderr, flush=True)
            try:  # e.g. a broken schedule setting: make it visible to W3 as well
                health.raise_scheduler_alert(conn, "scheduler:tick", f"Scheduler sikli xato: {exc!r}"[:300],
                                             {"error": repr(exc)}, sched.clock())
            except Exception:
                pass
            if conn.closed or conn.broken:
                try:
                    conn = conn_factory()
                    sched = Scheduler(conn, root)
                except Exception as exc2:
                    print(f"bazaga ulanib bo'lmadi: {exc2!r}", file=sys.stderr, flush=True)
        deadline = time.monotonic() + interval
        while time.monotonic() < deadline:
            if stop_file and os.path.exists(stop_file):
                print("stop fayli: scheduler to'xtadi", flush=True)
                return 0
            time.sleep(1)
