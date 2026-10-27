"""Monthly archive (D-13): partition -> compressed file on G: -> verify -> drop. And the way back.

    python -E archive_month.py archive --month 2025-01 [--table messages|transcripts|all] [--db genesis] [--dest DIR]
    python -E archive_month.py restore --month 2025-01 --table messages [--db genesis] [--dest DIR]

Runs as genesis_migrator (owner). Safety: only months older than config archive.keep_months; the
partition is re-attached if anything fails before the drop; the file is verified (pg_restore list +
row count from the dump itself) and its sha256 is stored in core.archive_log before DROP TABLE.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from pgtool import BIN, CFG, PG, pg_env, psql
from roles import MIGRATOR

TABLES = ["messages", "transcripts"]


def sql(text: str, db: str) -> str:
    return psql(text, user=MIGRATOR, db=db).stdout.strip()


def tool(name: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(BIN / name), "-h", PG["host"], "-p", str(PG["port"]), "-U", MIGRATOR, "-w", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=pg_env())


def bounds(db: str, month: date) -> tuple[str, str]:
    lo, hi = sql(f"SELECT lo || '|' || hi FROM core.month_bounds('{month}');", db).split("|")
    return lo, hi


def dump_row_count(path: Path) -> int:
    """Rows in the dump, counted from its COPY data (no database needed)."""
    res = subprocess.run([str(BIN / "pg_restore.exe"), "--data-only", "-f", "-", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", env=pg_env())
    if res.returncode != 0:
        raise RuntimeError(f"pg_restore cannot read {path.name}: {res.stderr.strip()}")
    rows, inside = 0, False
    for line in res.stdout.splitlines():
        if line.startswith("COPY "):
            inside = True
        elif inside and line == "\\.":
            inside = False
        elif inside:
            rows += 1
    return rows


def archive(db: str, table: str, month: date, dest: Path) -> None:
    part = sql(f"SELECT core.partition_name('{table}', '{month}');", db)
    keep = int(sql("SELECT (value #>> '{}')::int FROM core.config WHERE key = 'archive.keep_months';", db) or 12)
    cutoff = sql(f"SELECT (date_trunc('month', now() AT TIME ZONE 'Asia/Tashkent') - make_interval(months => {keep}))::date;", db)
    if str(month) >= cutoff:
        raise SystemExit(f"{part}: month {month} is not older than archive.keep_months={keep} (cutoff {cutoff})")
    attached = sql(f"SELECT count(*) FROM pg_inherits WHERE inhrelid = to_regclass('core.{part}');", db)
    if attached != "1":
        raise SystemExit(f"{part}: not an attached partition (already archived?)")
    lo, hi = bounds(db, month)
    rows = int(sql(f"SELECT count(*) FROM core.{part};", db))
    target = dest / table / f"{month:%Y-%m}.dump"
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".dump.partial")

    sql(f"ALTER TABLE core.{table} DETACH PARTITION core.{part};", db)
    try:
        res = tool("pg_dump.exe", "-d", db, "-Fc", "-Z", "9", "-t", f"core.{part}", "-f", str(partial))
        if res.returncode != 0:
            raise RuntimeError(f"pg_dump failed: {res.stderr.strip()}")
        dumped = dump_row_count(partial)
        if dumped != rows:
            raise RuntimeError(f"row count mismatch: table {rows}, dump {dumped}")
        partial.replace(target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        sql(f"""INSERT INTO core.archive_log (parent, partition, month, row_count, file_path, file_bytes, sha256, verified)
                VALUES ('{table}', '{part}', '{month}', {rows}, '{target}', {target.stat().st_size}, '{digest}', true)
                ON CONFLICT (parent, month) DO UPDATE SET row_count = EXCLUDED.row_count, file_path = EXCLUDED.file_path,
                    file_bytes = EXCLUDED.file_bytes, sha256 = EXCLUDED.sha256, verified = true, dropped_at = NULL,
                    archived_at = now();""", db)
    except Exception:
        sql(f"ALTER TABLE core.{table} ATTACH PARTITION core.{part} FOR VALUES FROM ('{lo}') TO ('{hi}');", db)
        print(f"{part}: FAILED, partition re-attached")
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    sql(f"DROP TABLE core.{part}; UPDATE core.archive_log SET dropped_at = now() WHERE parent = '{table}' AND month = '{month}';", db)
    print(f"{part}: archived {rows} rows -> {target} ({target.stat().st_size} bytes, sha256 {digest[:16]}...), partition dropped")


def restore(db: str, table: str, month: date, dest: Path) -> None:
    part = sql(f"SELECT core.partition_name('{table}', '{month}');", db)
    src = dest / table / f"{month:%Y-%m}.dump"
    want = sql(f"SELECT sha256 FROM core.archive_log WHERE parent = '{table}' AND month = '{month}';", db)
    if want and hashlib.sha256(src.read_bytes()).hexdigest() != want:
        raise SystemExit(f"{src}: sha256 differs from archive_log")
    res = tool("pg_restore.exe", "-d", db, "--exit-on-error", str(src))
    if res.returncode != 0:
        raise SystemExit(f"pg_restore failed: {res.stderr.strip()}")
    lo, hi = bounds(db, month)
    sql(f"ALTER TABLE core.{table} ATTACH PARTITION core.{part} FOR VALUES FROM ('{lo}') TO ('{hi}');"
        f"UPDATE core.archive_log SET dropped_at = NULL WHERE parent = '{table}' AND month = '{month}';", db)
    rows = sql(f"SELECT count(*) FROM core.{table} WHERE tableoid = 'core.{part}'::regclass;", db)
    print(f"{part}: restored from {src} and attached, {rows} rows visible through core.{table}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["archive", "restore"])
    ap.add_argument("--month", required=True, help="YYYY-MM")
    ap.add_argument("--table", default="all", choices=["all", *TABLES])
    ap.add_argument("--db", default=PG["database"])
    ap.add_argument("--dest", type=Path, default=CFG.path("paths.archive"))
    a = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}", a.month):
        raise SystemExit("--month must be YYYY-MM")
    month = date(int(a.month[:4]), int(a.month[5:]), 1)
    tables = TABLES if a.table == "all" else [a.table]
    for t in tables:
        if a.command == "archive":
            exists = sql(f"SELECT to_regclass('core.' || core.partition_name('{t}', '{month}')) IS NOT NULL;", a.db)
            if exists != "t" and a.table == "all":
                print(f"{t}: no partition for {month:%Y-%m}, skipped")
                continue
            archive(a.db, t, month, a.dest)
        else:
            restore(a.db, t, month, a.dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
