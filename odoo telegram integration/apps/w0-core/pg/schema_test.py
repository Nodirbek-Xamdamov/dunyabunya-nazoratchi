"""Functional checks of schema v1 on a throwaway database (W0-04).

Covers: DEFAULT partition + ensure_month_partitions (no silent loss), Tashkent month/day boundary,
P-05 retention trigger + purge, W4 manifest import (real manifest files, idempotent), every api view
readable by w3_dashboard. Prints PASS/FAIL lines; exit 1 on any failure.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pgtool import psql

DB = sys.argv[1] if len(sys.argv) > 1 else "genesis_migtest"
VIDEO_ROOT = Path(r"E:\data\video")
results: list[tuple[str, bool, str]] = []


def q(sql: str, user: str = "genesis_migrator") -> str:
    return psql(sql, user=user, db=DB).stdout.strip()


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def main() -> int:
    # fixtures
    q("""INSERT INTO core.sources (kind, tg_chat_id, tg_username, title) VALUES
         ('supergroup', -1001234567890, NULL, 'Sinov guruh'), ('supergroup', -1009876543210, 'testpublic', 'Public');
         INSERT INTO core.people (tg_user_id, display_name, role) VALUES (111, 'Hodim A', 'employee'), (222, 'Mijoz B', 'client');""",
      user="w0_admin")
    src = int(q("SELECT id FROM core.sources WHERE tg_chat_id = -1001234567890;", user="w0_admin"))

    # 1. month boundary: 2026-10-31 19:30 UTC = 2026-11-01 00:30 Tashkent -> November partition
    q(f"INSERT INTO core.messages (source_id, tg_message_id, sent_at, sender_tg_id, text) VALUES "
      f"({src}, 1, '2026-10-31 19:30+00', 222, 'salom, narx qancha?'), "
      f"({src}, 2, '2026-10-31 19:31+00', 111, 'assalomu alaykum'), "
      f"({src}, 3, '2026-10-31 18:59+00', 222, 'oktabr oxiri');", user="w1_ingest")
    part = q("SELECT tableoid::regclass || ' ' || local_day FROM core.messages WHERE tg_message_id IN (1, 3) ORDER BY 1;",
             user="w0_admin")
    check("Tashkent boundary: 19:30 UTC -> November partition, local_day 2026-11-01",
          "messages_y2026m11 2026-11-01" in part and "messages_y2026m10 2026-10-31" in part, part.replace("\n", " | "))

    # 2. idempotent insert
    r = psql(f"INSERT INTO core.messages (source_id, tg_message_id, sent_at, text) VALUES ({src}, 1, '2026-10-31 19:30+00', 'x') "
             f"ON CONFLICT (source_id, tg_message_id, sent_at) DO NOTHING;", user="w1_ingest", db=DB)
    cnt = q("SELECT count(*) FROM core.messages;", user="w0_admin")
    check("idempotent key (source_id, tg_message_id, sent_at)", r.returncode == 0 and cnt == "3", f"rows={cnt}")

    # 3. month without partition -> DEFAULT, signal, then ensure_month_partitions moves it
    q(f"INSERT INTO core.messages (source_id, tg_message_id, sent_at, text) VALUES ({src}, 900, '2025-01-15 10:00+00', 'eski tarix');",
      user="w1_ingest")
    where = q("SELECT tableoid::regclass FROM core.messages WHERE tg_message_id = 900;", user="w0_admin")
    health = q("SELECT default_partition_rows FROM api.v_system_health;", user="w3_dashboard")
    check("no partition -> row kept in DEFAULT and visible in v_system_health", where == "messages_default" and health == "1",
          f"{where}, default_partition_rows={health}")
    out = json.loads(q("SELECT core.ensure_month_partitions();", user="w0_admin"))
    moved = {c["partition"]: c["moved_rows"] for c in out["created"]}
    where2 = q("SELECT tableoid::regclass FROM core.messages WHERE tg_message_id = 900;", user="w0_admin")
    health2 = q("SELECT default_partition_rows FROM api.v_system_health;", user="w3_dashboard")
    check("ensure_month_partitions creates 2025-01 and moves the row out of DEFAULT",
          moved.get("messages_y2025m01") == 1 and where2 == "messages_y2025m01" and health2 == "0",
          f"created={moved} now in {where2}, default={health2}")
    again = json.loads(q("SELECT core.ensure_month_partitions();", user="w0_admin"))
    check("ensure_month_partitions is idempotent", again["created"] == [], json.dumps(again))

    # 4. retention P-05: relevance=no -> retain_until = ended_at + 72 h; purge after that
    q(f"""WITH e AS (
            INSERT INTO core.episodes (source_id, started_at, ended_at, first_tg_message_id, last_tg_message_id, message_count, relevance)
            VALUES ({src}, '2026-10-31 18:59+00', '2026-10-31 19:31+00', 3, 2, 3, 'no') RETURNING id)
          INSERT INTO core.episode_messages (episode_id, source_id, tg_message_id, sent_at)
          SELECT e.id, m.source_id, m.tg_message_id, m.sent_at FROM e, core.messages m WHERE m.tg_message_id IN (1, 2, 3);""",
      user="w2_analyst")
    ru = q("SELECT retain_until FROM core.episodes;", user="w0_admin")
    check("retention trigger: retain_until = ended_at + 72h", ru.startswith("2026-11-03 19:31") or ru.startswith("2026-11-04 00:31"), ru)
    early = json.loads(q("SELECT core.purge_unrelated_text('2026-11-02 00:00+00');", user="w0_admin"))
    late = json.loads(q("SELECT core.purge_unrelated_text('2026-11-05 00:00+00');", user="w0_admin"))
    texts = q("SELECT count(*) FILTER (WHERE text IS NULL AND text_purged_at IS NOT NULL) FROM core.messages WHERE tg_message_id IN (1,2,3);",
              user="w0_admin")
    check("purge_unrelated_text: nothing before retain_until, all 3 messages after",
          early["messages"] == 0 and late["messages"] == 3 and texts == "3", f"early={early} late={late}")
    q("UPDATE core.episodes SET relevance = 'yes';", user="w2_analyst")
    check("relevance yes -> retain_until NULL", q("SELECT coalesce(retain_until::text, 'NULL') FROM core.episodes;", user="w0_admin") == "NULL")

    # 5. W4 manifest import (real files), twice -> idempotent
    lines = []
    for f in sorted(VIDEO_ROOT.glob("*/*/manifest.jsonl")):
        lines += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    sql = "\n".join(f"SELECT core.import_manifest_record('{json.dumps(rec).replace(chr(39), chr(39) * 2)}'::jsonb, "
                    f"'{VIDEO_ROOT}');" for rec in lines)
    for _ in range(2):
        psql(sql, user="w4_camera", db=DB)
    segs = int(q("SELECT count(*) FROM core.video_segments;", user="w0_admin"))
    gaps = int(q("SELECT count(*) FROM core.video_gaps;", user="w0_admin"))
    want_s = sum(1 for r in lines if r["type"] == "segment")
    want_g = sum(1 for r in lines if r["type"] == "gap")
    check("manifest import (real W4 files, imported twice) is idempotent",
          lines and segs == want_s and gaps == want_g, f"lines={len(lines)} segments={segs}/{want_s} gaps={gaps}/{want_g}")
    cov = q("SELECT camera_id || ' ' || local_day || ' segments=' || segments || ' recorded_s=' || round(recorded_s) || "
            "' gaps=' || gaps || ' coverage=' || coverage_pct || '%' FROM api.v_camera_coverage ORDER BY local_day;",
            user="w3_dashboard")
    check("v_camera_coverage reads imported segments", bool(cov), cov.replace("\n", " | "))

    # 6. every api view is readable by w3_dashboard
    views = q("SELECT string_agg(viewname, ' ' ORDER BY viewname) FROM pg_views WHERE schemaname = 'api';", user="w0_admin").split()
    bad = [v for v in views if psql(f"SELECT count(*) FROM api.{v};", user="w3_dashboard", db=DB, check=False).returncode != 0]
    check(f"w3_dashboard can SELECT all {len(views)} api views", not bad, f"failed: {bad}")
    link = q("SELECT tg_link FROM api.v_messages_public WHERE tg_message_id = 1;", user="w3_dashboard")
    check("tg_link built for -100 chat id", link == "https://t.me/c/1234567890/1", link)

    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""))
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
