import logging
from concurrent.futures import ThreadPoolExecutor

import pytest
import pytest_asyncio
from helpers import FakeClock

from docbot import redaction
from docbot.config import QueueSettings
from docbot.db.migrate import run_migrations
from docbot.db.repositories.audit import AuditRepository
from docbot.db.repositories.jobs import JobRepository
from docbot.db.session import create_engine, create_session_factory


@pytest.fixture(autouse=True)
def _isolate_global_state():
    """Tests that configure logging or register secrets must not leak into each other."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    for handler in root.handlers[:]:
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)
    redaction.clear_secrets()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def queue_settings() -> QueueSettings:
    return QueueSettings(
        workers=2,
        poll_interval_seconds=0.01,
        lease_seconds=60,
        max_attempts=3,
        backoff_base_seconds=10,
        backoff_max_seconds=100,
        backoff_jitter=0,
    )


@pytest.fixture
def db_url(tmp_path) -> str:
    """A fresh SQLite file created by the real migrations."""
    url = "sqlite+aiosqlite:///" + (tmp_path / "test.db").as_posix()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(run_migrations, url).result()
    return url


@pytest_asyncio.fixture
async def engine(db_url):
    eng = create_engine(db_url)
    yield eng
    await eng.dispose()


@pytest.fixture
def session_factory(engine):
    return create_session_factory(engine)


@pytest.fixture
def jobs(session_factory, queue_settings, clock) -> JobRepository:
    return JobRepository(session_factory, queue_settings, clock=clock)


@pytest.fixture
def audit(session_factory, clock) -> AuditRepository:
    return AuditRepository(session_factory, clock=clock)
