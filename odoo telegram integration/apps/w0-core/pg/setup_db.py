"""Create roles, the `genesis` database and its schemas (W0-03, W0-04). Idempotent, prints no secrets.

Tables come from migrations (migrate.py, run as genesis_migrator), which also apply roles.grants_sql().
"""
from __future__ import annotations

from pgtool import APP_ROLES, ENV_FILE, PG, password_key, psql, scram_verifier  # adds shared/ to sys.path
from roles import MIGRATOR, database_sql, roles_sql

import genesis_common


def ensure_database(name: str) -> None:
    exists = psql(f"SELECT 1 FROM pg_database WHERE datname = '{name}';").stdout.strip()
    if not exists:
        psql(f'CREATE DATABASE "{name}" OWNER {MIGRATOR} TEMPLATE template0;')
    psql(database_sql(name), db=name)


def ensure_roles() -> None:
    values = genesis_common.read_secret_env(ENV_FILE)
    verifiers = {role: scram_verifier(values[password_key(role)]) for role in APP_ROLES}
    psql(roles_sql(verifiers))
    psql("REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;")


def main() -> None:
    ensure_roles()
    ensure_database(PG["database"])
    names = "', '".join(APP_ROLES)
    rows = psql(f"SELECT rolname || ' login=' || rolcanlogin || ' super=' || rolsuper FROM pg_roles "
                f"WHERE rolname IN ('{names}') ORDER BY 1;").stdout.split("\n")
    print("roles:", "; ".join(r for r in rows if r))
    print("database:", PG["database"], "owner:",
          psql(f"SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '{PG['database']}';").stdout.strip(),
          "schemas:",
          psql("SELECT string_agg(nspname || '(' || pg_get_userbyid(nspowner) || ')', ' ' ORDER BY nspname) "
               "FROM pg_namespace WHERE nspname IN ('core','api','public');", db=PG["database"]).stdout.strip())


if __name__ == "__main__":
    main()
