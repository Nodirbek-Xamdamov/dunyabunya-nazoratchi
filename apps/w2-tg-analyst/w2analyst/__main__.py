"""CLI (from apps/w2-tg-analyst):

    .venv\\Scripts\\python.exe -E -m w2analyst segment --db genesis_test [--source 12 --source 13]
    .venv\\Scripts\\python.exe -E -m w2analyst render --db genesis_test --episode 41
"""
from __future__ import annotations

import argparse
import json

from genesis_common import load_config
from genesis_queue import connect

from .render import episode_text
from .segmenter import SegConfig
from .store import run

APP = "w2-tg-analyst"


def main() -> int:
    ap = argparse.ArgumentParser(prog="w2analyst")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("segment", help="W2-04: assign pending messages to episodes")
    s.add_argument("--db", required=True)
    s.add_argument("--source", type=int, action="append")
    s.add_argument("--prod", action="store_true", help="allow the production database (from W2-05 on)")
    r = sub.add_parser("render", help="print the model text of one episode")
    r.add_argument("--db", required=True)
    r.add_argument("--episode", type=int, required=True)
    a = ap.parse_args()

    cfg = load_config(APP)
    seg_cfg = SegConfig.from_app(cfg["app"])
    role = cfg["app"]["segmenter"].get("db_role", "w2_analyst")
    if a.cmd == "segment":
        if a.db == cfg["postgres"]["database"] and not a.prod:
            raise SystemExit(f"refusing to write to the production database '{a.db}' without --prod")
        with connect(role, a.db, cfg) as conn:
            print(json.dumps(run(conn, seg_cfg, a.source).as_dict()))
    else:
        with connect(role, a.db, cfg) as conn:
            print(episode_text(conn, a.episode))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
