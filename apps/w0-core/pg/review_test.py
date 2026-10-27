"""core.review_decide (V011, W3-03 [CONTRACT]): behaviour, audit and privileges on a throwaway database.

Real logins (w3_dashboard etc.), not SET ROLE, so audit_log.details.db_user is the dashboard login.
Covers: confirm / reject / correct, every refusal (bad decision, empty reviewer, correct without values, closed or
missing row) leaves no answer and no audit row, two dashboards answering one row at once (second waits and is refused),
W3 has no direct write on review_queue / audit_log, other roles cannot EXECUTE, api.v_review_queue shows the answer,
V011 down -> W3's old direct rights back, up -> gone again, schema after up->down->up identical.

    python -E review_test.py
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
import json

from pgtool import BIN, PG, pg_env, psql
from setup_db import ensure_database
import migrate

DB = "genesis_reviewtest"
W3 = "w3_dashboard"
WHO = "dashboard-local"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def q(sql: str, user: str = "w0_admin") -> str:
    return psql(sql, user=user, db=DB).stdout.strip()


def call(sql: str, user: str = W3) -> tuple[str, str]:
    """-> (sqlstate or 'ok', output or error text)"""
    r = psql("\\set VERBOSITY verbose\n" + sql, user=user, db=DB, check=False)
    if r.returncode == 0:
        return "ok", r.stdout.strip()
    m = re.search(r"ERROR:\s+([0-9A-Z]{5}):\s*(.*)", r.stderr)
    return (m.group(1), m.group(2)) if m else ("?", r.stderr.strip())


def decide(rid: int | str, decision: str | None, who: str | None = WHO, details: str | None = None,
           user: str = W3) -> tuple[str, str]:
    lit = lambda v: "NULL" if v is None else "'" + v.replace("'", "''") + "'"  # noqa: E731
    args = f"{rid}, {lit(decision)}, {lit(who)}" + ("" if details is None else f", {lit(details)}::jsonb")
    return call(f"SELECT core.review_decide({args});", user)


def audits() -> int:
    return int(q("SELECT count(*) FROM core.audit_log;"))


def schema_dump() -> str:
    cmd = [str(BIN / "pg_dump.exe"), "-w", "-s", "-h", PG["host"], "-p", str(PG["port"]), "-U", "postgres", "-d", DB]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=pg_env(), check=True).stdout
    return "\n".join(l for l in out.splitlines() if not l.startswith("\\restrict") and not l.startswith("\\unrestrict"))


def main() -> int:
    psql(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE);')
    ensure_database(DB)
    migrate.cmd_up(DB, None)
    dump_up = schema_dump()

    ids = [int(x) for x in q(
        "INSERT INTO core.review_queue (item_type, item_id, reason, confidence, details) VALUES "
        "('analysis', 101, 'low_confidence', 0.41, '{\"categories\": [\"delivery\"]}'), "
        "('analysis', 102, 'low_confidence', 0.38, '{}'), "
        "('relevance', 103, 'unsure', 0.5, '{\"relevance\": \"unsure\"}'), "
        "('camera_event', 104, 'direction_unclear', 0.3, '{}'), "
        "('analysis', 105, 'low_confidence', 0.2, '{}') RETURNING id;", user="w2_analyst").split()]
    a, b, c, d, e = ids

    # 1. confirm
    st, out = decide(a, "confirm")
    res = json.loads(out) if st == "ok" else {}
    row = q(f"SELECT status || '|' || human_decision || '|' || reviewed_by || '|' || (reviewed_at IS NOT NULL) "
            f"FROM core.review_queue WHERE id = {a};")
    au = json.loads(q(f"SELECT row_to_json(x) FROM (SELECT actor, action, object_type, object_id, details "
                      f"FROM core.audit_log WHERE object_id = '{a}') x;") or "{}")
    check("confirm: row resolved with dashboard-local", st == "ok" and row == f"resolved|confirm|{WHO}|true"
          and res.get("decision") == "confirm" and res.get("audit_id"), f"{st} {row} {out}")
    check("confirm: audit_log row (actor, action, object, decision, previous, db_user)",
          au.get("actor") == WHO and au.get("action") == "review_decide" and au.get("object_type") == "review_queue"
          and au["details"]["decision"] == "confirm" and au["details"]["item_id"] == 101
          and au["details"]["db_user"] == W3 and au["details"]["previous"]["reviewed_at"] is None, json.dumps(au))

    # 2. reject
    st, _ = decide(b, "reject", details='{"note": "xato tasnif"}')
    row = q(f"SELECT status || '|' || human_decision FROM core.review_queue WHERE id = {b};")
    check("reject: resolved, note kept in audit", st == "ok" and row == "resolved|reject"
          and q(f"SELECT details->'details'->>'note' FROM core.audit_log WHERE object_id = '{b}';") == "xato tasnif", row)

    # 3. correct
    fix = '{"categories": ["quality"], "sentiment": "negative", "note": "sifat haqida"}'
    st, _ = decide(c, "correct", details=fix)
    hc = q(f"SELECT details->'human_correction'->>'sentiment' || '|' || (details->>'relevance') "
           f"FROM core.review_queue WHERE id = {c};")
    check("correct: values in review_queue.details.human_correction, W2 details kept", st == "ok" and hc == "negative|unsure", hc)
    check("correct: values in audit_log", q(f"SELECT details->'details'->'categories'->>0 FROM core.audit_log "
                                            f"WHERE object_id = '{c}';") == "quality")

    # 4. refusals: nothing written
    before = audits()
    for name, (st, msg), want in [
        ("bad decision 'approve'", decide(d, "approve"), "22023"),
        ("NULL decision", decide(d, None), "22023"),
        ("empty reviewed_by", decide(d, "confirm", who="   "), "22023"),
        ("NULL reviewed_by", decide(d, "confirm", who=None), "22023"),
        ("reviewed_by > 200 chars", decide(d, "confirm", who="x" * 201), "22023"),
        ("correct without values", decide(d, "correct", details='{"note": "faqat izoh"}'), "22023"),
        ("details not an object", decide(d, "reject", details='[1]'), "22023"),
        ("already resolved row", decide(a, "reject"), "55000"),
        ("missing row", decide(999999, "confirm"), "P0002"),
    ]:
        check(f"refused: {name} -> {want}", st == want, f"{st} {msg[:90]}")
    row_a = q(f"SELECT human_decision FROM core.review_queue WHERE id = {a};")
    check("refusals wrote no audit row and changed nothing", audits() == before and row_a == "confirm"
          and q(f"SELECT status FROM core.review_queue WHERE id = {d};") == "open", f"audit {before}->{audits()}")

    # 5. two dashboards at once on one row: the second waits for the lock, then is refused
    sql_a = (f"BEGIN; SELECT core.review_decide({e}, 'confirm', 'dash-A'); SELECT pg_sleep(2); COMMIT;")
    cmd = [str(BIN / "psql.exe"), "-X", "-q", "-v", "ON_ERROR_STOP=1", "-w", "-h", PG["host"], "-p", str(PG["port"]),
           "-U", W3, "-d", DB, "-At", "-f", "-"]
    pa = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", env=pg_env())
    pa.stdin.write(sql_a)
    pa.stdin.close()
    time.sleep(0.7)
    t0 = time.monotonic()
    st, _ = decide(e, "reject", who="dash-B")
    waited = time.monotonic() - t0
    pa.wait(timeout=30)
    row = q(f"SELECT human_decision || '|' || reviewed_by || '|' || "
            f"(SELECT count(*) FROM core.audit_log WHERE object_id = '{e}') FROM core.review_queue WHERE id = {e};")
    check("concurrent answers: first wins, second waits and gets 55000, one audit row",
          pa.returncode == 0 and st == "55000" and row == "confirm|dash-A|1" and waited > 0.8,
          f"A={pa.returncode} B={st} waited={waited:.1f}s row={row}")

    # 6. privileges
    for name, sql in [("UPDATE review_queue.human_decision", f"UPDATE core.review_queue SET human_decision = 'x' WHERE id = {d};"),
                      ("UPDATE review_queue.reviewed_at", f"UPDATE core.review_queue SET reviewed_at = now() WHERE id = {d};"),
                      ("INSERT audit_log", "INSERT INTO core.audit_log (actor, action) VALUES ('w3', 'x');")]:
        st, _ = call(sql)
        check(f"W3 direct {name} denied", st == "42501", st)
    check("W3 still reads review_queue", call("SELECT count(*) FROM core.review_queue;")[0] == "ok")
    for role in ("w1_ingest", "w2_analyst", "w4_camera"):
        st, _ = decide(d, "confirm", user=role)
        check(f"{role} cannot EXECUTE review_decide", st == "42501", st)
    st, out = call(f"SELECT human_decision || '|' || reviewed_by || '|' || status FROM api.v_review_queue "
                   f"WHERE review_id = {c};")
    check("api.v_review_queue shows the answer to W3", st == "ok" and out == f"correct|{WHO}|resolved", out)
    st, _ = decide(d, "confirm", user="w0_admin")
    check("w0_admin may EXECUTE (actor from parameter)", st == "ok")

    # 7. down / up
    priv = ("SELECT has_column_privilege('w3_dashboard', 'core.review_queue', 'human_decision', 'UPDATE') || '|' || "
            "has_table_privilege('w3_dashboard', 'core.audit_log', 'INSERT') || '|' || "
            "(to_regprocedure('core.review_decide(bigint, text, text, jsonb)') IS NOT NULL);")
    after_up = q(priv)
    migrate.cmd_down(DB, 10, None)
    after_down = q(priv)
    migrate.cmd_up(DB, None)
    again = q(priv)
    check("V011 down: function gone, W3 direct rights back (pre-V011 map)", after_down == "true|true|false", after_down)
    check("V011 up: function back, W3 direct rights gone", after_up == again == "false|false|true", f"{after_up} / {again}")
    check("schema identical after up -> down -> up", schema_dump() == dump_up)

    psql(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE);')
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""))
    bad = sum(not ok for _, ok, _ in results)
    print(f"{len(results) - bad}/{len(results)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
