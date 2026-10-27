"""W2-04 database test: ONLY genesis_test. Run from apps/w2-tg-analyst:
    .venv\\Scripts\\python.exe -E tests\\db_test.py

Setup and cleanup use w0_admin and touch only its own sources (tg_chat_id -1009904xxxxxx) and people
(tg ids 9904xxxx); segmentation runs as w2_analyst exactly like production. W3 fixture data is not changed.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve()
APP = HERE.parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(HERE.parent))

from genesis_queue import connect  # noqa: E402
from scenarios import PEOPLE, S, build  # noqa: E402
from w2analyst.render import episode_text  # noqa: E402
from w2analyst.segmenter import Msg, SegConfig, Segmenter, segment  # noqa: E402
from w2analyst.store import run  # noqa: E402

DB = "genesis_test"
CHAT_BASE = -1009904000000
UID = 9904000
CFG = SegConfig()
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""), flush=True)


def uid(u: int | None) -> int | None:
    return None if u is None else UID + u


def cleanup(adm) -> None:
    src = f"(SELECT id FROM core.sources WHERE tg_chat_id BETWEEN {CHAT_BASE - 1000} AND {CHAT_BASE - 1})"
    for t in ["episode_messages", "episodes", "transcripts", "media", "messages"]:
        adm.execute(f"DELETE FROM core.{t} WHERE source_id IN {src}")
    adm.execute(f"DELETE FROM core.sources WHERE tg_chat_id BETWEEN {CHAT_BASE - 1000} AND {CHAT_BASE - 1}")
    adm.execute(f"DELETE FROM core.people WHERE tg_user_id BETWEEN {UID} AND {UID + 999}")


def insert_msg(adm, src: int, m: Msg, text: str | None = None, deleted: bool = False) -> None:
    adm.execute("INSERT INTO core.messages (source_id, tg_message_id, sent_at, sender_tg_id, sender_name, sender_is_bot, "
                "kind, text, reply_to_tg_message_id, thread_id, deleted_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (src, m.tg_message_id, m.sent_at, uid(m.sender_tg_id), None, m.sender_is_bot, m.kind,
                 text if text is not None else f"msg {m.tg_message_id}", m.reply_to, m.thread_id,
                 m.sent_at if deleted else None))


def db_groups(w2, sources: list[int]) -> list[list[int]]:
    rows = w2.execute("SELECT e.id, em.tg_message_id FROM core.episodes e JOIN core.episode_messages em ON em.episode_id = e.id "
                      "WHERE e.source_id = ANY(%s) AND e.segmenter_version = %s ORDER BY e.source_id, e.started_at, "
                      "e.first_tg_message_id, em.sent_at, em.tg_message_id", (sources, CFG.version)).fetchall()
    out: dict[int, list[int]] = {}
    for ep, mid in rows:
        out.setdefault(ep, []).append(mid)
    return list(out.values())


def fingerprint(w2, sources: list[int]) -> str:
    rows = w2.execute("SELECT e.id, e.source_id, e.started_at, e.ended_at, e.first_tg_message_id, e.last_tg_message_id, "
                      "e.message_count, e.participant_tg_ids, e.updated_at, "
                      "(SELECT array_agg(tg_message_id ORDER BY tg_message_id) FROM core.episode_messages WHERE episode_id = e.id) "
                      "FROM core.episodes e WHERE e.source_id = ANY(%s) AND e.segmenter_version = %s ORDER BY e.id",
                      (sources, CFG.version)).fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()[:16]


def main() -> int:
    adm = connect("w0_admin", DB)
    w2 = connect("w2_analyst", DB)
    if adm.execute("SELECT current_database()").fetchone()[0] != "genesis_test":
        raise SystemExit("refusing: not genesis_test")
    try:
        cleanup(adm)
        fixture_before = adm.execute("SELECT segmenter_version, count(*) FROM core.episodes GROUP BY 1 ORDER BY 1").fetchall()
        for name, (u, role) in PEOPLE.items():
            if u is not None:
                adm.execute("INSERT INTO core.people (tg_user_id, display_name, role) VALUES (%s, %s, %s)",
                            (uid(u), f"TEST {name}", "unknown" if role is None else role))
        # one source per scenario
        sources: dict[str, int] = {}
        expected_msgs: list[Msg] = []
        for n, name in enumerate(S):
            sources[name] = adm.execute("INSERT INTO core.sources (kind, tg_chat_id, title, reader) VALUES "
                                        "('supergroup', %s, %s, 'w2-04 test') RETURNING id",
                                        (CHAT_BASE - 1 - n, f"TEST W2-04 {name}")).fetchone()[0]
            msgs, _ = build(name)
            for m in msgs:
                m = Msg(**{**m.__dict__, "source_id": sources[name]})
                insert_msg(adm, sources[name], m, deleted=name.startswith("S16") and m.tg_message_id == 1001)
                expected_msgs.append(m)
        src_ids = list(sources.values())
        # voice + transcript in S14 (row 0), photo caption row 1
        s14 = sources["S14 voice va rasm albomi"]
        m0 = adm.execute("SELECT sent_at FROM core.messages WHERE source_id = %s AND tg_message_id = 1000", (s14,)).fetchone()[0]
        media_id = adm.execute("INSERT INTO core.media (source_id, tg_message_id, message_sent_at, kind, status, duration_s) "
                               "VALUES (%s, 1000, %s, 'voice', 'downloaded', 42) RETURNING id", (s14, m0)).fetchone()[0]
        adm.execute("INSERT INTO core.transcripts (media_id, provider, model, message_sent_at, source_id, tg_message_id, "
                    "language, text) VALUES (%s, 'test', 'none', %s, %s, 1000, 'uz', 'Kafel siniq keldi, rasm tashladim')",
                    (media_id, m0, s14))

        def expected(msgs: list[Msg]) -> list[list[int]]:
            return [[m.tg_message_id for m in e.msgs] for e in segment(msgs, CFG)]

        # 1. first run equals the offline batch result
        st = run(w2, CFG, src_ids)
        check("1 first run == offline segment()", db_groups(w2, src_ids) == expected(expected_msgs),
              f"{st.as_dict()}")
        n_seg = sum(1 for m in expected_msgs if m.kind != "service" and not m.sender_is_bot)
        check("1b every segmentable message assigned once", st.messages_assigned == n_seg and
              w2.execute("SELECT count(*) FROM core.episode_messages WHERE source_id = ANY(%s)", (src_ids,)).fetchone()[0] == n_seg,
              f"{st.messages_assigned}/{n_seg}")
        check("1c episodes never cross Tashkent midnight", w2.execute(
            "SELECT count(*) FROM core.episodes WHERE source_id = ANY(%s) AND core.tashkent_day(started_at) <> core.tashkent_day(ended_at)",
            (src_ids,)).fetchone()[0] == 0)

        # 2. rerun: nothing changes
        fp1 = fingerprint(w2, src_ids)
        st2 = run(w2, CFG, src_ids)
        check("2 rerun is a no-op (same fingerprint)", st2.as_dict() == {"days": 0, "messages_assigned": 0,
              "episodes_created": 0, "episodes_updated": 0} and fingerprint(w2, src_ids) == fp1, f"fp={fp1}")

        # 3. incremental: continuation, new episode, late message, reply to old episode
        s02 = sources["S02 2 soat oraliqli ikki mavzu"]
        base = [m for m in expected_msgs if m.source_id == s02]
        t = base[-1].sent_at
        more = [Msg(source_id=s02, tg_message_id=1010, sent_at=t + timedelta(minutes=10), sender_tg_id=101, sender_role="client"),
                Msg(source_id=s02, tg_message_id=1011, sent_at=t + timedelta(hours=2), sender_tg_id=102, sender_role="client"),
                Msg(source_id=s02, tg_message_id=1012, sent_at=base[0].sent_at + timedelta(minutes=2), sender_tg_id=102,
                    sender_role="client"),  # late (out of order): inside the first episode
                Msg(source_id=s02, tg_message_id=1013, sent_at=t + timedelta(hours=2, minutes=5), sender_tg_id=201,
                    sender_role="employee", reply_to=1000)]  # reply to 09:10 message
        for m in more:
            insert_msg(adm, s02, m)
        seg = Segmenter(CFG)  # expected = replay stored + add new in arrival (time) order, as the writer does
        for i, ep in enumerate(segment(base, CFG)):
            seg.load(ep.msgs, db_id=i + 1)
        for m in sorted(more, key=lambda x: x.order):
            seg.add(m)
        want = [[m.tg_message_id for m in e.msgs] for e in seg.episodes]
        st3 = run(w2, CFG, [s02])
        check("3 incremental run == replay rule", db_groups(w2, [s02]) == want, f"{want} {st3.as_dict()}")
        check("3b stored episodes only extended (no orphans)", w2.execute(
            "SELECT count(*) FROM core.episodes e WHERE source_id = %s AND NOT EXISTS "
            "(SELECT 1 FROM core.episode_messages em WHERE em.episode_id = e.id)", (s02,)).fetchone()[0] == 0
            and w2.execute("SELECT bool_and(message_count = (SELECT count(*) FROM core.episode_messages em WHERE em.episode_id = e.id)) "
                           "FROM core.episodes e WHERE source_id = %s", (s02,)).fetchone()[0])
        fp3 = fingerprint(w2, src_ids)
        check("3c rerun after incremental is a no-op", run(w2, CFG, src_ids).messages_assigned == 0
              and fingerprint(w2, src_ids) == fp3)

        # 4. episode text: voice transcript in place of the message
        ep14 = w2.execute("SELECT episode_id FROM core.episode_messages WHERE source_id = %s AND tg_message_id = 1000",
                          (s14,)).fetchone()[0]
        text = episode_text(w2, ep14)
        print(text)
        check("4 render: voice -> transcript, roles, ids", "[voice 0:42] Kafel siniq keldi, rasm tashladim" in text
              and "[CLIENT] TEST C1:" in text and "[EMPLOYEE] TEST E1:" in text and "#1000]" in text
              and len(text.splitlines()) == 4)

        # 5. other data untouched, rights as planned
        fixture_after = adm.execute("SELECT segmenter_version, count(*) FROM core.episodes WHERE source_id <> ALL(%s) "
                                    "GROUP BY 1 ORDER BY 1", (src_ids,)).fetchall()
        check("5 fixture episodes untouched", fixture_after == fixture_before, f"{fixture_after}")
        try:
            w2.execute("DELETE FROM core.episodes WHERE id = -1")
            denied = False
        except Exception as e:  # noqa: BLE001
            denied = "permission denied" in str(e)
        check("5b w2_analyst has no DELETE (writer does not need it)", denied)

        # 6. CLI refuses the production database without --prod (refuses before connecting)
        p = subprocess.run([sys.executable, "-E", "-m", "w2analyst", "segment", "--db", "genesis"], cwd=APP,
                           capture_output=True, text=True)
        check("6 CLI refuses production DB", p.returncode != 0 and "refusing" in p.stderr, p.stderr.strip()[-80:])
    finally:
        cleanup(adm)
        left = adm.execute(f"SELECT count(*) FROM core.sources WHERE tg_chat_id BETWEEN {CHAT_BASE - 1000} AND {CHAT_BASE - 1}").fetchone()[0]
        print(f"cleanup: test sources left = {left}")
        adm.close()
        w2.close()
    failed = [r for r in results if not r[1]]
    print(f"\nW2-04 db_test: {len(results) - len(failed)}/{len(results)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
