"""Job queue on core.jobs (CONTRACTS §2, W0-05).

Lifecycle: enqueue -> claim (FOR UPDATE SKIP LOCKED, lease) -> heartbeat* -> complete | fail.
fail: retry with exponential backoff (status `failed` until run_after), `dead` after max_attempts.
GPU jobs are handed out only while the W0 GPU gate allows their gpu_class (genesis_gpu, V007).
A worker that dies stops heart-beating; when its lease expires the next claim returns the job to the
queue (or marks it dead if it was the last attempt). Which role may create/execute which job type is
enforced in the database (row-level security over core.job_types), not only here.
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from genesis_common import redact


class UnknownJobType(ValueError):
    pass


@dataclass(frozen=True)
class Job:
    id: int
    type: str
    payload: dict
    attempts: int
    max_attempts: int
    gpu_class: str
    priority: int
    lease_until: datetime
    target_role: str | None
    idempotency_key: str | None


@dataclass(frozen=True)
class Enqueued:
    id: int
    created: bool  # False = idempotency key already existed, nothing was written


_JOB_COLS = "j.id, j.type, j.payload, j.attempts, j.max_attempts, j.gpu_class, j.priority, j.lease_until, j.target_role, j.idempotency_key"


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


class JobQueue:
    def __init__(self, conn: psycopg.Connection, worker: str | None = None,
                 backoff_base_s: float = 30.0, backoff_cap_s: float = 3600.0):
        self.conn = conn
        self.worker = worker or default_worker_id()
        self.backoff_base_s = backoff_base_s
        self.backoff_cap_s = backoff_cap_s

    # -- producers ---------------------------------------------------------------------------------
    def enqueue(self, type: str, payload: dict | None = None, *, key: str | None = None, priority: int = 0,
                run_after: datetime | None = None, gpu_class: str | None = None, target_role: str | None = None,
                max_attempts: int | None = None) -> Enqueued:
        """Add a job. With `key`, a second enqueue of the same type+key is silently ignored."""
        idem = f"{type}:{key}" if key is not None else None
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("SELECT gpu_class, max_attempts FROM core.job_types WHERE type = %s", (type,))
            row = cur.fetchone()
            if row is None:
                raise UnknownJobType(type)
            cur.execute(
                """INSERT INTO core.jobs (type, payload, priority, run_after, gpu_class, idempotency_key, target_role, max_attempts)
                   VALUES (%s, %s, %s, coalesce(%s, now()), %s, %s, %s, %s)
                   ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
                   RETURNING id""",
                (type, Jsonb(payload or {}), priority, run_after, gpu_class or row[0], idem, target_role,
                 max_attempts or row[1]))
            new = cur.fetchone()
            if new:
                return Enqueued(new[0], True)
            cur.execute("SELECT id FROM core.jobs WHERE idempotency_key = %s", (idem,))
            old = cur.fetchone()
            return Enqueued(old[0] if old else -1, False)

    # -- workers -----------------------------------------------------------------------------------
    def claim(self, types: Iterable[str], *, gpu_classes: Iterable[str] | None = None, limit: int = 1,
              lease_s: float | None = None, gated: bool = True) -> list[Job]:
        """Take up to `limit` ready jobs of `types` (highest priority first). Expired leases of these
        types are returned to the queue (or made dead) first, in the same transaction.
        gated: GPU jobs only of the classes the W0 GPU gate allows now (core.gpu_allowed_classes();
        CONTRACTS §3). Class 'none' is never gated. Only W0 tools pass gated=False."""
        types = list(types)
        gpus = list(gpu_classes) if gpu_classes is not None else None
        with self.conn.transaction(), self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """WITH exp AS (SELECT id FROM core.jobs WHERE status = 'running' AND lease_until < now()
                                AND type = ANY(%(types)s) FOR UPDATE SKIP LOCKED)
                   UPDATE core.jobs j SET
                       status = CASE WHEN j.attempts >= j.max_attempts THEN 'dead' ELSE 'failed' END,
                       last_error = 'lease expired: worker ' || coalesce(j.locked_by, '?') || ' lost',
                       finished_at = CASE WHEN j.attempts >= j.max_attempts THEN now() END,
                       run_after = now(), locked_by = NULL, locked_at = NULL, lease_until = NULL
                   FROM exp WHERE j.id = exp.id""", {"types": types})
            cur.execute(
                f"""WITH c AS (
                       SELECT j.id FROM core.jobs j
                       WHERE j.status IN ('queued', 'failed') AND j.run_after <= now() AND j.type = ANY(%(types)s)
                         AND (%(gpus)s::text[] IS NULL OR j.gpu_class = ANY(%(gpus)s))
                         AND (j.gpu_class = 'none' OR NOT %(gated)s OR j.gpu_class = ANY(core.gpu_allowed_classes()))
                       ORDER BY j.priority DESC, j.run_after, j.id
                       LIMIT %(n)s FOR UPDATE SKIP LOCKED)
                   UPDATE core.jobs j SET status = 'running', attempts = j.attempts + 1, locked_by = %(w)s,
                       locked_at = now(), heartbeat_at = now(),
                       lease_until = now() + make_interval(secs => coalesce(%(lease)s,
                           (SELECT t.lease_seconds FROM core.job_types t WHERE t.type = j.type)))
                   FROM c WHERE j.id = c.id
                   RETURNING {_JOB_COLS}""",
                {"types": types, "gpus": gpus, "n": limit, "w": self.worker, "lease": lease_s, "gated": gated})
            rows = cur.fetchall()
        jobs = [Job(**r) for r in rows]
        return sorted(jobs, key=lambda j: (-j.priority, j.id))

    def heartbeat(self, job_id: int, lease_s: float | None = None) -> bool:
        """Extend the lease. False = the job is no longer ours (lease expired and it was taken back)."""
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute(
                """UPDATE core.jobs j SET heartbeat_at = now(), lease_until = now() + make_interval(secs => coalesce(%s,
                       (SELECT t.lease_seconds FROM core.job_types t WHERE t.type = j.type)))
                   WHERE j.id = %s AND j.status = 'running' AND j.locked_by = %s RETURNING 1""",
                (lease_s, job_id, self.worker))
            return cur.fetchone() is not None

    def complete(self, job_id: int, result: Any = None) -> bool:
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute(
                """UPDATE core.jobs SET status = 'done', result = %s, finished_at = now(), locked_by = NULL,
                       locked_at = NULL, lease_until = NULL
                   WHERE id = %s AND status = 'running' AND locked_by = %s RETURNING 1""",
                (Jsonb(result) if result is not None else None, job_id, self.worker))
            return cur.fetchone() is not None

    def fail(self, job_id: int, error: str, *, retry: bool = True) -> str | None:
        """Record a failed attempt. Returns the new status: 'failed' (retry later) or 'dead'."""
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute(
                """UPDATE core.jobs j SET
                       status = CASE WHEN NOT %(retry)s OR j.attempts >= j.max_attempts THEN 'dead' ELSE 'failed' END,
                       run_after = CASE WHEN NOT %(retry)s OR j.attempts >= j.max_attempts THEN j.run_after
                                   ELSE now() + make_interval(secs => least(%(cap)s, %(base)s * power(2, j.attempts - 1))) END,
                       finished_at = CASE WHEN NOT %(retry)s OR j.attempts >= j.max_attempts THEN now() END,
                       last_error = left(%(err)s, 2000), locked_by = NULL, locked_at = NULL, lease_until = NULL
                   WHERE j.id = %(id)s AND j.status = 'running' AND j.locked_by = %(w)s RETURNING j.status""",
                {"retry": retry, "cap": self.backoff_cap_s, "base": self.backoff_base_s, "err": redact(error),
                 "id": job_id, "w": self.worker})
            row = cur.fetchone()
            return row[0] if row else None

    # -- operations ------------------------------------------------------------------------------------
    def requeue_dead(self, *, job_id: int | None = None, type: str | None = None) -> list[int]:
        """Put dead jobs back (attempts reset). Needs a role that may update them (w0_admin in the CLI)."""
        if job_id is None and type is None:
            raise ValueError("job_id or type is required")
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute(
                """UPDATE core.jobs SET status = 'queued', attempts = 0, run_after = now(), finished_at = NULL,
                       last_error = 'requeued after dead: ' || coalesce(last_error, '')
                   WHERE status = 'dead' AND (%s::bigint IS NULL OR id = %s) AND (%s::text IS NULL OR type = %s)
                   RETURNING id""", (job_id, job_id, type, type))
            return [r[0] for r in cur.fetchall()]

    def status(self) -> list[dict]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""SELECT type, status, count(*) AS jobs, min(run_after) AS oldest_run_after,
                                  max(attempts) AS max_attempts_seen
                           FROM core.jobs GROUP BY type, status ORDER BY type, status""")
            return cur.fetchall()

    def dead(self, limit: int = 50) -> list[dict]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""SELECT id, type, attempts, finished_at, last_error, idempotency_key, target_role
                           FROM core.jobs WHERE status = 'dead' ORDER BY finished_at DESC NULLS LAST, id DESC LIMIT %s""",
                        (limit,))
            return cur.fetchall()
