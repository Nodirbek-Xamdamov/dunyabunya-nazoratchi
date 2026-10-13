from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _alembic_config(database_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))  # ConfigParser interpolation
    return cfg


def head_revision() -> str | None:
    return ScriptDirectory.from_config(_alembic_config("")).get_current_head()


def run_migrations(database_url: str, revision: str = "head") -> None:
    """Bring the schema up to date. Blocking; safe to call from sync or async code.

    Alembic's env.py starts its own event loop. It runs in a helper thread so it can neither fail
    inside a running loop nor leave the caller's thread without a current event loop.
    """
    cfg = _alembic_config(database_url)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(command.upgrade, cfg, revision).result()
