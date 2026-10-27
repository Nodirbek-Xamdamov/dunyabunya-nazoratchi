"""W2-04 segmentation rules, offline (no database). Run from apps/w2-tg-analyst:
    .venv\\Scripts\\python.exe -E -m unittest discover -s tests -v
"""
from __future__ import annotations

import random
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenarios import S, build, partition  # noqa: E402
from w2analyst.segmenter import TZ, Msg, SegConfig, Segmenter, segment  # noqa: E402


def at(hhmm: str, day: int = 20) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(2026, 10, day, h, m, tzinfo=TZ)


def msg(i: int, hhmm: str, who: int = 101, role: str | None = "client", **kw) -> Msg:
    return Msg(source_id=kw.pop("source_id", 1), tg_message_id=i, sent_at=kw.pop("sent_at", None) or at(hhmm),
               sender_tg_id=who, sender_role=role, **kw)


def groups(eps) -> list[list[int]]:
    return [[m.tg_message_id for m in e.msgs] for e in eps]


def random_day(seed: int, n: int = 400, sources: int = 3) -> list[Msg]:
    rnd = random.Random(seed)
    out, t = [], at("07:00")
    for i in range(n):
        t += timedelta(seconds=rnd.choice([5, 30, 120, 600, 1700, 1900, 3000, 4000, 9000]))
        who = rnd.choice([101, 102, 201, 202, None])
        role = {101: "client", 102: "client", 201: "employee", 202: "employee"}.get(who)
        reply = 2000 + rnd.randrange(i) if i and rnd.random() < 0.2 else None
        out.append(Msg(source_id=1 + i % sources, tg_message_id=2000 + i, sent_at=t, sender_tg_id=who, sender_role=role,
                       reply_to=reply, thread_id=rnd.choice([None, None, None, 7]),
                       kind=rnd.choice(["text"] * 8 + ["voice", "service"]), sender_is_bot=rnd.random() < 0.03))
    return out


class Acceptance(unittest.TestCase):
    """20 hand-labelled synthetic conversations; plan: at least 90 % segmented correctly."""

    def test_scenarios(self):
        ok, report = 0, []
        for name in S:
            msgs, truth = build(name)
            eps = segment(msgs)
            pos = {m.tg_message_id: i for i, m in enumerate(msgs)}
            got = {frozenset(pos[m.tg_message_id] for m in e.msgs) for e in eps}
            good = got == partition(truth)
            ok += good
            report.append(f"{'OK  ' if good else 'MISS'} {name}")
        print("\n" + "\n".join(report) + f"\nW2-04 acceptance: {ok}/{len(S)} = {ok / len(S):.0%}")
        self.assertEqual(len(S), 20)
        self.assertGreaterEqual(ok / len(S), 0.90)


class Rules(unittest.TestCase):
    def test_r1_input_order_does_not_matter(self):
        msgs = random_day(1)
        ref = groups(segment(msgs))
        for seed in range(30):
            shuffled = msgs[:]
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(groups(segment(shuffled)), ref)

    def test_streaming_in_time_order_equals_batch(self):
        for seed in range(5):
            msgs = sorted(random_day(seed), key=lambda m: (m.source_id, m.order))
            ref = groups(segment(msgs))
            # stream in chunks, rebuilding state from "stored" episodes each time (what the DB writer does)
            stored: list[list[Msg]] = []
            chunk = 37
            for k in range(0, len(msgs), chunk):
                seg = Segmenter()
                for n, ep_msgs in enumerate(stored):
                    seg.load(ep_msgs, db_id=n + 1)
                for m in msgs[k:k + chunk]:
                    seg.add(m)
                stored = [e.msgs[:] for e in seg.episodes]
            self.assertEqual([[m.tg_message_id for m in e] for e in stored], ref)

    def test_add_is_idempotent(self):
        seg = Segmenter()
        a = seg.add(msg(1, "10:00"))
        self.assertIs(seg.add(msg(1, "10:00")), a)
        self.assertEqual(len(a.msgs), 1)

    def test_r2_service_and_bots_skipped(self):
        seg = Segmenter()
        self.assertIsNone(seg.add(msg(1, "10:00", kind="service")))
        self.assertIsNone(seg.add(msg(2, "10:00", sender_is_bot=True)))
        self.assertEqual(seg.episodes, [])

    def test_r3_reply_window(self):
        eps = segment([msg(1, "08:00"), msg(2, "19:59", who=201, role="employee", reply_to=1)])
        self.assertEqual(groups(eps), [[1, 2]])  # 11:59 later: inside 12 h
        eps = segment([msg(1, "08:00"), msg(2, "20:01", who=201, role="employee", reply_to=1)],
                      SegConfig(reply_window_hours=12))
        self.assertEqual(groups(eps), [[1], [2]])

    def test_r3_reply_to_unknown_message_uses_time_rules(self):
        self.assertEqual(groups(segment([msg(1, "10:00"), msg(2, "10:10", reply_to=999)])), [[1, 2]])
        self.assertEqual(groups(segment([msg(1, "10:00"), msg(2, "12:10", reply_to=999)])), [[1], [2]])

    def test_r3_reply_across_midnight_starts_new_episode(self):
        eps = segment([msg(1, "23:00"), msg(2, "00:30", sent_at=at("00:30", 21), reply_to=1)])
        self.assertEqual(groups(eps), [[1], [2]])

    def test_midnight_is_tashkent_not_utc(self):
        # 23:59 and 00:01 Tashkent = 18:59 / 19:01 UTC: split. 04:59 / 05:01 Tashkent = UTC midnight: no split.
        self.assertEqual(groups(segment([msg(1, "23:59"), msg(2, "00:01", sent_at=at("00:01", 21))])), [[1], [2]])
        self.assertEqual(groups(segment([msg(1, "04:59"), msg(2, "05:01")])), [[1, 2]])

    def test_r4_gap_boundary(self):
        self.assertEqual(groups(segment([msg(1, "10:00"), msg(2, "10:30", who=102)])), [[1, 2]])
        self.assertEqual(groups(segment([msg(1, "10:00"), msg(2, "10:31", who=102)])), [[1], [2]])

    def test_r5_extended_gap(self):
        base = [msg(1, "10:00"), msg(2, "10:05", who=201, role="employee")]
        self.assertEqual(groups(segment(base + [msg(3, "11:05")])), [[1, 2, 3]])            # participant returns
        self.assertEqual(groups(segment(base + [msg(3, "11:06")])), [[1, 2], [3]])          # > 60 min
        self.assertEqual(groups(segment(base + [msg(3, "10:50", who=102)])), [[1, 2], [3]])  # newcomer client
        self.assertEqual(groups(segment([msg(1, "10:00"), msg(2, "10:50")])), [[1], [2]])    # same sender again
        q = [msg(1, "10:00")]
        self.assertEqual(groups(segment(q + [msg(2, "10:45", who=201, role="employee")])), [[1, 2]])  # staff answer
        self.assertEqual(groups(segment(q + [msg(2, "10:45", who=301, role="unknown")])), [[1], [2]])
        self.assertEqual(groups(segment(q + [msg(2, "10:45", who=301, role=None)])), [[1], [2]])

    def test_r6_max_messages(self):
        msgs = [msg(i, "", sent_at=at("10:00") + timedelta(seconds=5 * i)) for i in range(205)]
        self.assertEqual([len(g) for g in groups(segment(msgs, SegConfig(max_messages=200)))], [200, 5])

    def test_r6_max_duration(self):
        msgs = [msg(i, "", who=101 + i % 2, sent_at=at("08:00") + timedelta(minutes=20 * i)) for i in range(25)]
        eps = segment(msgs, SegConfig(max_episode_hours=6))
        self.assertTrue(all(e.ended_at - e.started_at <= timedelta(hours=6) for e in eps))
        self.assertEqual([len(g) for g in groups(eps)], [19, 6])

    def test_r7_late_messages(self):
        def late(stored: list[Msg], new: Msg) -> list[list[int]]:
            seg = Segmenter()
            for n, ep in enumerate(segment(stored)):
                seg.load(ep.msgs, db_id=n + 1)
            seg.add(new)
            return groups(seg.episodes)

        two = [msg(1, "10:00"), msg(2, "10:20"), msg(3, "11:30"), msg(4, "11:40")]
        self.assertEqual(late(two, msg(9, "10:10")), [[1, 9, 2], [3, 4]])        # inside
        self.assertEqual(late(two, msg(9, "11:05")), [[1, 2], [9, 3, 4]])        # just before the next one
        self.assertEqual(late(two, msg(9, "10:45")), [[1, 2, 9], [3, 4]])        # bridges: joins previous, no merge
        self.assertEqual(late(two, msg(9, "09:00")), [[9], [1, 2], [3, 4]])      # far: new episode

    def test_topics_are_separate(self):
        eps = segment([msg(1, "10:00", thread_id=5), msg(2, "10:01", thread_id=7), msg(3, "10:02")])
        self.assertEqual(groups(eps), [[1], [2], [3]])

    def test_sources_are_separate(self):
        eps = segment([msg(1, "10:00", source_id=1), msg(1, "10:01", source_id=2)])
        self.assertEqual(len(eps), 2)

    def test_same_second_album_order_by_id(self):
        t = at("10:00")
        eps = segment([msg(12, "", sent_at=t, kind="photo"), msg(11, "", sent_at=t, kind="photo")])
        self.assertEqual(groups(eps), [[11, 12]])
        self.assertEqual(eps[0].first_id, 11)

    def test_row_fields(self):
        ep = segment([msg(1, "10:00"), msg(2, "10:05", who=201, role="employee"), msg(3, "10:06", who=None, role=None)])[0]
        row = ep.as_row()
        self.assertEqual((row["first_tg_message_id"], row["last_tg_message_id"], row["message_count"]), (1, 3, 3))
        self.assertEqual(row["participant_tg_ids"], [101, 201])

    def test_naive_datetime_rejected(self):
        with self.assertRaises(ValueError):
            Segmenter().add(Msg(source_id=1, tg_message_id=1, sent_at=datetime(2026, 10, 20, 10, 0)))

    def test_load_rejects_cross_day_episode(self):
        with self.assertRaises(ValueError):
            Segmenter().load([msg(1, "23:50"), msg(2, "", sent_at=at("00:10", 21))], db_id=1)


if __name__ == "__main__":
    unittest.main()
