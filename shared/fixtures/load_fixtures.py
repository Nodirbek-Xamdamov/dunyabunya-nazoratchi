"""One command: create (if needed) the test database, apply the same migrations, load the W3 demo data.

    python -E shared/fixtures/load_fixtures.py [--db genesis_test] [--anchor YYYY-MM-DD]

Idempotent: every run empties the data tables (TRUNCATE ... RESTART IDENTITY) and loads the same rows
in one transaction, so a reload on the same anchor day gives identical data (ids included).
Refuses the production database `genesis`. Runs as genesis_migrator; secrets via secrets/pgpass.conf.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "w0-core" / "pg"))  # W0 tooling: pgtool, setup_db, migrate

from pgtool import psql  # noqa: E402
from setup_db import ensure_database  # noqa: E402
import migrate  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from w3_demo import build_sql  # noqa: E402

KEEP = {"config", "cameras", "schema_migrations", "partitioned_tables", "archive_log", "job_types", "gpu_gate_state", "categories"}


def fingerprint(db: str) -> str:
    tables = psql("SELECT relname FROM pg_class WHERE relnamespace = 'core'::regnamespace AND relkind IN ('r','p') "
                  "AND NOT relispartition AND relname <> 'schema_migrations' ORDER BY 1;", db=db).stdout.split()
    h = hashlib.sha256()
    for t in tables:
        cols = psql(f"SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum) FROM pg_attribute WHERE attrelid = 'core.{t}'::regclass "
                    f"AND attnum > 0 AND NOT attisdropped AND attname NOT IN ('created_at', 'updated_at', 'ingested_at', 'imported_at', "
                    f"'computed_at', 'updated_by');", db=db).stdout.strip()
        h.update(psql(f"SELECT md5(coalesce(string_agg(r::text, '|' ORDER BY r::text), '')) FROM (SELECT {cols} FROM core.{t}) r;",
                      db=db).stdout.encode())
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="genesis_test")
    ap.add_argument("--anchor", help="second (last) demo day, default: today in Asia/Tashkent")
    a = ap.parse_args()
    if a.db == "genesis":
        raise SystemExit("refusing to load fixtures into the production database 'genesis'")
    anchor = date.fromisoformat(a.anchor) if a.anchor else datetime.now(timezone(timedelta(hours=5))).date()

    if not psql(f"SELECT 1 FROM pg_database WHERE datname = '{a.db}';").stdout.strip():
        ensure_database(a.db)
        print(f"created database {a.db}")
    migrate.cmd_up(a.db, None)  # same migrations and grants as production

    data_tables = [t for t in psql("SELECT relname FROM pg_class WHERE relnamespace = 'core'::regnamespace AND relkind IN ('r','p') "
                                   "AND NOT relispartition ORDER BY 1;", db=a.db).stdout.split() if t not in KEEP]
    sql = (f"TRUNCATE {', '.join('core.' + t for t in data_tables)} RESTART IDENTITY CASCADE;\n"
           + build_sql(anchor) + "SELECT core.ensure_month_partitions();\n")
    psql(sql, user="genesis_migrator", db=a.db, single_transaction=True)
    counts = psql("SELECT string_agg(t || '=' || n, ' ' ORDER BY t) FROM (VALUES "
                  + ", ".join(f"('{t}', (SELECT count(*) FROM core.{t}))" for t in
                              ["sources", "messages", "episodes", "analyses", "response_pairs", "review_queue", "daily_stats",
                               "anon_persons", "camera_events", "camera_event_frames", "presence_sessions", "daily_presence", "video_segments"])
                  + ") v(t, n);", db=a.db).stdout.strip()
    print(f"loaded fixtures into {a.db}, days {anchor - timedelta(days=1)} .. {anchor}")
    print(counts)
    print("fingerprint", fingerprint(a.db))
    return 0


if __name__ == "__main__":
    sys.exit(main())
