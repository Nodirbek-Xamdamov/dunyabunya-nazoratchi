"""The running bot: one process with Telegram polling, the worker pool, the notifier and housekeeping.

* One instance only (a lock file): two pollers on one bot token would fight over updates.
* ``data/heartbeat`` is rewritten every 30 s; the Windows watchdog restarts a process whose heartbeat stopped.
* Migrations run at start, so an updated checkout just needs a restart.
* Odoo or OpenAI being down at start is not fatal: jobs wait in the queue and retry.
"""

import asyncio
import contextlib
import logging
import os
import signal
import sys
from datetime import timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher

from docbot.ai import OpenAIReader
from docbot.clock import utcnow
from docbot.config import AppConfig, readiness_issues
from docbot.db.migrate import run_migrations
from docbot.db.repositories.audit import AuditRepository
from docbot.db.repositories.jobs import JobRepository
from docbot.db.session import create_engine, create_session_factory
from docbot.documents import Documents
from docbot.errors import DocbotError
from docbot.odoo import OdooClient
from docbot.pipeline.files import FileStore
from docbot.pipeline.finder import RecordFinder
from docbot.pipeline.processor import Processor
from docbot.pipeline.review import ReviewService
from docbot.pipeline.worker import WorkerPool
from docbot.telegram.chats import ChatRegistry
from docbot.telegram.files import TelegramFiles
from docbot.telegram.handlers import build_router
from docbot.telegram.notifier import Notifier

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 30
CLEANUP_SECONDS = 3600


class InstanceLock:
    """An exclusive lock on a file, released by the OS if the process dies. Holds the PID for the watchdog."""

    def __init__(self, path: Path):
        self._path = path
        self._file = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self._path, "a+")  # noqa: SIM115 (kept open while the lock is held)
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._file = handle
        self._path.with_suffix(".pid").write_text(str(os.getpid()), encoding="ascii")
        return True

    def release(self) -> None:
        if self._file is not None:
            with contextlib.suppress(OSError):
                self._path.with_suffix(".pid").unlink(missing_ok=True)
            self._file.close()  # closing the handle releases the lock
            self._file = None


async def _every(stop: asyncio.Event, seconds: float, action) -> None:
    while not stop.is_set():
        try:
            await action()
        except Exception:
            log.exception("housekeeping step failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), seconds)


async def serve(cfg: AppConfig) -> int:
    issues = readiness_issues(cfg)
    if issues:
        for issue in issues:
            log.error("config: %s", issue)
        return 2
    lock = InstanceLock(cfg.lock_path)
    if not lock.acquire():
        log.error("another docbot is already running (%s)", cfg.lock_path)
        return 3
    try:
        _beat(cfg)  # the watchdog must not take a slow start (migrations, Odoo check) for a hang
        await asyncio.to_thread(run_migrations, cfg.database_url)
        return await _serve(cfg)
    finally:
        lock.release()


def _beat(cfg: AppConfig) -> None:
    cfg.heartbeat_path.write_text(f"{os.getpid()} {utcnow().isoformat()}", encoding="ascii")


async def _serve(cfg: AppConfig) -> int:
    settings = cfg.settings
    engine = create_engine(cfg.database_url)
    session_factory = create_session_factory(engine)
    jobs = JobRepository(session_factory, settings.queue)
    audit = AuditRepository(session_factory)
    documents = Documents(cfg.documents)

    odoo = OdooClient(
        cfg.env.odoo_url, cfg.env.odoo_db, cfg.env.odoo_user, cfg.env.odoo_api_key.get_secret_value(),
        timeout=settings.odoo.timeout_seconds,
    )
    try:
        log.info("Odoo %s (%s) is reachable", await odoo.version(), cfg.env.odoo_url)
    except DocbotError as exc:
        log.warning("Odoo is not reachable yet, jobs will wait: %s", exc)

    ai = None
    if settings.ai.enabled and cfg.env.openai_api_key is not None:
        ai = OpenAIReader(cfg.env.openai_api_key.get_secret_value(), settings.ai, documents)

    bot = Bot(cfg.env.bot_token.get_secret_value())
    finder = RecordFinder(odoo, documents)
    files = FileStore(cfg.images_dir, TelegramFiles(bot), max_bytes=int(settings.files.max_mb * 1024 * 1024))
    chats = ChatRegistry(cfg.chats_path, cfg.env.allowed_chat_ids)
    notifier = Notifier(
        bot, jobs, documents, admin_ids=cfg.env.admin_ids, chats=chats,
        messages=settings.notify.messages, success_message=settings.notify.success_message,
        unclear_text=settings.notify.unclear_text, missing_pages_text=settings.notify.missing_pages_text,
    )
    processor = Processor(
        settings=settings, documents=documents, finder=finder, odoo=odoo, ai=ai, files=files, audit=audit,
        on_change=notifier.wake,
    )
    review = ReviewService(jobs, finder, documents, cfg.env.admin_ids)
    pool = WorkerPool(jobs, processor, settings.queue)

    dispatcher = Dispatcher(jobs=jobs, review=review)
    dispatcher.include_router(build_router(cfg, chats))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop(*_args) -> None:
        loop.call_soon_threadsafe(stop.set)

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            with contextlib.suppress(ValueError, OSError):
                signal.signal(getattr(signal, name), request_stop)

    async def heartbeat() -> None:
        _beat(cfg)

    async def cleanup() -> None:
        before = utcnow() - timedelta(days=settings.files.keep_days)
        for job in await jobs.list_disposable_files(before):
            files.delete(job.path)
            await jobs.forget_file(job.id)

    odoo_alerted = False

    async def watch_odoo() -> None:
        """One message to the admins when Odoo refuses our login, one when it works again."""
        nonlocal odoo_alerted
        if odoo.login_error and not odoo_alerted:
            text = f"⚠️ {odoo.login_error}. Hujjatlar navbatda kutyapti, kalit tuzatilgach biriktiriladi."
        elif not odoo.login_error and odoo_alerted:
            text = "✅ Odoo ulanishi tiklandi, kutayotgan hujjatlar biriktirilmoqda."
        else:
            return
        for admin in cfg.env.admin_ids:
            with contextlib.suppress(Exception):
                await bot.send_message(admin, text)
        odoo_alerted = not odoo_alerted

    async def polling() -> None:
        try:
            me = await bot.get_me()
            log.info("Telegram bot @%s started", me.username)
            await dispatcher.start_polling(
                bot, handle_signals=False, close_bot_session=False, allowed_updates=dispatcher.resolve_used_update_types()
            )
        finally:
            stop.set()  # polling ended (stop requested, or a fatal error such as a revoked token)

    tasks = [
        asyncio.create_task(pool.run(stop), name="workers"),
        asyncio.create_task(notifier.run(stop), name="notifier"),
        asyncio.create_task(_every(stop, HEARTBEAT_SECONDS, heartbeat), name="heartbeat"),
        asyncio.create_task(_every(stop, CLEANUP_SECONDS, cleanup), name="cleanup"),
        asyncio.create_task(_every(stop, 60, watch_odoo), name="watch-odoo"),
    ]
    poller = asyncio.create_task(polling(), name="polling")
    log.info("docbot running (workers: %s, AI: %s)", settings.queue.workers, ai.model if ai else "off")
    try:
        await stop.wait()
    finally:
        log.info("stopping...")
        with contextlib.suppress(RuntimeError):
            await dispatcher.stop_polling()
        results = await asyncio.gather(poller, *tasks, return_exceptions=True)
        for task, result in zip([poller, *tasks], results, strict=True):
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                log.error("%s ended with %s: %s", task.get_name(), type(result).__name__, result)
        await bot.session.close()
        if ai is not None:
            await ai.close()
        await engine.dispose()
        log.info("stopped")
    return 0
