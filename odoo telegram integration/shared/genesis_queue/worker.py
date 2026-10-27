"""A small worker loop: claim one job, keep its lease alive from a heartbeat thread, run the handler,
complete or fail it. Raise `Permanent` from the handler to send the job straight to dead."""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Iterable

import psycopg

from .queue import Job, JobQueue

log = logging.getLogger("genesis_queue.worker")


class Permanent(Exception):
    """Retrying will not help (bad payload etc.): the job goes to dead at once."""


def run_worker(make_conn: Callable[[], psycopg.Connection], types: Iterable[str], handler: Callable[[Job], Any], *,
               worker: str | None = None, gpu_classes: Iterable[str] | None = None, poll_s: float = 5.0,
               lease_s: float | None = None, stop: threading.Event | None = None, max_jobs: int | None = None) -> int:
    """Process jobs until `stop` is set (or `max_jobs` done). Returns the number of jobs handled."""
    stop = stop or threading.Event()
    q = JobQueue(make_conn(), worker=worker)
    hb = JobQueue(make_conn(), worker=q.worker)  # own connection: heartbeats must not wait for the handler
    types = list(types)
    handled = 0
    while not stop.is_set() and (max_jobs is None or handled < max_jobs):
        jobs = q.claim(types, gpu_classes=gpu_classes, limit=1, lease_s=lease_s)
        if not jobs:
            stop.wait(poll_s)
            continue
        job = jobs[0]
        lease = (job.lease_until - _now(q)).total_seconds()
        done = threading.Event()
        lost = threading.Event()

        def beat() -> None:
            while not done.wait(max(1.0, lease / 3)):
                if not hb.heartbeat(job.id, lease_s):
                    lost.set()
                    return

        t = threading.Thread(target=beat, name=f"heartbeat-{job.id}", daemon=True)
        t.start()
        try:
            result = handler(job)
            done.set()
            if not q.complete(job.id, result):
                log.warning("job %s finished after its lease was lost; result discarded", job.id)
        except Permanent as e:
            done.set()
            q.fail(job.id, f"permanent: {e}", retry=False)
        except Exception as e:  # noqa: BLE001 - every handler error is recorded on the job
            done.set()
            status = q.fail(job.id, f"{type(e).__name__}: {e}")
            log.warning("job %s failed (%s): %s", job.id, status, e)
        t.join(timeout=5)
        handled += 1
    return handled


def _now(q: JobQueue):
    with q.conn.cursor() as cur:
        cur.execute("SELECT now()")
        return cur.fetchone()[0]
