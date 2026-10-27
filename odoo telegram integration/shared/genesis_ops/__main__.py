"""W0 operations (W0-08): scheduler, health signals, retention.

    python -m genesis_ops run [--interval 30]       # the scheduler loop (service genesis_scheduler)
    python -m genesis_ops tick                      # one scheduler pass (health checks included)
    python -m genesis_ops check                     # health checks only, prints open alerts
    python -m genesis_ops retention                 # retention plan (dry-run regardless of settings)
    python -m genesis_ops alerts | runs
All take --db (default: config.toml postgres.database). Runs as w0_admin.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from genesis_common import load_config
from genesis_gpu import TASHKENT
from genesis_queue import connect

from . import health, retention
from .scheduler import Scheduler, run_loop


def main() -> int:
    ap = argparse.ArgumentParser(prog="genesis_ops")
    ap.add_argument("command", choices=["run", "tick", "check", "retention", "alerts", "runs"])
    ap.add_argument("--db")
    ap.add_argument("--interval", type=float, default=30)
    a = ap.parse_args()
    root = Path(load_config()["project"]["root"])
    if a.command == "run":
        return run_loop(lambda: connect("w0_admin", a.db), root, a.interval)
    conn = connect("w0_admin", a.db)
    now = datetime.now(TASHKENT)
    if a.command == "tick":
        print(json.dumps(Scheduler(conn, root).tick(force_health=True), ensure_ascii=False, indent=1, default=str))
    elif a.command == "check":
        findings, checked = health.run_checks(conn, now, root)
        print(json.dumps(health.sync_alerts(conn, findings, checked, now), ensure_ascii=False))
        for f in findings:
            print(f"[{f.severity}] {f.key}: {f.message}")
    elif a.command == "retention":
        out = retention.run(conn, now, health.config(conn), delete_enabled=False)
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    elif a.command == "alerts":
        with conn.cursor() as cur:
            cur.execute("SELECT row_to_json(v) FROM api.v_alerts v ORDER BY is_open DESC, last_seen_at DESC LIMIT 50")
            for (row,) in cur.fetchall():
                print(json.dumps(row, ensure_ascii=False))
    elif a.command == "runs":
        with conn.cursor() as cur:
            cur.execute("SELECT task, run_key, started_at, finished_at, ok, result FROM core.scheduler_runs "
                        "ORDER BY started_at DESC LIMIT 20")
            for r in cur.fetchall():
                print(r[0], r[1], r[2], r[4], json.dumps(r[5], ensure_ascii=False, default=str)[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
