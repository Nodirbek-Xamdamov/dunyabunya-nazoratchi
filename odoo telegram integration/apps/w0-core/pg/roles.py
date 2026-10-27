"""DB roles and privileges (CONTRACTS.md §5 + SCHEMA.md §3 extensions). Applied by migrate.py after
every migration, so the database always matches this map exactly.

Layout: schema `core` = tables and functions, schema `api` = views (CONTRACTS §6).
Owner of everything: genesis_migrator (DDL only through migrations).
"Writes" = SELECT + INSERT + UPDATE. DELETE/TRUNCATE only for w0_admin (retention, archive).
"""
from __future__ import annotations

CORE = "core"
API = "api"
MIGRATOR = "genesis_migrator"
ADMIN = "w0_admin"
W3 = "w3_dashboard"

WRITES: dict[str, list[str]] = {
    "w1_ingest": ["sources", "source_members", "messages", "media", "jobs", "heartbeats"],
    "w2_analyst": ["transcripts", "episodes", "episode_messages", "analyses", "chat_context", "daily_stats",
                   "daily_summaries", "review_queue", "response_pairs", "jobs", "heartbeats"],
    "w3_dashboard": ["report_deliveries", "dashboard_users", "jobs", "heartbeats"],
    "w4_camera": ["video_segments", "video_gaps", "camera_events", "anon_persons", "presence_sessions",
                  "daily_presence", "camera_event_frames", "jobs", "heartbeats"],
}
READS: dict[str, list[str]] = {
    "w1_ingest": ["people", "config", "job_types"],
    "w2_analyst": ["messages", "media", "people", "job_types", "categories"],
    "w3_dashboard": ["job_types"],
    "w4_camera": ["cameras", "config", "job_types"],
}
# core.jobs additionally has row-level security (V006): a role inserts only the job types it creates
# and sees/updates only the types it executes (core.job_types, CONTRACTS §2).
# W3 reads everything else through api views; appends to audit_log; answers review_queue.
W3_APPEND = ["audit_log"]
W3_REVIEW_TABLE = "review_queue"
W3_REVIEW_COLUMNS = ["human_decision", "reviewed_by", "reviewed_at"]
# V011 (W3-03 [CONTRACT]): once core.review_decide exists it is W3's only write path for answers and audit;
# W3 then keeps SELECT on review_queue and loses the direct column UPDATE and the audit_log INSERT above.
REVIEW_FN = "core.review_decide(bigint, text, text, jsonb)"
# W0-08: the W3-07 reporter marks alerts it has sent (reads them through api.v_alerts)
W3_ALERT_TABLE = "alerts"
W3_ALERT_COLUMNS = ["notified_at", "notified_by"]
ADMIN_READ_ONLY = ["schema_migrations"]

SEARCH_PATH = {
    MIGRATOR: "core, api",
    ADMIN: "core, api",
    "w1_ingest": "core",
    "w2_analyst": "core",
    W3: "api, core",
    "w4_camera": "core",
}
APP_ROLES = [r for r in SEARCH_PATH if r not in (MIGRATOR, ADMIN)]

# function signature (schema.name(arg types)) -> roles allowed to EXECUTE (owner always can).
# Pure helpers are open to every role: views and generated columns call them as the querying user.
FUNCTIONS: dict[str, list[str]] = {
    "core.tashkent_day(timestamp with time zone)": [ADMIN, *APP_ROLES],
    "core.tg_link(bigint, text, bigint)": [ADMIN, *APP_ROLES],
    "core.ensure_month_partitions(integer)": [ADMIN],
    "core.purge_unrelated_text(timestamp with time zone)": [ADMIN],
    "core.import_manifest_record(jsonb, text)": ["w4_camera", ADMIN],
    "core.gpu_allowed_classes()": [ADMIN, *APP_ROLES],
    "core.gpu_device_for(text)": [ADMIN, *APP_ROLES],
    REVIEW_FN: [W3, ADMIN],
}


def q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def roles_sql(verifiers: dict[str, str]) -> str:
    """CREATE/ALTER LOGIN roles. `verifiers` are SCRAM strings, never plain passwords."""
    out = []
    for role, verifier in verifiers.items():
        attrs = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 30"
        out.append(f"""DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN CREATE ROLE {q(role)}; END IF;
END $$;
ALTER ROLE {q(role)} {attrs} PASSWORD '{verifier}';""")
    return "\n".join(out)


def database_sql(db: str) -> str:
    """Run in the target database as superuser: owner, schemas, schema rights, default rights."""
    everyone = ", ".join(map(q, SEARCH_PATH))
    lines = [
        f"ALTER DATABASE {q(db)} OWNER TO {MIGRATOR};",
        f"REVOKE ALL ON DATABASE {q(db)} FROM PUBLIC;",
        f"GRANT CONNECT, TEMPORARY ON DATABASE {q(db)} TO {everyone};",
        "REVOKE ALL ON SCHEMA public FROM PUBLIC;",
        f"CREATE SCHEMA IF NOT EXISTS {CORE} AUTHORIZATION {MIGRATOR};",
        f"CREATE SCHEMA IF NOT EXISTS {API} AUTHORIZATION {MIGRATOR};",
        f"ALTER SCHEMA {CORE} OWNER TO {MIGRATOR};",
        f"ALTER SCHEMA {API} OWNER TO {MIGRATOR};",
        f"REVOKE ALL ON SCHEMA {CORE}, {API} FROM PUBLIC;",
        f"GRANT USAGE ON SCHEMA {CORE} TO {', '.join(map(q, [ADMIN, *APP_ROLES]))};",
        f"GRANT USAGE ON SCHEMA {API} TO {q(W3)}, {q(ADMIN)};",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR} IN SCHEMA {API} GRANT SELECT ON TABLES TO {q(W3)}, {q(ADMIN)};",
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {MIGRATOR} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;",
        "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;",
    ]
    for role, path in SEARCH_PATH.items():
        lines.append(f"ALTER ROLE {q(role)} IN DATABASE {q(db)} SET search_path = {path};")
    return "\n".join(lines)


def grants_sql(tables: set[str], functions: set[str]) -> str:
    """Exact table/function privileges, run by the owner (migrator) after each migration.
    `tables`: existing core tables (partitioned parents and plain tables, not partitions).
    `functions`: existing core function signatures as regprocedure text."""
    everyone = ", ".join(map(q, [ADMIN, *APP_ROLES]))
    lines = [f"REVOKE ALL ON ALL TABLES IN SCHEMA {CORE} FROM PUBLIC, {everyone};",
             f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {CORE} FROM PUBLIC, {everyone};",
             f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {CORE} FROM PUBLIC, {everyone};"]
    for t in sorted(tables):
        if t in ADMIN_READ_ONLY:
            lines.append(f"GRANT SELECT ON {CORE}.{q(t)} TO {q(ADMIN)};")
        else:
            lines.append(f"GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON {CORE}.{q(t)} TO {q(ADMIN)};")
    for role, ts in WRITES.items():
        for t in ts:
            if t in tables:
                lines.append(f"GRANT SELECT, INSERT, UPDATE ON {CORE}.{q(t)} TO {q(role)};")
    for role, ts in READS.items():
        for t in ts:
            if t in tables:
                lines.append(f"GRANT SELECT ON {CORE}.{q(t)} TO {q(role)};")
    review_fn = REVIEW_FN in functions
    for t in W3_APPEND:
        if t in tables and not review_fn:
            lines.append(f"GRANT INSERT ON {CORE}.{q(t)} TO {q(W3)};")
    if W3_REVIEW_TABLE in tables:
        if review_fn:
            lines.append(f"GRANT SELECT ON {CORE}.{q(W3_REVIEW_TABLE)} TO {q(W3)};")
        else:
            cols = ", ".join(map(q, W3_REVIEW_COLUMNS))
            lines.append(f"GRANT SELECT, UPDATE ({cols}) ON {CORE}.{q(W3_REVIEW_TABLE)} TO {q(W3)};")
    if W3_ALERT_TABLE in tables:
        cols = ", ".join(map(q, W3_ALERT_COLUMNS))
        lines.append(f"GRANT SELECT, UPDATE ({cols}) ON {CORE}.{q(W3_ALERT_TABLE)} TO {q(W3)};")
    for sig, roles in FUNCTIONS.items():
        if sig in functions:
            lines.append(f"GRANT EXECUTE ON FUNCTION {sig} TO {', '.join(map(q, roles))};")
    lines.append(f"GRANT SELECT ON ALL TABLES IN SCHEMA {API} TO {q(W3)}, {q(ADMIN)};")
    return "\n".join(lines)
