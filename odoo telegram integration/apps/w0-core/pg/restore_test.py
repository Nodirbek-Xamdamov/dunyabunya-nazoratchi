"""Quick restore test (W0-03): restore the newest dump into a temporary database, compare, drop it.

    python restore_test.py            # compares schemas, tables and row counts with the live db
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from pgtool import BIN, PG, pg_env, psql

BACKUP_DIR = Path(r"F:\genesis\backup")
TEMP_DB = "genesis_restore_test"

INVENTORY_SQL = """
SELECT n.nspname || '.' || c.relname || ' ' || c.relkind::text
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') AND c.relkind IN ('r', 'v', 'p', 'S')
ORDER BY 1;
"""


def row_counts(db: str) -> dict[str, str]:
    tables = psql("SELECT quote_ident(schemaname) || '.' || quote_ident(relname) FROM pg_stat_user_tables "
                  "ORDER BY 1;", db=db).stdout.split()
    out = {}
    for t in tables:
        out[t] = psql(f"SELECT count(*) || ' ' || coalesce(md5(string_agg(x::text, ',' ORDER BY x::text)), '-') "
                      f"FROM {t} x;", db=db).stdout.strip()
    return out


def main() -> int:
    dumps = sorted(BACKUP_DIR.glob("genesis_*.dump"))
    if not dumps:
        print("no dump found in", BACKUP_DIR)
        return 1
    dump = dumps[-1]
    psql(f'DROP DATABASE IF EXISTS "{TEMP_DB}" WITH (FORCE);')
    psql(f'CREATE DATABASE "{TEMP_DB}" OWNER w0_admin TEMPLATE template0;')
    t0 = time.monotonic()
    res = subprocess.run([str(BIN / "pg_restore.exe"), "-h", PG["host"], "-p", str(PG["port"]), "-U", "postgres",
                          "-w", "-d", TEMP_DB, "--exit-on-error", str(dump)],
                         capture_output=True, text=True, env=pg_env())
    seconds = time.monotonic() - t0
    try:
        if res.returncode != 0:
            print("pg_restore failed:", res.stderr.strip())
            return 1
        live_inv = psql(INVENTORY_SQL, db=PG["database"]).stdout.split("\n")
        rest_inv = psql(INVENTORY_SQL, db=TEMP_DB).stdout.split("\n")
        live_rows, rest_rows = row_counts(PG["database"]), row_counts(TEMP_DB)
        same = live_inv == rest_inv and live_rows == rest_rows
        print(f"dump: {dump.name} ({dump.stat().st_size} bytes), restore {seconds:.1f} s")
        print(f"objects live={len([x for x in live_inv if x])} restored={len([x for x in rest_inv if x])}")
        for t, v in rest_rows.items():
            print(f"  {t}: restored {v} | live {live_rows.get(t)}")
        print("RESULT:", "identical" if same else "DIFFERENT")
        return 0 if same else 1
    finally:
        psql(f'DROP DATABASE IF EXISTS "{TEMP_DB}" WITH (FORCE);')
        gone = not psql(f"SELECT 1 FROM pg_database WHERE datname = '{TEMP_DB}';").stdout.strip()
        print("temp db dropped:", gone)


if __name__ == "__main__":
    sys.exit(main())
