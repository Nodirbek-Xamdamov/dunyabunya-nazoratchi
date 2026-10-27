"""GPU gate CLI (w0_admin).

    python -m genesis_gpu run [--interval 15]                 # the gate loop; service genesis_gpu_gate (W0-07)
    python -m genesis_gpu tick | state | log [--limit 30]
    python -m genesis_gpu mode timeslice|coresident|dedicated --reason "..."   # no restart needed (D-30)
    python -m genesis_gpu devices llm=0,vision=1 --reason "..."               # dedicated: gpu_class -> GPU index
    python -m genesis_gpu override (--allow none,llm,vision | --mode M) --minutes N --reason "..."
    python -m genesis_gpu cancel ID
All commands take --db (default: config.toml postgres.database).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from genesis_queue import connect

from .gate import GpuGate, describe
from .policy import MODES

CLASSES = {"none", "llm", "vision"}


def rows(conn, sql: str, params=None) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


def run_loop(conn, db: str | None, interval: float) -> int:
    """The service loop (W0-07): a failed tick (database or Ollama down) is logged and retried, the database
    connection is reopened; GENESIS_STOP_FILE (set by genesis_service) makes it exit cleanly."""
    stop_file = os.environ.get("GENESIS_STOP_FILE")
    gate = GpuGate(conn)
    last, ticks = None, 0
    while True:
        try:
            state = gate.tick()
            key = {k: v for k, v in state.items() if k not in ("at", "vram_used_mib")}
            if key != last or ticks % 40 == 0:  # print changes, and a heartbeat line every ~10 minutes
                print(describe(state), flush=True)
            last, ticks = key, ticks + 1
        except Exception as exc:  # keep the gate alive; workers get only 'none' until a tick succeeds again
            print(f"tick xatosi: {exc!r}", file=sys.stderr, flush=True)
            try:
                gate.publish_failure(repr(exc))
            except Exception as exc2:  # the database itself is unreachable: workers cannot claim anything either
                print(f"xato e'lonini yozib bo'lmadi: {exc2!r}", file=sys.stderr, flush=True)
            if gate.conn.closed or gate.conn.broken:
                try:
                    gate.conn = connect("w0_admin", db)
                    print("bazaga qayta ulandi", file=sys.stderr, flush=True)
                except Exception as exc2:
                    print(f"bazaga ulanib bo'lmadi: {exc2!r}", file=sys.stderr, flush=True)
            last = None
        deadline = time.monotonic() + interval
        while time.monotonic() < deadline:
            if stop_file and os.path.exists(stop_file):
                print("stop fayli: gate to'xtadi", flush=True)
                return 0
            time.sleep(1)


def main() -> int:
    ap = argparse.ArgumentParser(prog="genesis_gpu")
    ap.add_argument("command", choices=["run", "tick", "state", "log", "mode", "devices", "override", "cancel"])
    ap.add_argument("value", nargs="?")
    ap.add_argument("--db")
    ap.add_argument("--interval", type=float, default=15)
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--allow")
    ap.add_argument("--mode", dest="ov_mode", choices=list(MODES))
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--reason")
    a = ap.parse_args()
    conn = connect("w0_admin", a.db)

    if a.command == "tick":
        print(describe(GpuGate(conn).tick()), flush=True)
        return 0
    if a.command == "run":
        return run_loop(conn, a.db, a.interval)
    if a.command == "state":
        for r in rows(conn, "SELECT row_to_json(v) FROM api.v_gpu_gate v"):
            print(json.dumps(r[0], ensure_ascii=False, indent=1))
        return 0
    if a.command == "log":
        for at, sim, ev, det in reversed(rows(conn, "SELECT at, sim_at, event, details FROM core.gpu_gate_log ORDER BY id DESC LIMIT %s",
                                              (a.limit,))):
            print(f"{at:%Y-%m-%d %H:%M:%S}{' sim ' + format(sim, '%H:%M') if sim else ''}  {ev:<16} {json.dumps(det, ensure_ascii=False)}")
        return 0
    if a.command == "mode":
        if a.value not in MODES or not a.reason:
            ap.error("mode timeslice|coresident|dedicated --reason ...")
        rows(conn, "UPDATE core.config SET value = %s WHERE key = 'gpu.mode'", (json.dumps(a.value),))
        rows(conn, "INSERT INTO core.gpu_gate_log (event, details) VALUES ('mode_set', %s)",
             (json.dumps({"mode": a.value, "reason": a.reason}),))
        print(f"gpu.mode = {a.value} (gate keyingi siklda qo'llaydi)")
        return 0
    if a.command == "devices":
        try:
            devices = {k.strip(): int(v) for k, v in (part.split("=") for part in (a.value or "").split(","))}
        except ValueError:
            devices = {}
        if set(devices) != {"llm", "vision"} or min(devices.values()) < 0 or not a.reason:
            ap.error("devices llm=N,vision=M --reason ...")
        rows(conn, "UPDATE core.config SET value = %s WHERE key = 'gpu.devices'", (json.dumps(devices),))
        rows(conn, "INSERT INTO core.gpu_gate_log (event, details) VALUES ('devices_set', %s)",
             (json.dumps({"devices": devices, "reason": a.reason}),))
        print(f"gpu.devices = {devices} (dedicated rejimida ishlatiladi)")
        return 0
    if a.command == "override":
        allow = sorted(set(a.allow.split(","))) if a.allow else None
        if (allow is None and a.ov_mode is None) or not a.reason or (allow and not set(allow) <= CLASSES):
            ap.error("override (--allow none,llm,vision | --mode M) --minutes N --reason ...")
        oid = rows(conn, """INSERT INTO core.gpu_overrides (mode, allowed_classes, ends_at, reason)
                            VALUES (%s, %s, now() + make_interval(secs => %s), %s) RETURNING id""",
                   (a.ov_mode, allow, a.minutes * 60, a.reason))[0][0]
        rows(conn, "INSERT INTO core.gpu_gate_log (event, details) VALUES ('override_set', %s)",
             (json.dumps({"id": oid, "mode": a.ov_mode, "allow": allow, "minutes": a.minutes, "reason": a.reason}),))
        print(f"override #{oid} faol {a.minutes:g} daqiqa")
        return 0
    if a.command == "cancel":
        n = rows(conn, "UPDATE core.gpu_overrides SET cancelled_at = now(), cancelled_by = current_user "
                       "WHERE id = %s AND cancelled_at IS NULL RETURNING id", (int(a.value),))
        rows(conn, "INSERT INTO core.gpu_gate_log (event, details) VALUES ('override_cancelled', %s)",
             (json.dumps({"id": int(a.value), "found": bool(n)}),))
        print(f"override #{a.value} bekor qilindi" if n else f"override #{a.value} topilmadi yoki allaqachon bekor")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
