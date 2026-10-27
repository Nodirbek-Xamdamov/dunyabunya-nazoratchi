"""Dashboard users (W3-05): create, change role / camera right / password, switch on or off. Nothing is
deleted (a user is switched off with ``active <login> off``). Runs on the server only.

    cd apps\\w3-dashboard
    .venv\\Scripts\\python.exe -E scripts\\manage_users.py list
    .venv\\Scripts\\python.exe -E scripts\\manage_users.py add <login> --role admin|manager|viewer [--camera]
    .venv\\Scripts\\python.exe -E scripts\\manage_users.py passwd <login>
    .venv\\Scripts\\python.exe -E scripts\\manage_users.py role <login> admin|manager|viewer
    .venv\\Scripts\\python.exe -E scripts\\manage_users.py camera <login> on|off
    .venv\\Scripts\\python.exe -E scripts\\manage_users.py active <login> on|off

The password is asked twice without echo (or read from standard input with ``--password-stdin``); it is
never an argument, never printed and never logged; only its scrypt hash is stored. The database is the
dashboard's own (app.toml [db] database, or W3_DATABASE). The connection uses the ``w3_dashboard`` role and
touches ``core.dashboard_users`` only.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent.parent
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

import psycopg  # noqa: E402

from core import db, users  # noqa: E402
from core.settings import Settings, load_settings  # noqa: E402

TABLE = "core.dashboard_users"


def connect(settings: Settings) -> psycopg.Connection:
    """A writable connection (unlike the dashboard's), used only for the users table."""
    dbc = settings.db
    return psycopg.connect(host=dbc.host, port=dbc.port, dbname=dbc.database, user=dbc.role, password=db._password(),
                           options="-c statement_timeout=10000 -c search_path=core -c TimeZone=UTC",
                           connect_timeout=5)


def _login(login: str) -> str:
    login = users.normalize_login(login)
    if not users.LOGIN_RE.match(login):
        raise users.UserError("login: 2-40 belgi, kichik lotin harflari, raqam, . _ -")
    return login


def _role(role: str) -> str:
    if role not in users.ROLES:
        raise users.UserError(f"rol: {' | '.join(users.ROLES)}")
    return role


def _changed(cursor, login: str) -> None:
    if cursor.rowcount != 1:
        raise users.UserError(f"{login}: bunday foydalanuvchi yo'q")


def add(conn, login: str, role: str, camera: bool, password: str) -> None:
    login, role = _login(login), _role(role)
    try:
        with conn.transaction():  # a savepoint: a duplicate login leaves the rest of the work usable
            conn.execute(f"INSERT INTO {TABLE} (login, password_hash, role, camera_access) VALUES (%s, %s, %s, %s)",
                         [login, users.hash_password(password), role, camera])
    except psycopg.errors.UniqueViolation as exc:
        raise users.UserError(f"{login}: bunday login bor") from exc


def set_password(conn, login: str, password: str) -> None:
    login = _login(login)
    _changed(conn.execute(f"UPDATE {TABLE} SET password_hash = %s WHERE login = %s",
                          [users.hash_password(password), login]), login)


def set_role(conn, login: str, role: str) -> None:
    login, role = _login(login), _role(role)
    _changed(conn.execute(f"UPDATE {TABLE} SET role = %s WHERE login = %s", [role, login]), login)


def set_camera(conn, login: str, value: bool) -> None:
    login = _login(login)
    _changed(conn.execute(f"UPDATE {TABLE} SET camera_access = %s WHERE login = %s", [value, login]), login)


def set_active(conn, login: str, value: bool) -> None:
    login = _login(login)
    _changed(conn.execute(f"UPDATE {TABLE} SET is_active = %s WHERE login = %s", [value, login]), login)


def rows(conn) -> list[tuple]:
    return conn.execute(f"SELECT login, role, camera_access, is_active, created_at FROM {TABLE} ORDER BY login").fetchall()


def _ask_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("Yangi parol: ")
    if first != getpass.getpass("Parolni qayta kiriting: "):
        raise users.UserError("parollar mos emas")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="W3 dashboard foydalanuvchilari (core.dashboard_users)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p_add = sub.add_parser("add")
    p_add.add_argument("login")
    p_add.add_argument("--role", required=True, choices=users.ROLES)
    p_add.add_argument("--camera", action="store_true", help="kamera ma'lumotlari va kadrlarga ruxsat")
    p_add.add_argument("--password-stdin", action="store_true")
    p_pw = sub.add_parser("passwd")
    p_pw.add_argument("login")
    p_pw.add_argument("--password-stdin", action="store_true")
    p_role = sub.add_parser("role")
    p_role.add_argument("login")
    p_role.add_argument("role", choices=users.ROLES)
    for name in ("camera", "active"):
        p_flag = sub.add_parser(name)
        p_flag.add_argument("login")
        p_flag.add_argument("value", choices=("on", "off"))
    args = parser.parse_args(argv)
    settings = load_settings()
    try:
        password = _ask_password(args.password_stdin) if args.cmd in ("add", "passwd") else None
        with connect(settings) as conn:
            if args.cmd == "list":
                for login, role, camera, active, created in rows(conn):
                    print(f"{login:20} {role:8} kamera={'ha' if camera else "yo'q":4} "
                          f"{'faol' if active else "o'chirilgan":11} {created:%Y-%m-%d}")
                return 0
            if args.cmd == "add":
                add(conn, args.login, args.role, args.camera, password)
            elif args.cmd == "passwd":
                set_password(conn, args.login, password)
            elif args.cmd == "role":
                set_role(conn, args.login, args.role)
            elif args.cmd == "camera":
                set_camera(conn, args.login, args.value == "on")
            elif args.cmd == "active":
                set_active(conn, args.login, args.value == "on")
    except users.UserError as exc:
        print(f"Xato: {exc}", file=sys.stderr)
        return 2
    print(f"OK: {args.cmd} {users.normalize_login(args.login)} ({settings.db.label})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
