"""Who saw what (W3-05 "ko'rish jurnali"), one JSON line per event in ``Settings.access_log``.

The plan puts this into ``core.audit_log``, but since V011 the ``w3_dashboard`` role has no INSERT there
(SCHEMA.md §12) and no function writes view events yet ([W3-05] [CONTRACT] in QUESTIONS.md). Until W0
adds one, events stay in this local file; the same fields (actor, action, object_type, object_id, details,
client_addr) move to the database unchanged. Passwords are never written; a failed login names the
login only when such a user exists.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("dashboard.access")
_LOCK = threading.Lock()


def write(path: Path, actor: str, action: str, object_type: str | None = None, object_id: str | None = None,
          details: dict | None = None, client_addr: str | None = None) -> None:
    record = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "actor": actor, "action": action,
              "object_type": object_type, "object_id": object_id, "details": details or {},
              "client_addr": client_addr}
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:  # the page still works; the problem shows in the dashboard log
        log.warning("access log not written: %s", type(exc).__name__)


def read(path: Path, limit: int = 200) -> list[dict]:
    """Newest last; for the tests and the system page."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
