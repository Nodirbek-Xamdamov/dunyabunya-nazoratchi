"""W2-04 database side of segmentation (role w2_analyst: messages r, episodes/episode_messages rw, no DELETE).

For every (source, Tashkent day) that has messages not yet in an episode of this segmenter version:
lock the source, replay the stored episodes of that day, add the new messages in time order, write back.
Stored episodes are only extended (UPDATE), never merged, split or deleted (see segmenter R7), so a rerun
with no new messages changes nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from .segmenter import Msg, SegConfig, Segmenter

LOCK_CLASS = 72040  # pg_advisory_xact_lock(LOCK_CLASS, source_id): one writer per source

PENDING_SQL = """
SELECT m.source_id, m.local_day, count(*)
FROM core.messages m
WHERE m.kind <> 'service' AND NOT m.sender_is_bot
  AND (%(sources)s::bigint[] IS NULL OR m.source_id = ANY(%(sources)s::bigint[]))
  AND NOT EXISTS (SELECT 1 FROM core.episode_messages em JOIN core.episodes e ON e.id = em.episode_id
                  WHERE em.source_id = m.source_id AND em.tg_message_id = m.tg_message_id
                    AND e.segmenter_version = %(version)s)
GROUP BY 1, 2 ORDER BY 2, 1
"""

DAY_MESSAGES_SQL = """
SELECT m.source_id, m.tg_message_id, m.sent_at, m.sender_tg_id, m.sender_is_bot, m.kind,
       m.reply_to_tg_message_id, m.thread_id, em.episode_id, p.role
FROM core.messages m
LEFT JOIN core.people p ON p.tg_user_id = m.sender_tg_id
LEFT JOIN LATERAL (SELECT em.episode_id FROM core.episode_messages em JOIN core.episodes e ON e.id = em.episode_id
                   WHERE em.source_id = m.source_id AND em.tg_message_id = m.tg_message_id
                     AND e.segmenter_version = %(version)s) em ON true
WHERE m.source_id = %(source)s AND m.local_day = %(day)s
ORDER BY m.sent_at, m.tg_message_id
"""

INSERT_EPISODE_SQL = """
INSERT INTO core.episodes (source_id, started_at, ended_at, first_tg_message_id, last_tg_message_id, message_count,
                           participant_tg_ids, segmenter_version)
VALUES (%(source_id)s, %(started_at)s, %(ended_at)s, %(first_tg_message_id)s, %(last_tg_message_id)s,
        %(message_count)s, %(participant_tg_ids)s, %(version)s)
RETURNING id
"""

UPDATE_EPISODE_SQL = """
UPDATE core.episodes SET started_at = %(started_at)s, ended_at = %(ended_at)s,
       first_tg_message_id = %(first_tg_message_id)s, last_tg_message_id = %(last_tg_message_id)s,
       message_count = %(message_count)s, participant_tg_ids = %(participant_tg_ids)s, updated_at = now()
WHERE id = %(id)s AND segmenter_version = %(version)s
"""

INSERT_MEMBER_SQL = """
INSERT INTO core.episode_messages (episode_id, source_id, tg_message_id, sent_at) VALUES (%s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""


@dataclass
class RunStats:
    days: int = 0
    messages_assigned: int = 0
    episodes_created: int = 0
    episodes_updated: int = 0
    touched_episode_ids: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"days": self.days, "messages_assigned": self.messages_assigned,
                "episodes_created": self.episodes_created, "episodes_updated": self.episodes_updated}


def pending(conn: Any, cfg: SegConfig, sources: Iterable[int] | None = None) -> list[tuple[int, date, int]]:
    src = list(sources) if sources is not None else None
    return [tuple(r) for r in conn.execute(PENDING_SQL, {"sources": src, "version": cfg.version}).fetchall()]


def segment_day(conn: Any, cfg: SegConfig, source_id: int, day: date, stats: RunStats | None = None) -> RunStats:
    stats = stats or RunStats()
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_CLASS, int(source_id)))
        rows = conn.execute(DAY_MESSAGES_SQL, {"source": source_id, "day": day, "version": cfg.version}).fetchall()
        seg = Segmenter(cfg)
        stored: dict[int, list[Msg]] = {}
        new: list[Msg] = []
        for r in rows:
            m = Msg(source_id=r[0], tg_message_id=r[1], sent_at=r[2], sender_tg_id=r[3], sender_is_bot=r[4],
                    kind=r[5], reply_to=r[6], thread_id=r[7], sender_role=r[9])
            (stored.setdefault(r[8], []) if r[8] is not None else new).append(m)
        for ep_id, msgs in sorted(stored.items(), key=lambda kv: min(m.order for m in kv[1])):
            seg.load(msgs, db_id=ep_id)
        added: dict[int, list[Msg]] = {}
        for m in new:  # rows are in time order already
            ep = seg.add(m)
            if ep is not None:
                added.setdefault(id(ep), []).append(m)
        for ep in seg.episodes:
            if not ep.dirty:
                continue
            row = ep.as_row() | {"version": cfg.version}
            if ep.db_id is None:
                ep.db_id = conn.execute(INSERT_EPISODE_SQL, row).fetchone()[0]
                stats.episodes_created += 1
            else:
                conn.execute(UPDATE_EPISODE_SQL, row | {"id": ep.db_id})
                stats.episodes_updated += 1
            for m in added.get(id(ep), []):
                conn.execute(INSERT_MEMBER_SQL, (ep.db_id, m.source_id, m.tg_message_id, m.sent_at))
                stats.messages_assigned += 1
            ep.dirty = False
            stats.touched_episode_ids.append(ep.db_id)
    stats.days += 1
    return stats


def run(conn: Any, cfg: SegConfig, sources: Iterable[int] | None = None) -> RunStats:
    """Segment everything pending (optionally only `sources`). `conn` is an autocommit w2_analyst connection."""
    stats = RunStats()
    for source_id, day, _ in pending(conn, cfg, sources):
        segment_day(conn, cfg, source_id, day, stats)
    return stats
