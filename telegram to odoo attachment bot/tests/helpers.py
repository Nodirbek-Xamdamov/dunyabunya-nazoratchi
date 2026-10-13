import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from docbot.db.repositories.jobs import JobRepository, NewJob
from docbot.pipeline.states import Status


# A complete, valid configuration (dummy values) used by the config and CLI tests.
VALID_ENV = """\
BOT_TOKEN=123456789:AAHdummyTokenForTestsOnly_0123456789abcd
ALLOWED_CHAT_IDS=-1001111111111, -1002222222222
ADMIN_IDS=111,222
ODOO_URL=https://odoo.example.com/
ODOO_DB=prod
ODOO_USER=docbot
ODOO_API_KEY=0123456789abcdef0123456789abcdef01234567
"""

# A small document list (the shipped config/documents.yaml has three kinds).
VALID_DOCUMENTS = r"""
documents:
  - key: sale
    title: Sotuv buyurtmasi
    model: sale.order
    number: 'S\d{5,}'
    codes:
      - '/my/orders/(?P<id>\d+)\?access_token=(?P<token>[\w-]+)'
      - '^(?P<name>S\d{5,})$'
    total_field: amount_total
"""


class FakeClock:
    """Deterministic time for queue tests."""

    def __init__(self, start: datetime | None = None):
        self.now = start or datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make_job(n: int = 1, **overrides) -> NewJob:
    fields = dict(
        chat_id=-1001,
        message_id=n,
        telegram_user_id=42,
        file_id=f"file-{n}",
        file_unique_id=f"uniq-{n}",
        update_id=1000 + n,
    )
    return NewJob(**{**fields, **overrides})


async def eventually(check: Callable[[], Awaitable[bool]], *, timeout: float = 10.0, interval: float = 0.02) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if await check():
            return
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(interval)


async def wait_for_status(jobs: JobRepository, job_id: int, *statuses: Status, timeout: float = 10.0) -> None:
    async def check() -> bool:
        job = await jobs.get(job_id)
        return job is not None and job.status in statuses

    try:
        await eventually(check, timeout=timeout)
    except AssertionError:
        job = await jobs.get(job_id)
        raise AssertionError(f"job {job_id} is {job.status if job else None}, expected one of {statuses}") from None
