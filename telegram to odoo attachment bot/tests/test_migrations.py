import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text

from docbot.db.base import Base
from docbot.db.migrate import head_revision, run_migrations


def sync_engine(db_url: str) -> sa.Engine:
    return sa.create_engine(db_url.replace("sqlite+aiosqlite", "sqlite"))


def test_migration_creates_the_expected_tables(db_url):
    engine = sync_engine(db_url)
    tables = set(sa.inspect(engine).get_table_names())
    engine.dispose()
    assert {"jobs", "audit_log", "alembic_version"} <= tables


def test_database_is_at_the_head_revision(db_url):
    engine = sync_engine(db_url)
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    engine.dispose()
    assert version == head_revision()


def test_running_migrations_again_is_harmless(db_url):
    run_migrations(db_url)
    run_migrations(db_url)


def test_models_and_migrations_describe_the_same_schema(db_url):
    """If this fails, a model changed without a new Alembic revision (run `alembic revision --autogenerate`)."""
    engine = sync_engine(db_url)
    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        diff = compare_metadata(context, Base.metadata)
    engine.dispose()
    assert diff == []


async def test_sqlite_is_configured_for_a_durable_queue(engine):
    async with engine.connect() as conn:
        assert (await conn.execute(text("PRAGMA journal_mode"))).scalar_one() == "wal"
        assert (await conn.execute(text("PRAGMA synchronous"))).scalar_one() == 2  # FULL: commits survive power loss
        assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
