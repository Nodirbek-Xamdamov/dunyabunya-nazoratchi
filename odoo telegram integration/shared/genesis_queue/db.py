"""Connections for the queue: role password from secrets/postgres.env (never logged)."""
from __future__ import annotations

import psycopg

from genesis_common import Config, load_config, read_secret_env


def connect(role: str, db: str | None = None, cfg: Config | None = None) -> psycopg.Connection:
    """Autocommit connection as `role` (e.g. "w2_analyst"); the queue opens its own transactions."""
    cfg = cfg or load_config()
    pg = cfg["postgres"]
    password = read_secret_env("postgres.env", cfg)[f"{role.upper()}_PASSWORD"]
    return psycopg.connect(host=pg["host"], port=pg["port"], dbname=db or pg["database"], user=role,
                           password=password, autocommit=True, application_name=f"genesis:{role}")
