"""Command line entry point: ``python -m docbot <command>``."""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from docbot import __version__
from docbot.config import AppConfig, load_config, readiness_issues
from docbot.db.migrate import head_revision, run_migrations
from docbot.errors import ConfigError
from docbot.logging_setup import setup_logging
from docbot.redaction import register_secrets


def bootstrap(cfg: AppConfig, *, console: bool = True) -> None:
    """Common startup: data directories, secret masking, logging."""
    cfg.ensure_dirs()
    register_secrets(cfg.secret_values())
    log = cfg.settings.logging
    setup_logging(
        log.level,
        log_dir=cfg.log_dir if log.to_file else None,
        max_bytes=log.max_bytes,
        backup_count=log.backup_count,
        console=console,
    )


def _load(args: argparse.Namespace) -> AppConfig | None:
    try:
        return load_config(args.home)
    except ConfigError as exc:
        print("XATO: konfiguratsiya noto'g'ri:")
        for problem in exc.problems:
            print(f"  - {problem}")
        return None


def cmd_check_config(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if cfg is None:
        return 1
    print(f"Konfiguratsiya sintaksisi to'g'ri (loyiha papkasi: {cfg.home})")
    issues = readiness_issues(cfg)
    if not issues:
        print("Hammasi to'ldirilgan: bot ishga tushirishga tayyor.")
        return 0
    print("Botni ishga tushirishdan oldin to'ldirish kerak:")
    for issue in issues:
        print(f"  - {issue}")
    return 1 if args.strict else 0


def cmd_migrate(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if cfg is None:
        return 1
    bootstrap(cfg)
    run_migrations(cfg.database_url)
    print(f"DB sxemasi yangilandi (revision: {head_revision()}).")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if cfg is None:
        return 1
    bootstrap(cfg, console=sys.stderr.isatty())  # started by the watchdog: the log file is enough
    from docbot.app import serve  # aiogram etc. are only needed by the running bot

    try:
        return asyncio.run(serve(cfg))
    except KeyboardInterrupt:
        return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if cfg is None:
        return 1
    return asyncio.run(_status(cfg, args.limit))


async def _status(cfg: AppConfig, limit: int) -> int:
    from docbot.db.repositories.jobs import JobRepository
    from docbot.db.session import create_engine, create_session_factory
    from docbot.pipeline.states import Status

    engine = create_engine(cfg.database_url)
    try:
        jobs = JobRepository(create_session_factory(engine), cfg.settings.queue)
        counts = await jobs.counts_by_status()
        print("Holatlar: " + (", ".join(f"{s.value}={counts[s]}" for s in Status if counts.get(s)) or "bo'sh"))
        for job in await jobs.list_by_status([Status.NEEDS_REVIEW, Status.FAILED], limit=limit):
            print(f"  job {job.id} {job.status.value}: {job.error or ''}")
    finally:
        await engine.dispose()
    return 0


def cmd_requeue(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if cfg is None:
        return 1
    return asyncio.run(_requeue(cfg, args))


async def _requeue(cfg: AppConfig, args: argparse.Namespace) -> int:
    """Send failed / waiting files through the pipeline again (the running bot picks them up)."""
    from docbot.db.repositories.jobs import JobRepository
    from docbot.db.session import create_engine, create_session_factory
    from docbot.errors import InvalidTransition, LeaseLost
    from docbot.pipeline.states import Status

    statuses = [s for s, wanted in ((Status.FAILED, args.failed), (Status.NEEDS_REVIEW, args.review)) if wanted]
    if not statuses and not args.ids:
        print("Nimani qayta ishlash kerak: --failed, --review va/yoki --ids 1,2,3")
        return 2
    engine = create_engine(cfg.database_url)
    try:
        jobs = JobRepository(create_session_factory(engine), cfg.settings.queue)
        chosen = await jobs.list_by_status(statuses, limit=10_000) if statuses else []
        if args.ids:
            wanted = {int(part) for part in args.ids.split(",") if part.strip()}
            extra = await jobs.list_by_status([Status.FAILED, Status.NEEDS_REVIEW], limit=10_000)
            chosen = [j for j in {j.id: j for j in chosen + [j for j in extra if j.id in wanted]}.values()]
        done = 0
        for job in sorted(chosen, key=lambda j: j.id):
            try:
                await jobs.transition(job.id, Status.RECEIVED, worker_id=None, actor="admin:cli", event="requeued")
                done += 1
            except (InvalidTransition, LeaseLost) as exc:
                print(f"  job {job.id}: o'tkazib yuborildi ({exc})")
        print(f"Qayta navbatga qo'yildi: {done} ta. Ishlab turgan bot ularni qayta ishlaydi.")
    finally:
        await engine.dispose()
    return 0


def cmd_groups(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if cfg is None:
        return 1
    from docbot.telegram.chats import ChatRegistry

    chats = ChatRegistry(cfg.chats_path, cfg.env.allowed_chat_ids)
    if args.action == "pause":
        print("To'xtatildi: " + (", ".join(chats.pause_all()) or "guruh yo'q"))
    elif args.action == "live":
        print("Ishga tushdi (faqat hozirdan keyingi xabarlar): " + (", ".join(chats.go_live()) or "guruh yo'q"))
    for chat_id, info in chats.groups().items():
        state = "PAUZA" if info.get("paused") else "ishlayapti"
        print(f"  {chat_id} {info.get('title')}: {state}, xabarlar hisobi {info.get('live_since') or info.get('added_at')} dan")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docbot", description="Telegram hujjat rasmlari -> Odoo")
    parser.add_argument("--version", action="version", version=f"docbot {__version__}")
    parser.add_argument(
        "--home", type=Path, default=None, help="loyiha papkasi (standart: DOCBOT_HOME, joriy papka yoki o'rnatilgan joy)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check-config", help="konfiguratsiyani tekshirish (tarmoqsiz)")
    check.add_argument("--strict", action="store_true", help="placeholder qolgan bo'lsa xato kodi bilan tugash")
    check.set_defaults(func=cmd_check_config)

    migrate = sub.add_parser("migrate", help="DB sxemasini oxirgi versiyaga yangilash")
    migrate.set_defaults(func=cmd_migrate)

    run = sub.add_parser("run", help="botni ishga tushirish (to'xtatish: Ctrl+C)")
    run.set_defaults(func=cmd_run)

    status = sub.add_parser("status", help="navbat holati: nechta job qaysi holatda, kutayotgan savollar va xatolar")
    status.add_argument("--limit", type=int, default=20, help="nechta savol/xato ko'rsatilsin")
    status.set_defaults(func=cmd_status)

    requeue = sub.add_parser("requeue", help="failed / aniqlanmagan fayllarni qayta ishlash (masalan tuzatishdan keyin)")
    requeue.add_argument("--failed", action="store_true", help="xato bilan tugaganlar")
    requeue.add_argument("--review", action="store_true", help="aniqlanmagan (tiniqroq so'ralgan) fayllar")
    requeue.add_argument("--ids", default="", help="aniq joblar: 1,2,3")
    requeue.set_defaults(func=cmd_requeue)

    groups = sub.add_parser("groups", help="guruhlar: ro'yxat, pauza yoki ishga tushirish (ishlab turgan bot darhol ko'radi)")
    groups.add_argument("action", nargs="?", choices=["list", "pause", "live"], default="list")
    groups.set_defaults(func=cmd_groups)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")  # a redirected Windows console must not crash on non-cp1251 text
    args = build_parser().parse_args(argv)
    return args.func(args)
