from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from docbot.clock import Clock, utcnow
from docbot.db.models import AuditLog
from docbot.db.session import SessionFactory
from docbot.pipeline.states import Method, Status
from docbot.redaction import redact

MAX_ERROR_LEN = 2000


def clean_error(error: str | None) -> str | None:
    """Errors are stored in the DB: mask secrets (exception texts may contain URLs with tokens) and cap the size."""
    if error is None:
        return None
    return redact(error)[:MAX_ERROR_LEN]


def audit_entry(
    now: datetime,
    *,
    job_id: int | None,
    actor: str,
    event: str,
    from_status: Status | None = None,
    to_status: Status | None = None,
    method: Method | None = None,
    result: str = "ok",
    error: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Build a row for an existing session, so it commits atomically with the change it describes."""
    return AuditLog(
        ts=now,
        job_id=job_id,
        actor=actor,
        event=event,
        from_status=from_status.value if from_status else None,
        to_status=to_status.value if to_status else None,
        method=method.value if method else None,
        result=result,
        error=clean_error(error),
        details=details,
    )


class AuditRepository:
    """Standalone access for events that are not part of a job change (link/unlink, admin commands)."""

    def __init__(self, session_factory: SessionFactory, *, clock: Clock = utcnow):
        self._sf = session_factory
        self._clock = clock

    async def record(
        self,
        *,
        actor: str,
        event: str,
        job_id: int | None = None,
        result: str = "ok",
        error: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        entry = audit_entry(
            self._clock(), job_id=job_id, actor=actor, event=event, result=result, error=error, details=details
        )
        async with self._sf() as session, session.begin():
            session.add(entry)

    async def list_for_job(self, job_id: int) -> list[AuditLog]:
        async with self._sf() as session:
            rows = await session.execute(select(AuditLog).where(AuditLog.job_id == job_id).order_by(AuditLog.id))
            return list(rows.scalars())

    async def count_since(self, event: str, since: datetime) -> int:
        """How many ``event`` rows were written at or after ``since`` (e.g. AI calls today, for the daily limit)."""
        async with self._sf() as session:
            query = select(func.count()).select_from(AuditLog).where(AuditLog.event == event, AuditLog.ts >= since)
            return (await session.execute(query)).scalar_one()
