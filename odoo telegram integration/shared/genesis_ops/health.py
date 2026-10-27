"""Health signals (W0-08) -> core.alerts. One open row per problem key; a problem that is gone gets resolved_at.
Every check takes `now`, so tests can move the clock. Telegram delivery is W3-07's job (it reads api.v_alerts).

Checks (thresholds in core.config alerts.*):
  ingest_silence     W1 listener heartbeat older than N minutes (only once a listener has ever run)
  no_messages        no group message for M minutes inside the active hours (only once a listener has ever run)
  queue_backlog      too many claimable jobs, or the oldest claimable job waits too long (claimable = the gate allows
                     its gpu_class now; only job types that have a working consumer, so not-yet-built windows stay quiet)
  dead_jobs          dead jobs above the limit
  gpu_gate           gate reports an error, or its publication/heartbeat is stale
  disk_space         disk fill >= warn / crit percent (E:)
  recorder_heartbeat W4 recorder heartbeat file stale inside the camera's recording window
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Callable

from psycopg.types.json import Jsonb

from genesis_gpu import TASHKENT

KINDS = ("ingest_silence", "no_messages", "queue_backlog", "dead_jobs", "gpu_gate", "disk_space", "recorder_heartbeat")


@dataclass
class Finding:
    key: str
    kind: str
    severity: str  # warning | critical
    message: str
    details: dict = field(default_factory=dict)


def _q(conn, sql: str, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def config(conn) -> dict:
    return {k: v for k, v in _q(conn, "SELECT key, value FROM core.config WHERE key LIKE 'alerts.%' "
                                      "OR key LIKE 'schedule.%' OR key LIKE 'retention.%'")}


def _hm(t: datetime) -> str:
    return t.astimezone(TASHKENT).strftime("%H:%M")


def _in_hours(now: datetime, window: str) -> bool:
    a, b = window.split("-")
    start, end = dtime.fromisoformat(a), dtime.fromisoformat(b)
    t = now.astimezone(TASHKENT).time()
    return start <= t < end if start < end else (t >= start or t < end)


def check_ingest(conn, now: datetime, cfg: dict) -> list[Finding]:
    n = float(cfg["alerts.ingest_silence_minutes"])
    out = []
    for instance, beat, status in _q(conn, "SELECT instance, last_beat_at, status FROM core.heartbeats WHERE component = 'w1_ingest'"):
        if status != "stopped" and now - beat > timedelta(minutes=n):
            mins = int((now - beat).total_seconds() // 60)
            out.append(Finding(f"ingest_silence:w1_ingest/{instance}", "ingest_silence", "critical",
                               f"W1 tinglovchisi ({instance}) {mins} daqiqadan beri jim (oxirgi yurak urishi {_hm(beat)})",
                               {"instance": instance, "last_beat_at": beat.isoformat(), "minutes": mins, "limit": n}))
    return out


def check_messages(conn, now: datetime, cfg: dict) -> list[Finding]:
    if not _q(conn, "SELECT 1 FROM core.heartbeats WHERE component = 'w1_ingest' LIMIT 1"):
        return []
    if not _in_hours(now, cfg["alerts.no_messages_hours"]):
        return []
    last = _q(conn, "SELECT max(sent_at) FROM core.messages WHERE sent_at > %s - interval '7 days'", (now,))[0][0]
    m = float(cfg["alerts.no_messages_minutes"])
    if last is not None and now - last > timedelta(minutes=m):
        mins = int((now - last).total_seconds() // 60)
        return [Finding("no_messages", "no_messages", "warning",
                        f"{mins} daqiqadan beri birorta guruh xabari kelmadi (oxirgisi {_hm(last)})",
                        {"last_message_at": last.isoformat(), "minutes": mins, "limit": m})]
    return []


def check_queue(conn, now: datetime, cfg: dict) -> list[Finding]:
    rows = _q(conn, """
        WITH served AS (SELECT DISTINCT type FROM core.jobs
                        WHERE status IN ('running', 'done') AND coalesce(finished_at, locked_at, updated_at) > %(now)s - interval '7 days'),
             ready AS (SELECT j.type, j.run_after FROM core.jobs j JOIN served s USING (type)
                       WHERE j.status IN ('queued', 'failed') AND j.run_after <= %(now)s
                         AND (j.gpu_class = 'none' OR j.gpu_class = ANY(core.gpu_allowed_classes())))
        SELECT type, count(*), min(run_after) FROM ready GROUP BY type ORDER BY type""", {"now": now})
    total = sum(r[1] for r in rows)
    oldest = min((r[2] for r in rows), default=None)
    max_ready, max_age = int(cfg["alerts.queue_max_ready"]), float(cfg["alerts.queue_max_age_minutes"])
    age = (now - oldest).total_seconds() / 60 if oldest else 0
    if total > max_ready or age > max_age:
        per_type = {t: {"ready": n, "oldest": o.isoformat()} for t, n, o in rows}
        return [Finding("queue_backlog", "queue_backlog", "warning",
                        f"Navbat to'planib qoldi: {total} ta job olinishini kutmoqda, eng eskisi {int(age)} daqiqa",
                        {"ready": total, "oldest_minutes": int(age), "limits": [max_ready, max_age], "types": per_type})]
    return []


def check_dead(conn, now: datetime, cfg: dict) -> list[Finding]:
    rows = _q(conn, "SELECT type, count(*) FROM core.jobs WHERE status = 'dead' GROUP BY type ORDER BY type")
    total = sum(n for _, n in rows)
    if total > int(cfg["alerts.dead_jobs_max"]):
        return [Finding("dead_jobs", "dead_jobs", "warning", f"{total} ta job 'dead' (urinishlar tugagan)",
                        {"types": dict(rows)})]
    return []


def check_gate(conn, now: datetime, cfg: dict) -> list[Finding]:
    stale = timedelta(minutes=float(cfg["alerts.gpu_gate_stale_minutes"]))
    row = _q(conn, "SELECT phase, valid_until, note, updated_at FROM core.gpu_gate_state WHERE id = 1")
    beat = _q(conn, "SELECT last_beat_at FROM core.heartbeats WHERE component = 'w0_gpu_gate' ORDER BY last_beat_at DESC LIMIT 1")
    if not row:
        return []
    phase, valid_until, note, updated = row[0]
    if phase == "error":
        return [Finding("gpu_gate", "gpu_gate", "critical", f"GPU gate xato: {note or '?'}", {"phase": phase, "note": note})]
    if phase == "init" and not beat:
        return []  # the gate has never run (fresh database)
    last = max(valid_until, beat[0][0]) if beat else valid_until
    if now - last > stale:
        return [Finding("gpu_gate", "gpu_gate", "critical",
                        f"GPU gate jim: e'lon/yurak urishi {int((now - last).total_seconds() // 60)} daqiqa eski",
                        {"valid_until": valid_until.isoformat(), "heartbeat": beat[0][0].isoformat() if beat else None})]
    return []


def check_disk(conn, now: datetime, cfg: dict, usage: Callable = shutil.disk_usage) -> list[Finding]:
    warn, crit = float(cfg["alerts.disk_warn_pct"]), float(cfg["alerts.disk_crit_pct"])
    out = []
    for path in cfg["alerts.disk_paths"]:
        u = usage(path)
        pct = round(u.used / u.total * 100, 1)
        if pct >= warn:
            sev = "critical" if pct >= crit else "warning"
            out.append(Finding(f"disk:{path}", "disk_space", sev,
                               f"{path} disk {pct}% to'ldi ({u.free / 2**30:.0f} GB bo'sh)",
                               {"path": path, "used_pct": pct, "free_gb": round(u.free / 2**30, 1), "limits": [warn, crit]}))
    return out


def check_recorder(conn, now: datetime, cfg: dict, root: Path) -> list[Finding]:
    path = Path(str(cfg["alerts.recorder_heartbeat_file"]).replace("{root}", str(root)))
    stale = timedelta(minutes=float(cfg["alerts.recorder_stale_minutes"]))
    out = []
    for cam_id, start, end in _q(conn, "SELECT id, record_start, record_end FROM core.cameras WHERE enabled"):
        local = now.astimezone(TASHKENT)
        begin = datetime.combine(local.date(), start, TASHKENT) + stale  # grace after the window opens
        finish = datetime.combine(local.date(), end, TASHKENT)
        if not (begin <= local < finish):
            continue
        beat = None
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("camera") in (None, cam_id):
                beat = datetime.fromisoformat(data["ts_utc"].replace("Z", "+00:00"))
                _q(conn, """INSERT INTO core.heartbeats (component, instance, last_beat_at, status, version, details)
                            VALUES ('w4_recorder', %s, %s, 'ok', 'file', %s)
                            ON CONFLICT (component, instance) DO UPDATE SET last_beat_at = EXCLUDED.last_beat_at,
                                details = EXCLUDED.details""",
                   (cam_id, beat, Jsonb({k: data.get(k) for k in ("state", "day", "current_file", "segments_today",
                                                                  "disk_free_gb")})))
        if beat is None or now - beat > stale:
            ago = f"{int((now - beat).total_seconds() // 60)} daqiqa" if beat else "fayl yo'q"
            out.append(Finding(f"recorder_heartbeat:{cam_id}", "recorder_heartbeat", "critical",
                               f"W4 yozuvchisi ({cam_id}) yozuv oynasida jim: {ago}",
                               {"camera_id": cam_id, "file": str(path), "last_beat": beat.isoformat() if beat else None}))
    return out


def run_checks(conn, now: datetime, root: Path, usage: Callable = shutil.disk_usage) -> tuple[list[Finding], list[str]]:
    cfg = config(conn)
    findings: list[Finding] = []
    checked: list[str] = []
    for kind, fn in (("ingest_silence", lambda: check_ingest(conn, now, cfg)),
                     ("no_messages", lambda: check_messages(conn, now, cfg)),
                     ("queue_backlog", lambda: check_queue(conn, now, cfg)),
                     ("dead_jobs", lambda: check_dead(conn, now, cfg)),
                     ("gpu_gate", lambda: check_gate(conn, now, cfg)),
                     ("disk_space", lambda: check_disk(conn, now, cfg, usage)),
                     ("recorder_heartbeat", lambda: check_recorder(conn, now, cfg, root))):
        findings.extend(fn())  # an exception propagates: the scheduler raises a 'scheduler' alert for it
        checked.append(kind)
    return findings, checked


def sync_alerts(conn, findings: list[Finding], checked: list[str], now: datetime) -> dict:
    """Open/refresh alerts for current findings, resolve open alerts of checked kinds that are gone."""
    opened = refreshed = 0
    for f in findings:
        row = _q(conn, """UPDATE core.alerts SET last_seen_at = %s, severity = %s, message = %s, details = %s
                          WHERE key = %s AND resolved_at IS NULL RETURNING id""",
                 (now, f.severity, f.message, Jsonb(f.details), f.key))
        if row:
            refreshed += 1
        else:
            _q(conn, """INSERT INTO core.alerts (key, kind, severity, message, details, first_seen_at, last_seen_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)""", (f.key, f.kind, f.severity, f.message, Jsonb(f.details), now, now))
            opened += 1
    keys = [f.key for f in findings]
    resolved = _q(conn, """UPDATE core.alerts SET resolved_at = %s WHERE resolved_at IS NULL AND kind = ANY(%s)
                           AND NOT (key = ANY(%s)) RETURNING key""", (now, checked, keys)) or []
    return {"opened": opened, "refreshed": refreshed, "resolved": [r[0] for r in resolved]}


def raise_scheduler_alert(conn, key: str, message: str, details: dict, now: datetime, resolve: bool = False) -> None:
    if resolve:
        _q(conn, "UPDATE core.alerts SET resolved_at = %s WHERE key = %s AND resolved_at IS NULL", (now, key))
        return
    sync = _q(conn, "UPDATE core.alerts SET last_seen_at = %s, message = %s, details = %s WHERE key = %s AND resolved_at IS NULL "
                    "RETURNING id", (now, message, Jsonb(details), key))
    if not sync:
        _q(conn, "INSERT INTO core.alerts (key, kind, severity, message, details, first_seen_at, last_seen_at) "
                 "VALUES (%s, 'scheduler', 'warning', %s, %s, %s, %s)", (key, message, Jsonb(details), now, now))
