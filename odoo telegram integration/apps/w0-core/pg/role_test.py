"""Negative/positive privilege tests on the real schema v1 (W0-03, extended in W0-04).

Builds a throwaway database with all migrations (same grants as production via migrate.py), then
tries every operation as every role on every core table, api view and core function, plus DDL.
The expected matrix below is written out by hand from CONTRACTS.md §5 and SCHEMA.md §3
(not derived from roles.py), so a wrong grant shows up as a mismatch.
A permitted statement may still fail for a non-privilege reason (NOT NULL, FK, no partition);
that counts as "allowed": only SQLSTATE 42501 (insufficient_privilege) counts as "denied".
"""
from __future__ import annotations

import sys

from pgtool import APP_ROLES, PG, psql
from setup_db import ensure_database
import migrate

TEST_DB = "genesis_roletest"
MIGRATOR, ADMIN = "genesis_migrator", "w0_admin"

RW = {
    "w1_ingest": {"sources", "source_members", "messages", "media", "jobs", "heartbeats"},
    "w2_analyst": {"transcripts", "episodes", "episode_messages", "analyses", "chat_context", "daily_stats",
                   "daily_summaries", "review_queue", "response_pairs", "jobs", "heartbeats"},
    "w3_dashboard": {"report_deliveries", "dashboard_users", "jobs", "heartbeats"},
    "w4_camera": {"video_segments", "video_gaps", "camera_events", "anon_persons", "presence_sessions",
                  "daily_presence", "camera_event_frames", "jobs", "heartbeats"},
}
RO = {
    "w1_ingest": {"people", "config", "job_types"},
    "w2_analyst": {"messages", "media", "people", "job_types", "categories"},
    "w3_dashboard": {"job_types"},
    "w4_camera": {"cameras", "config", "job_types"},
}
# core.jobs has row-level security (V006, CONTRACTS §2): a role may INSERT only job types it creates.
JOB_CREATES = {"w1_ingest": "transcribe", "w2_analyst": "analyze_episode", MIGRATOR: "daily_close", ADMIN: "daily_close"}
OPS = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"]
FUNCS = {  # call -> roles allowed besides the owner
    "core.tashkent_day(now())": {ADMIN, "w1_ingest", "w2_analyst", "w3_dashboard", "w4_camera"},
    "core.tg_link(-1001234567890, NULL, 1)": {ADMIN, "w1_ingest", "w2_analyst", "w3_dashboard", "w4_camera"},
    "core.ensure_month_partitions(0)": {ADMIN},
    "core.purge_unrelated_text(now())": {ADMIN},
    "core.import_manifest_record('{\"v\":1,\"type\":\"none\"}'::jsonb, NULL)": {ADMIN, "w4_camera"},
    "core.create_month_partition('messages', '2031-01-01')": set(),
    "core.gpu_allowed_classes()": {ADMIN, "w1_ingest", "w2_analyst", "w3_dashboard", "w4_camera"},
    "core.gpu_device_for('vision')": {ADMIN, "w1_ingest", "w2_analyst", "w3_dashboard", "w4_camera"},
    # V011: W3's only write path for review answers (behaviour and audit: review_test.py)
    "core.review_decide(0, 'confirm', 'dashboard-local')": {ADMIN, "w3_dashboard"},
}


def expected(role: str, table: str, op: str) -> bool:
    if role == MIGRATOR:
        return True
    if role == ADMIN:
        return op == "SELECT" if table == "schema_migrations" else True
    if role == "w3_dashboard" and table == "audit_log":
        return False  # V011: written only through core.review_decide
    if role == "w3_dashboard" and table == "review_queue":
        return op == "SELECT"  # V011: answers only through core.review_decide, columns tested separately
    if role == "w3_dashboard" and table == "alerts":
        # the generic UPDATE probe writes the last column, notified_by, which W3 may set; other columns tested separately
        return op in ("SELECT", "UPDATE")
    if op == "SELECT":
        return table in RW[role] or table in RO[role]
    if op == "INSERT" and table == "jobs":
        return role in JOB_CREATES  # w3/w4 execute jobs but create none
    if op in ("INSERT", "UPDATE"):
        return table in RW[role]
    return False  # DELETE, TRUNCATE: only W0


def probe(role: str, label: str, sql: str) -> str:
    return (f"SET ROLE {role};\nDO $t$ BEGIN {sql}; RAISE NOTICE 'RES|{role}|{label}|ok'; "
            f"EXCEPTION WHEN insufficient_privilege THEN RAISE NOTICE 'RES|{role}|{label}|denied'; "
            f"WHEN OTHERS THEN RAISE NOTICE 'RES|{role}|{label}|ok %', SQLSTATE; END $t$;\nRESET ROLE;")


def build_db() -> tuple[list[str], dict[str, str], list[str]]:
    psql(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE);')
    ensure_database(TEST_DB)
    migrate.cmd_up(TEST_DB, None)
    tables = psql("SELECT relname FROM pg_class WHERE relnamespace = 'core'::regnamespace AND relkind IN ('r','p') "
                  "AND NOT relispartition ORDER BY 1;", db=TEST_DB).stdout.split()
    upd = {}
    for t in tables:
        upd[t] = psql(f"SELECT attname FROM pg_attribute WHERE attrelid = 'core.{t}'::regclass AND attnum > 0 "
                      f"AND NOT attisdropped AND attgenerated = '' AND attidentity = '' ORDER BY attnum DESC LIMIT 1;",
                      db=TEST_DB).stdout.strip()
    views = psql("SELECT viewname FROM pg_views WHERE schemaname = 'api' ORDER BY 1;", db=TEST_DB).stdout.split()
    return tables, upd, views


def stmt(op: str, table: str, col: str, role: str = "") -> str:
    t = f"core.{table}"
    if op == "INSERT" and table == "jobs":
        return f"INSERT INTO core.jobs (type) VALUES ('{JOB_CREATES.get(role, 'transcribe')}')"
    return {
        "SELECT": f"PERFORM 1 FROM {t} LIMIT 1",
        "INSERT": f"INSERT INTO {t} DEFAULT VALUES",
        "UPDATE": f"UPDATE {t} SET {col} = NULL WHERE false",
        "DELETE": f"DELETE FROM {t} WHERE false",
        "TRUNCATE": f"TRUNCATE {t}",
    }[op]


def main() -> int:
    tables, upd, views = build_db()
    roles = [r for r in APP_ROLES]  # migrator, admin, w1..w4
    checks: dict[tuple[str, str], bool] = {}
    script = []

    def add(role: str, label: str, want: bool, sql: str) -> None:
        checks[(role, label)] = want
        script.append(probe(role, label, sql))

    for role in roles:
        for t in tables:
            for op in OPS:
                if op == "TRUNCATE" and expected(role, t, op):
                    continue  # would empty the test db for later probes; the privilege is covered by DELETE
                add(role, f"{t}:{op}", expected(role, t, op), stmt(op, t, upd[t], role))
        for v in views:
            add(role, f"api.{v}:SELECT", role in (MIGRATOR, ADMIN, "w3_dashboard"), f"PERFORM 1 FROM api.{v} LIMIT 1")
        for call, allowed in FUNCS.items():
            add(role, f"EXECUTE {call.split('(')[0]}", role == MIGRATOR or role in allowed, f"PERFORM {call}")
        add(role, "core:CREATE", role == MIGRATOR, f"CREATE TABLE core.zz_{role} (i int)")
        add(role, "public:CREATE", role == MIGRATOR, f"CREATE TABLE public.zz_{role} (i int)")
        add(role, "api:CREATE", role == MIGRATOR, f"CREATE VIEW api.zz_{role} AS SELECT 1 AS x")
    for col, ok in (("human_decision", False), ("reviewed_by", False), ("reviewed_at", False), ("reason", False),
                    ("item_id", False)):
        value = "now()" if col == "reviewed_at" else ("1" if col == "item_id" else "'y'")
        add("w3_dashboard", f"review_queue.{col}:UPDATE", ok,
            f"UPDATE core.review_queue SET {col} = {value} WHERE false")
    for col, ok in (("notified_at", True), ("notified_by", True), ("resolved_at", False), ("message", False)):
        value = "now()" if col.endswith("_at") else "'y'"
        add("w3_dashboard", f"alerts.{col}:UPDATE", ok, f"UPDATE core.alerts SET {col} = {value} WHERE false")

    out = psql("\n".join(script), db=TEST_DB, check=False)
    got: dict[tuple[str, str], str] = {}
    for line in out.stderr.splitlines():
        if "RES|" in line:
            role, label, result = line.split("RES|", 1)[1].split("|")
            got[(role, label)] = result.strip()

    bad, allowed, denied = [], 0, 0
    for key, want in checks.items():
        res = got.get(key, "missing")
        ok = res.startswith("ok") if want else res == "denied"
        if not ok:
            bad.append(f"MISMATCH {key[0]} {key[1]}: expected {'allowed' if want else 'denied'}, got {res}")
        elif want:
            allowed += 1
        else:
            denied += 1

    logins = []
    for role in roles:
        r = psql("SELECT current_user;", user=role, db=PG["database"], check=False)
        logins.append((f"login {role}@genesis", r.returncode == 0 and r.stdout.strip() == role))
    r = psql("SELECT 1;", user="w1_ingest", db=PG["database"], password="wrong-password", check=False)
    logins.append(("wrong password refused", r.returncode != 0 and "password authentication failed" in r.stderr))
    r = psql("SELECT 1;", user="w1_ingest", db="postgres", check=False)
    logins.append(("w1_ingest cannot connect to db postgres", r.returncode != 0 and "permission denied" in r.stderr))

    psql(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE);')

    print(f"objects: {len(tables)} core tables, {len(views)} api views, {len(FUNCS)} functions, 3 DDL targets, "
          f"{len(roles)} roles")
    print(f"privilege checks: {len(checks)} total, {allowed} allowed as expected, {denied} denied as expected, "
          f"{len(bad)} mismatches")
    for line in bad:
        print(line)
    for name, ok in logins:
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    return 1 if bad or not all(ok for _, ok in logins) else 0


if __name__ == "__main__":
    sys.exit(main())
