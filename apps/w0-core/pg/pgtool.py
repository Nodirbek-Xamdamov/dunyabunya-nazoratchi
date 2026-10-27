"""Small helpers for the Genesis PostgreSQL cluster: paths, psql runner, SCRAM verifiers.

Secrets are read through genesis_common and never printed. psql gets passwords from
secrets/pgpass.conf via PGPASSFILE, SQL goes through stdin (not the command line).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "shared"))

from genesis_common import load_config  # noqa: E402

CFG = load_config("w0-core", root=ROOT)
PG = CFG["postgres"]
BIN = Path(PG["bin_dir"])
SECRETS = CFG.path("paths.secrets")
ENV_FILE = "postgres.env"
PGPASS_FILE = SECRETS / "pgpass.conf"

SUPERUSER = "postgres"
APP_ROLES = ["genesis_migrator", "w0_admin", "w1_ingest", "w2_analyst", "w3_dashboard", "w4_camera"]


def password_key(role: str) -> str:
    return f"{role.upper()}_PASSWORD"


def scram_verifier(password: str, iterations: int = 4096) -> str:
    """SCRAM-SHA-256 verifier as PostgreSQL stores it; the server never sees the password."""
    salt = os.urandom(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def pg_env(password: str | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGPASSWORD", None)
    env["PGPASSFILE"] = str(PGPASS_FILE)
    env["PGCLIENTENCODING"] = "UTF8"
    env["LC_MESSAGES"] = "C"
    if password is not None:  # only for the wrong-password test
        env["PGPASSWORD"] = password
        env["PGPASSFILE"] = os.devnull
    return env


def psql(sql: str, user: str = SUPERUSER, db: str = "postgres", password: str | None = None,
         check: bool = True, single_transaction: bool = False) -> subprocess.CompletedProcess:
    cmd = [str(BIN / "psql.exe"), "-X", "-q", "-v", "ON_ERROR_STOP=1", "-w",
           "-h", PG["host"], "-p", str(PG["port"]), "-U", user, "-d", db, "-At", "-f", "-"]
    if single_transaction:
        cmd.insert(1, "-1")
    res = subprocess.run(cmd, input=sql, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", env=pg_env(password))
    if check and res.returncode != 0:
        raise RuntimeError(f"psql failed as {user}@{db}: {res.stderr.strip()}")
    return res
