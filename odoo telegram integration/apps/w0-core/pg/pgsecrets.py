"""Create secrets/postgres.env and secrets/pgpass.conf if missing. Prints no secret values.

    python pgsecrets.py                      # create missing files
    python pgsecrets.py --initdb-pwfile F    # also write the superuser password to F (for initdb)
"""
from __future__ import annotations

import argparse
import secrets as rnd
from pathlib import Path

from pgtool import APP_ROLES, ENV_FILE, PG, PGPASS_FILE, SECRETS, SUPERUSER, password_key

import genesis_common


def ensure_env() -> dict[str, str]:
    path = SECRETS / ENV_FILE
    if path.is_file():
        values = genesis_common.read_secret_env(ENV_FILE)
    else:
        values = {}
    wanted = {"PG_HOST": PG["host"], "PG_PORT": str(PG["port"]), "PG_DATABASE": PG["database"],
              "PG_SUPERUSER": SUPERUSER}
    for role in [SUPERUSER, *APP_ROLES]:
        wanted[password_key(role)] = ""
    changed = False
    for key, default in wanted.items():
        if key not in values:
            values[key] = default or rnd.token_urlsafe(24)  # [A-Za-z0-9_-]: safe for pgpass
            changed = True
    if changed:
        SECRETS.mkdir(parents=True, exist_ok=True)
        lines = ["# Genesis PostgreSQL (W0-03). Generated; do not commit, do not print."]
        lines += [f"{k}={v}" for k, v in values.items()]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return values


def write_pgpass(values: dict[str, str]) -> None:
    host, port = PG["host"], PG["port"]
    lines = [f"{host}:{port}:*:{role}:{values[password_key(role)]}" for role in [SUPERUSER, *APP_ROLES]]
    PGPASS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--initdb-pwfile", type=Path)
    args = ap.parse_args()
    values = ensure_env()
    write_pgpass(values)
    if args.initdb_pwfile:
        args.initdb_pwfile.write_text(values[password_key(SUPERUSER)] + "\n", encoding="ascii")
    print(f"ok: {ENV_FILE} ({len(values)} keys), pgpass.conf ({1 + len(APP_ROLES)} roles)")


if __name__ == "__main__":
    main()
