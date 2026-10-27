"""Versioned SQL migrations for the Genesis database (W0-04).

    python -E migrate.py status [--db genesis]
    python -E migrate.py up     [--db genesis] [--to N]
    python -E migrate.py down   [--db genesis] (--to N | --steps K)

Files: apps/w0-core/migrations/V<NNN>__<name>.up.sql and .down.sql. Each step runs in one transaction
as genesis_migrator (owner of all objects), records itself in core.schema_migrations, and after any
change roles.grants_sql() re-applies the exact privilege map. Applied files must not change (checksum).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from pgtool import PG, psql
from roles import MIGRATOR, grants_sql

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
LOCK_KEY = 74_200_001  # advisory lock: one migrator at a time

BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS core.schema_migrations (
    version      integer PRIMARY KEY,
    name         text NOT NULL,
    checksum     text NOT NULL,
    applied_at   timestamptz NOT NULL DEFAULT now(),
    applied_by   text NOT NULL DEFAULT current_user,
    duration_ms  integer
);
COMMENT ON TABLE core.schema_migrations IS 'Qo''llangan migratsiyalar (migrate.py yozadi)';
"""


@dataclass
class Migration:
    version: int
    name: str
    up: Path
    down: Path

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.up.read_bytes()).hexdigest()


def discover() -> list[Migration]:
    found = []
    for up in sorted(MIGRATIONS.glob("V*__*.up.sql")):
        m = re.fullmatch(r"V(\d{3})__(.+)\.up\.sql", up.name)
        if not m:
            raise SystemExit(f"bad migration file name: {up.name}")
        down = up.with_name(up.name.replace(".up.sql", ".down.sql"))
        if not down.is_file():
            raise SystemExit(f"missing down file for {up.name}")
        found.append(Migration(int(m.group(1)), m.group(2), up, down))
    versions = [m.version for m in found]
    if versions != list(range(1, len(found) + 1)):
        raise SystemExit(f"migration versions must be 1..N without gaps: {versions}")
    return found


def run(sql: str, db: str) -> str:
    return psql(sql, user=MIGRATOR, db=db, single_transaction=True).stdout


def applied(db: str) -> dict[int, str]:
    run(BOOTSTRAP, db)
    rows = run("SELECT version || '|' || checksum FROM core.schema_migrations ORDER BY version;", db)
    return {int(v): c for v, c in (r.split("|") for r in rows.split() if r)}


def apply_grants(db: str) -> None:
    tables = set(run("SELECT relname FROM pg_class WHERE relnamespace = 'core'::regnamespace "
                     "AND relkind IN ('r', 'p') AND NOT relispartition;", db).split())
    funcs = set(run("SELECT format('%I.%I(%s)', n.nspname, p.proname, oidvectortypes(p.proargtypes)) "
                    "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'core';",
                    db).splitlines())
    run(grants_sql(tables, funcs), db)


def check_checksums(migs: list[Migration], done: dict[int, str]) -> None:
    for m in migs:
        if m.version in done and done[m.version] != m.checksum:
            raise SystemExit(f"V{m.version:03d} {m.name}: file changed after it was applied (checksum mismatch)")


def cmd_status(db: str) -> None:
    migs, done = discover(), applied(db)
    for m in migs:
        print(f"V{m.version:03d} {m.name:<14} {'applied' if m.version in done else 'pending'}")


def cmd_up(db: str, to: int | None) -> None:
    migs, done = discover(), applied(db)
    check_checksums(migs, done)
    target = to or len(migs)
    for m in migs:
        if m.version in done or m.version > target:
            continue
        t0 = time.monotonic()
        sql = (f"SELECT pg_advisory_xact_lock({LOCK_KEY});\n{m.up.read_text(encoding='utf-8')}\n"
               f"INSERT INTO core.schema_migrations (version, name, checksum) "
               f"VALUES ({m.version}, '{m.name}', '{m.checksum}');")
        run(sql, db)
        ms = int((time.monotonic() - t0) * 1000)
        run(f"UPDATE core.schema_migrations SET duration_ms = {ms} WHERE version = {m.version};", db)
        print(f"up   V{m.version:03d} {m.name} ({ms} ms)")
    apply_grants(db)


def cmd_down(db: str, to: int | None, steps: int | None) -> None:
    migs, done = discover(), applied(db)
    check_checksums(migs, done)
    order = sorted(done, reverse=True)
    if steps is not None:
        order = order[:steps]
    elif to is not None:
        order = [v for v in order if v > to]
    else:
        raise SystemExit("down needs --to N or --steps K")
    by_version = {m.version: m for m in migs}
    for v in order:
        m = by_version[v]
        sql = (f"SELECT pg_advisory_xact_lock({LOCK_KEY});\n{m.down.read_text(encoding='utf-8')}\n"
               f"DELETE FROM core.schema_migrations WHERE version = {v};")
        run(sql, db)
        print(f"down V{v:03d} {m.name}")
    apply_grants(db)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["status", "up", "down"])
    ap.add_argument("--db", default=PG["database"])
    ap.add_argument("--to", type=int)
    ap.add_argument("--steps", type=int)
    a = ap.parse_args()
    if a.command == "status":
        cmd_status(a.db)
    elif a.command == "up":
        cmd_up(a.db, a.to)
    else:
        cmd_down(a.db, a.to, a.steps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
