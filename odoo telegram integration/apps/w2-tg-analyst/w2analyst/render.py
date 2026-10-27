"""Episode text for the model (PROMPT-SPEC §2.1): one line per message, media replaced by its text.

    [10:05 #123] [CLIENT] Ali: matn
    [10:07 #124] [EMPLOYEE] Vali: [voice 0:42] transkript
    [10:09 #125] [CLIENT] Ali: [photo] tavsif | caption

The message id is in every line because the analysis must quote evidence by message id (PROMPT-SPEC §1).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .segmenter import TZ

ROLES = {"employee": "EMPLOYEE", "client": "CLIENT"}
MEDIA_TAGS = {"voice": "voice", "audio": "audio", "video": "video", "video_note": "video", "photo": "photo",
              "document": "document", "sticker": "sticker", "other": "media"}
SPOKEN = {"voice", "audio", "video", "video_note"}

NO_TRANSCRIPT = "(transkript hali yo'q)"
NO_DESCRIPTION = "(tavsif hali yo'q)"
PURGED = "(matn o'chirilgan)"


@dataclass(frozen=True)
class Line:
    tg_message_id: int
    sent_at: datetime
    kind: str = "text"
    sender_name: str | None = None
    role: str | None = None            # people.role
    text: str | None = None            # text or caption
    text_purged: bool = False
    deleted: bool = False
    forward_from: str | None = None
    reply_to: int | None = None
    duration_s: float | None = None
    transcript: str | None = None
    description: str | None = None     # photo description (W2-08)


def _dur(s: float | None) -> str:
    if s is None:
        return ""
    s = int(round(float(s)))
    return f" {s // 60}:{s % 60:02d}"


def render_line(x: Line) -> str:
    head = f"[{x.sent_at.astimezone(TZ):%H:%M} #{x.tg_message_id}] [{ROLES.get(x.role or '', 'UNKNOWN')}] {x.sender_name or '?'}:"
    parts: list[str] = []
    if x.forward_from:
        parts.append(f"[forward: {x.forward_from}]")
    if x.reply_to is not None:
        parts.append(f"[reply #{x.reply_to}]")
    caption = PURGED if x.text_purged else (x.text or "").strip()
    if x.kind in SPOKEN:
        body = PURGED if x.text_purged else ((x.transcript or "").strip() or NO_TRANSCRIPT)
        parts.append(f"[{MEDIA_TAGS[x.kind]}{_dur(x.duration_s)}] {body}")
        if caption and not x.text_purged:
            parts.append(f"| {caption}")
    elif x.kind == "photo":
        desc = (x.description or "").strip()
        parts.append(f"[photo] {desc or (NO_DESCRIPTION if not caption else '')}".rstrip())
        if caption:
            parts.append(f"| {caption}" if desc else caption)
    elif x.kind in MEDIA_TAGS:
        parts.append(f"[{MEDIA_TAGS[x.kind]}]")
        if caption:
            parts.append(caption)
    else:
        parts.append(caption)
    if x.deleted:
        parts.append("(o'chirilgan xabar)")
    return " ".join([head, *[p for p in parts if p]])


def render(lines: list[Line]) -> str:
    return "\n".join(render_line(x) for x in sorted(lines, key=lambda x: (x.sent_at, x.tg_message_id)))


EPISODE_LINES_SQL = """
SELECT m.tg_message_id, m.sent_at, m.kind, coalesce(m.sender_name, p.display_name) AS sender_name, p.role, m.text,
       m.text_purged_at IS NOT NULL AS text_purged, m.deleted_at IS NOT NULL AS deleted, m.forward_from,
       m.reply_to_tg_message_id, md.duration_s, t.text AS transcript
FROM core.episode_messages em
JOIN core.messages m ON m.source_id = em.source_id AND m.tg_message_id = em.tg_message_id AND m.sent_at = em.sent_at
LEFT JOIN core.people p ON p.tg_user_id = m.sender_tg_id
LEFT JOIN core.media md ON md.source_id = m.source_id AND md.tg_message_id = m.tg_message_id
LEFT JOIN LATERAL (SELECT tr.text FROM core.transcripts tr
                   WHERE tr.media_id = md.id AND tr.message_sent_at = m.sent_at
                   ORDER BY tr.is_primary DESC, tr.created_at DESC LIMIT 1) t ON true
WHERE em.episode_id = %s
ORDER BY m.sent_at, m.tg_message_id
"""


def episode_text(conn: Any, episode_id: int, descriptions: dict[int, str] | None = None) -> str:
    """Build the text of a stored episode (role w2_analyst). `descriptions`: tg_message_id -> photo description."""
    descriptions = descriptions or {}
    rows = conn.execute(EPISODE_LINES_SQL, (episode_id,)).fetchall()
    return render([Line(tg_message_id=r[0], sent_at=r[1], kind=r[2], sender_name=r[3], role=r[4], text=r[5],
                        text_purged=r[6], deleted=r[7], forward_from=r[8], reply_to=r[9], duration_s=r[10],
                        transcript=r[11], description=descriptions.get(r[0])) for r in rows])
