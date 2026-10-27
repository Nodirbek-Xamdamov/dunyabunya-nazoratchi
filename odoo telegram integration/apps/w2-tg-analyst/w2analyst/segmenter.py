"""W2-04 conversation segmentation: rule based, no LLM, deterministic.

Messages of one source are split into episodes. Rules (details and edge cases: SEGMENTER.md):
  R1 order by (sent_at, tg_message_id); episodes never cross the Tashkent midnight or a forum topic (thread).
  R2 service messages and bot messages are not part of any episode.
  R3 a reply joins the episode of the message it replies to (same day/topic, within reply_window).
  R4 otherwise the episode of the previous message if the gap <= gap_minutes ...
  R5 ... or <= extended_gap_minutes when the sender already took part and is not the one who wrote last,
     or an employee answers (without the reply button) after a client wrote last.
  R6 an episode never grows past max_episode_hours (except by R3) or max_messages.
  R7 a late (out of order) message joins the episode it falls into, then the previous one (R4/R5),
     then the next one when it is within gap_minutes before it; otherwise it starts a new episode.
     Existing episodes are never merged or split, so already stored episodes stay stable.

The state machine works on messages in any arrival order. Fed in time order it gives exactly the batch
result, which is what the database writer relies on (it replays stored episodes, then adds new messages).
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Tashkent")
SKIP_KINDS = frozenset({"service"})


@dataclass(frozen=True)
class SegConfig:
    version: str = "w2-seg-v1"
    gap_minutes: float = 30
    extended_gap_minutes: float = 60
    reply_window_hours: float = 12
    max_episode_hours: float = 6
    max_messages: int = 200

    @classmethod
    def from_app(cls, app_cfg: dict[str, Any]) -> "SegConfig":
        seg = dict(app_cfg.get("segmenter", {}))
        seg.pop("db_role", None)
        return cls(**seg)

    @property
    def gap(self) -> timedelta:
        return timedelta(minutes=self.gap_minutes)

    @property
    def extended_gap(self) -> timedelta:
        return timedelta(minutes=self.extended_gap_minutes)

    @property
    def reply_window(self) -> timedelta:
        return timedelta(hours=self.reply_window_hours)

    @property
    def max_duration(self) -> timedelta:
        return timedelta(hours=self.max_episode_hours)


@dataclass(frozen=True)
class Msg:
    source_id: int
    tg_message_id: int
    sent_at: datetime  # timezone aware
    sender_tg_id: int | None = None
    sender_is_bot: bool = False
    kind: str = "text"
    reply_to: int | None = None
    thread_id: int | None = None
    sender_role: str | None = None  # people.role: employee | client | unknown

    @property
    def order(self) -> tuple[datetime, int]:
        return (self.sent_at, self.tg_message_id)


def local_day(ts: datetime) -> date:
    if ts.tzinfo is None:
        raise ValueError("sent_at must be timezone aware")
    return ts.astimezone(TZ).date()


def bucket_of(m: Msg) -> tuple[int, int, date]:
    return (m.source_id, m.thread_id or 0, local_day(m.sent_at))


def is_segmentable(m: Msg) -> bool:
    return m.kind not in SKIP_KINDS and not m.sender_is_bot


@dataclass(eq=False)
class Episode:
    bucket: tuple[int, int, date]
    msgs: list[Msg] = field(default_factory=list)
    db_id: int | None = None
    dirty: bool = False

    @property
    def started_at(self) -> datetime:
        return self.msgs[0].sent_at

    @property
    def ended_at(self) -> datetime:
        return self.msgs[-1].sent_at

    @property
    def first_id(self) -> int:
        return self.msgs[0].tg_message_id

    @property
    def last_id(self) -> int:
        return self.msgs[-1].tg_message_id

    @property
    def last_sender(self) -> int | None:
        return self.msgs[-1].sender_tg_id

    @property
    def last_role(self) -> str | None:
        return self.msgs[-1].sender_role

    @property
    def participants(self) -> list[int]:
        return sorted({m.sender_tg_id for m in self.msgs if m.sender_tg_id is not None})

    def add(self, m: Msg) -> None:
        keys = [x.order for x in self.msgs]
        self.msgs.insert(bisect.bisect_right(keys, m.order), m)
        self.dirty = True

    def as_row(self) -> dict[str, Any]:
        return {"source_id": self.bucket[0], "started_at": self.started_at, "ended_at": self.ended_at,
                "first_tg_message_id": self.first_id, "last_tg_message_id": self.last_id,
                "message_count": len(self.msgs), "participant_tg_ids": self.participants,
                "message_ids": [m.tg_message_id for m in self.msgs]}


class Segmenter:
    def __init__(self, cfg: SegConfig | None = None):
        self.cfg = cfg or SegConfig()
        self.buckets: dict[tuple[int, int, date], list[Episode]] = {}
        self.index: dict[tuple[int, int], Episode] = {}  # (source_id, tg_message_id) -> episode

    # -- state ------------------------------------------------------------------------------------------
    @property
    def episodes(self) -> list[Episode]:
        eps = [e for b in self.buckets.values() for e in b]
        return sorted(eps, key=lambda e: (e.bucket[0], e.msgs[0].order))

    def load(self, msgs: Iterable[Msg], db_id: int) -> Episode:
        """Restore an already stored episode as is (the writer calls this before adding new messages)."""
        msgs = sorted(msgs, key=lambda m: m.order)
        ep = Episode(bucket=bucket_of(msgs[0]), msgs=msgs, db_id=db_id)
        for m in msgs:
            if bucket_of(m) != ep.bucket:
                raise ValueError(f"stored episode {db_id} crosses a day or topic boundary")
            self.index[(m.source_id, m.tg_message_id)] = ep
        self.buckets.setdefault(ep.bucket, []).append(ep)
        return ep

    # -- rules ------------------------------------------------------------------------------------------
    def _fits(self, ep: Episode, m: Msg, *, limit_duration: bool = True) -> bool:
        if len(ep.msgs) >= self.cfg.max_messages:
            return False
        if not limit_duration:
            return True
        start, end = min(ep.started_at, m.sent_at), max(ep.ended_at, m.sent_at)
        return end - start <= self.cfg.max_duration

    def _choose(self, m: Msg) -> Episode | None:
        cfg, b = self.cfg, bucket_of(m)
        # R3 reply chain (a reply to the topic root is not a real reply)
        if m.reply_to is not None and m.reply_to != m.thread_id:
            target = self.index.get((m.source_id, m.reply_to))
            if (target is not None and target.bucket == b and m.sent_at - target.ended_at <= cfg.reply_window
                    and self._fits(target, m, limit_duration=False)):
                return target
        eps = self.buckets.get(b, [])
        # R7 late message inside an episode's time span (latest started wins)
        inside = [e for e in eps if e.started_at <= m.sent_at <= e.ended_at and self._fits(e, m)]
        if inside:
            return max(inside, key=lambda e: e.msgs[0].order)
        # R4/R5 previous episode; several open ones (after a reply revived an older one): the sender's own wins
        sender = m.sender_tg_id
        near = [e for e in eps if e.ended_at < m.sent_at and self._fits(e, m) and (
            m.sent_at - e.ended_at <= cfg.gap or (m.sent_at - e.ended_at <= cfg.extended_gap and (
                (sender is not None and sender in e.participants and sender != e.last_sender)
                or (m.sender_role == "employee" and e.last_role == "client"))))]
        if near:
            return max(near, key=lambda e: (sender is not None and sender in e.participants,
                                            e.msgs[-1].order, e.msgs[0].order))
        # R7 late message just before an episode
        nxt = [e for e in eps if e.started_at > m.sent_at]
        if nxt:
            n = min(nxt, key=lambda e: e.msgs[0].order)
            if n.started_at - m.sent_at <= cfg.gap and self._fits(n, m):
                return n
        return None

    def add(self, m: Msg) -> Episode | None:
        """Assign one message; returns its episode, or None for skipped messages (R2). Idempotent."""
        if not is_segmentable(m):
            return None
        key = (m.source_id, m.tg_message_id)
        if key in self.index:
            return self.index[key]
        ep = self._choose(m)
        if ep is None:
            ep = Episode(bucket=bucket_of(m))
            self.buckets.setdefault(ep.bucket, []).append(ep)
        ep.add(m)
        self.index[key] = ep
        return ep


def segment(messages: Iterable[Msg], cfg: SegConfig | None = None) -> list[Episode]:
    """Batch segmentation: input order does not matter (sorted by R1)."""
    seg = Segmenter(cfg)
    for m in sorted(messages, key=lambda x: (x.source_id, x.order)):
        seg.add(m)
    return seg.episodes
