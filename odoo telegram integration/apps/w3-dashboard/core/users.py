"""Dashboard logins and roles (W3-05; SCHEMA.md ``core.dashboard_users``, W3 [CONTRACT] 13).

* Roles (the table's CHECK): ``admin`` = egasi, ``manager`` = menejer, ``viewer`` = kuzatuvchi.
  Camera data is a separate right (``camera_access``), for every role, the owner included (D-31).
* What each right opens (``Access``): numbers and AI summaries -> everybody who logged in; raw messages
  (text, voice transcript, media, the original request text) -> admin and manager; camera page -> camera
  right; camera frames -> camera right and ``[camera] show_frames``; system page -> admin.
* Only a hash is stored: scrypt (Python standard library, no extra package), format
  ``scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>``. A password never goes to a log, the URL or the screen.
* The dashboard reads users through its read-only session. Users are written only by
  scripts/manage_users.py over a separate connection that may touch ``core.dashboard_users`` and nothing else.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

from core.db import Session

ROLES = ("admin", "manager", "viewer")
RAW_ROLES = ("admin", "manager")
LOGIN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,39}$")
MIN_PASSWORD = 10
_N, _R, _P, _LEN = 2 ** 15, 8, 1, 32
_MAXMEM = 64 * 1024 * 1024


class UserError(ValueError):
    """A wrong login, role or password rule; the message is for the person running the command."""


def hash_password(password: str) -> str:
    if len(password or "") < MIN_PASSWORD:
        raise UserError(f"parol kamida {MIN_PASSWORD} belgi bo'lsin")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_LEN, maxmem=_MAXMEM)
    b64 = lambda raw: base64.b64encode(raw).decode("ascii")  # noqa: E731
    return f"scrypt${_N}${_R}${_P}${b64(salt)}${b64(digest)}"


def check_password(password: str, stored: str | None) -> bool:
    try:
        scheme, n, r, p, salt, digest = str(stored or "").split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt((password or "").encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r),
                                p=int(p), dklen=len(expected), maxmem=_MAXMEM)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


_DUMMY = hash_password(secrets.token_urlsafe(24))  # unknown logins cost the same time as known ones


@dataclass(frozen=True)
class Access:
    login: str
    role: str
    camera: bool

    @property
    def raw(self) -> bool:
        return self.role in RAW_ROLES

    @property
    def system(self) -> bool:
        return self.role == "admin"

    def frames(self, show_frames: bool) -> bool:
        return self.camera and show_frames

    def to_state(self) -> dict:
        return {"login": self.login, "role": self.role, "camera": self.camera}

    @classmethod
    def from_state(cls, value) -> "Access | None":
        if not isinstance(value, dict) or value.get("role") not in ROLES or not value.get("login"):
            return None
        return cls(str(value["login"]), str(value["role"]), bool(value.get("camera")))


def normalize_login(login: str) -> str:
    return str(login or "").strip().lower()


def fetch(session: Session, login: str) -> tuple | None:
    """(login, password_hash, role, camera_access) of an active user, or None."""
    return session.one("users.fetch", """
        SELECT login, password_hash, role, camera_access FROM core.dashboard_users
        WHERE login = %s AND is_active""", [normalize_login(login)])


def authenticate(session: Session, login: str, password: str) -> Access | None:
    login = normalize_login(login)
    row = fetch(session, login) if LOGIN_RE.match(login) else None
    if not check_password(password, row[1] if row else _DUMMY) or row is None or row[2] not in ROLES:
        return None
    return Access(row[0], row[2], bool(row[3]))


def count_active(session: Session) -> int:
    return int(session.scalar("users.count", "SELECT count(*) FROM core.dashboard_users WHERE is_active", default=0))


def known_login(session: Session, login: str) -> bool:
    """Whether a login exists (only to decide what the access log may name; never shown on the page)."""
    login = normalize_login(login)
    return bool(LOGIN_RE.match(login)) and session.one(
        "users.known", "SELECT 1 FROM core.dashboard_users WHERE login = %s", [login]) is not None
