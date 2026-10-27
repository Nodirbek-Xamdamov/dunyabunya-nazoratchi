"""W0-05 acceptance tests for genesis_queue. DESTRUCTIVE for core.jobs: only on a test database.

    apps\\w0-core\\.venv\\Scripts\\python.exe -E shared\\genesis_queue\\tests\\run_queue_tests.py [--db genesis_test]

Workers in the concurrency and "worker dies" tests are separate OS processes (own DB sessions).
Prints PASS/FAIL lines; exit 1 on any failure. Afterwards reload the W3 demo data:
    python -E shared/fixtures/load_fixtures.py
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import psycopg

from genesis_queue import JobQueue, Permanent, UnknownJobType, connect, run_worker

HERE = Path(__file__).resolve()
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""), flush=True)


def q(role: str, db: str, **kw) -> JobQueue:
    return JobQueue(connect(role, db), worker=kw.pop("worker", f"test-{role}"), **kw)


def sql(db: str, text: str, params=None, role: str = "w0_admin"):
    with connect(role, db) as c, c.cursor() as cur:
        cur.execute(text, params)
        return cur.fetchall() if cur.description else cur.rowcount


def spawn(*args: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-E", str(HERE), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)


# -- child processes --------------------------------------------------------------------------------------
def child_claim(db: str, worker: str, start_at: float) -> None:
    """Claim + complete analyze_episode jobs until none are left; print the ids taken."""
    qq = q("w2_analyst", db, worker=worker)
    while time.time() < start_at:
        time.sleep(0.005)
    taken = []
    while True:
        jobs = qq.claim(["analyze_episode"], limit=3)
        if not jobs:
            break
        for j in jobs:
            taken.append(j.id)
            qq.complete(j.id, {"by": worker})
    print(json.dumps(taken))


def child_die(db: str) -> None:
    """Claim the camera job with a 3 s lease, report, then 'work' forever (the parent kills us)."""
    qq = q("w4_camera", db, worker="dying-worker")
    jobs = qq.claim(["camera_day_batch"], lease_s=3)
    print(json.dumps([j.id for j in jobs]), flush=True)
    time.sleep(120)


# -- tests ------------------------------------------------------------------------------------------------
def main(db: str) -> int:
    if db == "genesis":
        raise SystemExit("refusing to run destructive queue tests on the production database 'genesis'")
    sql(db, "DELETE FROM core.jobs")
    # claim() obeys the GPU gate (W0-06). These tests are about the queue, so publish "all classes" for an hour.
    sql(db, "UPDATE core.gpu_gate_state SET allowed_classes = '{none,llm,vision}', valid_until = now() + interval '1 hour', "
            "phase = 'test', note = 'run_queue_tests'")

    # 1. idempotency
    w1 = q("w1_ingest", db)
    a = w1.enqueue("transcribe", {"media_id": 1}, key="media:1")
    b = w1.enqueue("transcribe", {"media_id": 1, "again": True}, key="media:1")
    n = sql(db, "SELECT count(*) FROM core.jobs WHERE idempotency_key = 'transcribe:media:1'")[0][0]
    check("idempotency: same key twice -> one row, second call created=False, same id",
          a.created and not b.created and a.id == b.id and n == 1, f"first={a} second={b} rows={n}")
    out: list = []
    ts = [threading.Thread(target=lambda: out.append(q("w1_ingest", db).enqueue("transcribe", key="media:2"))) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    n = sql(db, "SELECT count(*) FROM core.jobs WHERE idempotency_key = 'transcribe:media:2'")[0][0]
    check("idempotency under 8 concurrent enqueues -> 1 row, exactly one created=True",
          n == 1 and sum(r.created for r in out) == 1 and len({r.id for r in out}) == 1, f"rows={n} created={sum(r.created for r in out)}")

    # 2. two worker processes never take the same job
    w2 = q("w2_analyst", db)
    ids = [w2.enqueue("analyze_episode", {"episode_id": i}, key=f"ep:{i}").id for i in range(300)]
    start = time.time() + 2
    procs = [spawn("child-claim", db, f"proc-{k}", str(start)) for k in ("A", "B")]
    taken = [json.loads(p.communicate(timeout=120)[0]) for p in procs]
    both = taken[0] + taken[1]
    attempts = sql(db, "SELECT max(attempts), count(*) FILTER (WHERE status = 'done') FROM core.jobs WHERE type = 'analyze_episode'")[0]
    check("2 worker processes: no job taken twice, all 300 done, attempts=1",
          len(both) == 300 and len(set(both)) == 300 and set(both) == set(ids) and attempts == (1, 300),
          f"A={len(taken[0])} B={len(taken[1])} overlap={len(set(taken[0]) & set(taken[1]))} max_attempts={attempts[0]} done={attempts[1]}")

    # 3. worker dies -> job comes back after its lease
    adm = q("w0_admin", db)
    cam = adm.enqueue("camera_day_batch", {"day": "2026-10-26", "camera": "camera1"}, key="camera1:2026-10-26")
    p = spawn("child-die", db)
    got = json.loads(p.stdout.readline())
    p.kill()
    p.wait(timeout=10)
    w4 = q("w4_camera", db, worker="rescue-worker")
    early = w4.claim(["camera_day_batch"])
    time.sleep(3.5)
    late = w4.claim(["camera_day_batch"])
    row = sql(db, "SELECT attempts, last_error FROM core.jobs WHERE id = %s", (cam.id,))[0]
    ok_done = bool(late) and w4.complete(late[0].id, {"ok": True})
    check("worker killed: job not given out while leased, returned after lease, attempts=2, completed",
          got == [cam.id] and early == [] and late and late[0].id == cam.id and row[0] == 2 and ok_done,
          f"killed pid {p.pid} had {got}; before lease end {len(early)}; after: {[j.id for j in late]}; attempts={row[0]}; last_error='{row[1]}'")

    # 4. retries with exponential backoff, dead after 5, manual requeue (via CLI)
    wq = q("w2_analyst", db, backoff_base_s=0.2)
    job = wq.enqueue("analyze_episode", {"bad": True}, key="always-fails").id
    statuses, delays = [], []
    for i in range(5):
        deadline = time.time() + 10
        claimed = []
        while not claimed and time.time() < deadline:
            claimed = [j for j in wq.claim(["analyze_episode"]) if j.id == job]
            if not claimed:
                time.sleep(0.05)
        st = wq.fail(job, f"boom {i + 1}")
        statuses.append(st)
        if st == "failed":
            delays.append(round(sql(db, "SELECT extract(epoch FROM run_after - now()) FROM core.jobs WHERE id = %s", (job,))[0][0], 2))
    row = sql(db, "SELECT status, attempts, last_error FROM core.jobs WHERE id = %s", (job,))[0]
    check("5 failed attempts -> dead (attempts=5); backoff doubles",
          statuses == ["failed"] * 4 + ["dead"] and row[:2] == ("dead", 5) and all(b > a for a, b in zip(delays, delays[1:])),
          f"statuses={statuses} delays_s={delays} last_error='{row[2]}'")
    dead_cli = subprocess.run([sys.executable, "-E", "-m", "genesis_queue", "dead", "--db", db], capture_output=True, text=True)
    req_cli = subprocess.run([sys.executable, "-E", "-m", "genesis_queue", "requeue", "--db", db, "--id", str(job)],
                             capture_output=True, text=True)
    row = sql(db, "SELECT status, attempts FROM core.jobs WHERE id = %s", (job,))[0]
    again = wq.claim(["analyze_episode"])
    check("CLI: dead list shows it, requeue -> queued attempts=0, claimable again",
          str(job) in dead_cli.stdout and f"[{job}]" in req_cli.stdout and row == ("queued", 0) and [j.id for j in again] == [job],
          req_cli.stdout.strip())
    wq.complete(job)
    last = adm.enqueue("analyze_episode", key="one-shot", max_attempts=1).id
    q("w2_analyst", db, worker="w-lost").claim(["analyze_episode"], lease_s=1)
    time.sleep(1.5)
    nothing = q("w2_analyst", db, worker="w-next").claim(["analyze_episode"])
    st = sql(db, "SELECT status FROM core.jobs WHERE id = %s", (last,))[0][0]
    check("lease expired on the last attempt -> dead, not handed out again", nothing == [] and st == "dead", f"status={st}")

    # 5. priority and gpu_class filter
    for prio in (0, 5, 1):
        w2.enqueue("analyze_episode", {"p": prio}, key=f"prio:{prio}", priority=prio)
    order = [q("w2_analyst", db).claim(["analyze_episode"])[0].priority for _ in range(3)]
    check("claim order follows priority", order == [5, 1, 0], f"order={order}")
    adm.enqueue("camera_day_batch", key="camera1:2026-10-27")
    llm_only = w4.claim(["camera_day_batch"], gpu_classes=["llm"])
    vision = w4.claim(["camera_day_batch"], gpu_classes=["vision"])
    check("gpu_class filter: vision job not given to an llm claim", llm_only == [] and len(vision) == 1)
    w4.complete(vision[0].id)

    # 6. roles (CONTRACTS §2/§5) enforced by the database
    def denied(fn) -> str:
        try:
            fn()
            return "allowed"
        except psycopg.errors.InsufficientPrivilege:
            return "denied"
    check("w1 may not create camera_day_batch (RLS)", denied(lambda: w1.enqueue("camera_day_batch", key="x")) == "denied")
    check("w3 may not create build_excel_tg (W0 scheduler only)",
          denied(lambda: q("w3_dashboard", db).enqueue("build_excel_tg", key="x")) == "denied")
    check("w4 may not create any job type", denied(lambda: w4.enqueue("transcribe", key="x")) == "denied")
    w1.enqueue("transcribe", key="media:3")
    w4_sees = sql(db, "SELECT count(*) FROM core.jobs WHERE type = 'transcribe'", role="w4_camera")[0][0]
    w4_takes = w4.claim(["transcribe"])
    w1_takes = w1.claim(["transcribe"])
    w1_sees = sql(db, "SELECT count(*) FROM core.jobs WHERE type = 'transcribe'", role="w1_ingest")[0][0]
    w2_takes = q("w2_analyst", db).claim(["transcribe"], limit=10)
    check("transcribe: w4 sees 0 / claims 0, w1 (creator) sees but cannot claim, w2 claims",
          w4_sees == 0 and w4_takes == [] and w1_takes == [] and w1_sees == 3 and len(w2_takes) == 3,
          f"w4 sees {w4_sees}, w1 sees {w1_sees}, w2 took {len(w2_takes)}")
    cam2 = adm.enqueue("camera_day_batch", key="camera1:2026-10-28").id
    upd = sql(db, "UPDATE core.jobs SET status = 'done' WHERE id = %s", (cam2,), role="w2_analyst")
    st = sql(db, "SELECT status FROM core.jobs WHERE id = %s", (cam2,))[0][0]
    check("w2 cannot complete a camera job (0 rows updated)", upd == 0 and st == "queued", f"rows={upd} status={st}")
    d2 = adm.enqueue("daily_close", {"day": "2026-10-26"}, key="w2:2026-10-26", target_role="w2_analyst").id
    d3 = adm.enqueue("daily_close", {"day": "2026-10-26"}, key="w3:2026-10-26", target_role="w3_dashboard").id
    t3 = [j.id for j in q("w3_dashboard", db).claim(["daily_close"], limit=5)]
    t2 = [j.id for j in q("w2_analyst", db).claim(["daily_close"], limit=5)]
    check("daily_close target_role: w3 gets only its job, w2 only its job", t3 == [d3] and t2 == [d2], f"w3={t3} w2={t2}")
    try:
        w1.enqueue("no_such_type")
        unknown = "accepted"
    except UnknownJobType:
        unknown = "UnknownJobType"
    try:
        sql(db, "INSERT INTO core.jobs (type) VALUES ('no_such_type')")
        fk = "accepted"
    except psycopg.errors.ForeignKeyViolation:
        fk = "foreign key violation"
    check("unknown job type rejected (library and database)", unknown == "UnknownJobType" and fk == "foreign key violation",
          f"{unknown}, raw insert as w0_admin: {fk}")
    types = sql(db, "SELECT string_agg(type || ':' || gpu_class, ' ' ORDER BY type) FROM core.job_types")[0][0]
    check("all 8 CONTRACTS §2 job types registered (camera_day_batch included)", types.count(":") == 8, types)
    status_cli = subprocess.run([sys.executable, "-E", "-m", "genesis_queue", "status", "--db", db], capture_output=True, text=True)
    check("CLI status works", status_cli.returncode == 0 and "analyze_episode" in status_cli.stdout,
          f"{len(status_cli.stdout.splitlines())} lines")

    # 7. run_worker: heartbeats keep a job that runs longer than its lease; Permanent -> dead at once
    sql(db, "DELETE FROM core.jobs")
    for k, perm in ((1, False), (2, True), (3, False)):
        w2.enqueue("build_context", {"k": k, "permanent": perm}, key=f"ctx:{k}")

    def handler(job):
        if job.payload["permanent"]:
            raise Permanent("bad payload")
        time.sleep(3.5)  # longer than the 2 s lease: only heartbeats keep it ours
        return {"k": job.payload["k"]}

    n = run_worker(lambda: connect("w2_analyst", db), ["build_context"], handler, worker="loop-worker", lease_s=2,
                   poll_s=0.2, max_jobs=3)
    rows = sql(db, "SELECT payload->>'k', status, attempts, coalesce(result::text, '') FROM core.jobs ORDER BY id")
    check("run_worker: 2 long jobs done on 1st attempt (heartbeat kept the lease), Permanent -> dead",
          n == 3 and [(r[1], r[2]) for r in rows] == [("done", 1), ("dead", 1), ("done", 1)], f"{rows}")

    sql(db, "DELETE FROM core.jobs")
    sql(db, "UPDATE core.gpu_gate_state SET allowed_classes = '{none}', valid_until = now(), phase = 'init', note = NULL")
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "child-claim":
        child_claim(sys.argv[2], sys.argv[3], float(sys.argv[4]))
    elif len(sys.argv) > 1 and sys.argv[1] == "child-die":
        child_die(sys.argv[2])
    else:
        ap = argparse.ArgumentParser()
        ap.add_argument("--db", default="genesis_test")
        sys.exit(main(ap.parse_args().db))
