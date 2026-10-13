import asyncio

from alembic import context
from sqlalchemy import pool

from docbot.db import models  # noqa: F401  (registers the tables on Base.metadata)
from docbot.db.base import Base
from docbot.db.session import create_engine
from docbot.db.types import UTCDateTime

config = context.config
target_metadata = Base.metadata


def _render_item(type_, obj, autogen_context):
    # Migrations must not import application code: render our column type as the plain type it stores as.
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime()"
    return False


def _database_url() -> str:
    """URL passed in by docbot.db.migrate; falls back to the project config for the plain `alembic` CLI."""
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    from docbot.config import load_config

    return load_config().database_url


def _run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite cannot ALTER most things in place
        compare_type=True,
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
            await connection.commit()
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported; run against a database.")
asyncio.run(_run_online())
