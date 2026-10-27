"""Queue CLI (connects as w0_admin by default).

    python -m genesis_queue status  [--db genesis]
    python -m genesis_queue dead    [--db genesis] [--limit 50]
    python -m genesis_queue requeue [--db genesis] (--id N | --type T)
"""
from __future__ import annotations

import argparse
import sys

from .db import connect
from .queue import JobQueue


def table(rows: list[dict]) -> str:
    if not rows:
        return "(bo'sh)"
    cols = list(rows[0])
    text = [[str(r[c]) if r[c] is not None else "" for c in cols] for r in rows]
    width = [max(len(c), *(len(t[i][:80]) for t in text)) for i, c in enumerate(cols)]
    line = lambda vals: "  ".join(v[:80].ljust(w) for v, w in zip(vals, width))  # noqa: E731
    return "\n".join([line(cols), line(["-" * w for w in width])] + [line(t) for t in text])


def main() -> int:
    ap = argparse.ArgumentParser(prog="genesis_queue")
    ap.add_argument("command", choices=["status", "dead", "requeue"])
    ap.add_argument("--db")
    ap.add_argument("--role", default="w0_admin")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--id", type=int)
    ap.add_argument("--type")
    a = ap.parse_args()
    q = JobQueue(connect(a.role, a.db), worker="cli")
    if a.command == "status":
        print(table(q.status()))
    elif a.command == "dead":
        print(table(q.dead(a.limit)))
    else:
        if a.id is None and a.type is None:
            ap.error("requeue needs --id or --type")
        ids = q.requeue_dead(job_id=a.id, type=a.type)
        print(f"requeued {len(ids)}: {ids}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
