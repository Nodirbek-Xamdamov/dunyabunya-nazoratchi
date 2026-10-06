"""Ro'yxat hisobotlarining umumiy tuzilishi: guruhlash, ustun turlari va qiymatlarni ko'rsatish
(telefon, sana, pog'onali nom). HTML, Excel, Word, PDF va matn eksporti shu bitta tuzilmadan
foydalanadi — ro'yxat hamma formatda bir xil tartibda va bir xil ko'rinishda chiqadi.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PATH_SEP = " / "
NO_GROUP = "Ko'rsatilmagan"

_PHONE_FIELD_RE = re.compile(r"phone|mobile|(^|_)tel($|_)", re.IGNORECASE)
_PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{5,}\d")
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::\d{2})?)?$")


@dataclass
class Group:
    title: str                 # qisqa nom: pog'onali nomning oxirgi bo'g'ini ("Отдел B2B")
    context: str               # undan oldingi bo'g'inlar ("Savdo bo'limi"); hamma guruhga umumiy qism olib tashlangan
    full: str                  # bazadagi to'liq nom
    rows: list[dict] = field(default_factory=list)


@dataclass
class ListLayout:
    columns: list[tuple[str, str]]     # (maydon, sarlavha) — guruh ustunisiz
    kinds: dict[str, str]              # maydon -> phone | email | path | date | datetime | number | bool | text
    groups: list[Group]                # guruhlanmagan bo'lsa bitta guruh (title = "")
    group_label: str = ""              # guruh maydonining nomi ("Bo'lim"); "" — guruhlanmagan

    @property
    def grouped(self) -> bool:
        return bool(self.group_label)

    @property
    def rows(self) -> list[dict]:
        return [row for group in self.groups for row in group.rows]


# ── Qiymatlarni ko'rsatish ──────────────────────────────────────────────────

def phone_parts(value) -> list[tuple[str, str]]:
    """Matndagi telefon raqamlari: [(ko'rinishi, "tel:" uchun raqam)]. O'zbekiston raqamlari
    "+998 91 785 00 20" ko'rinishida; raqam topilmasa (ichki qisqa raqam, matn) — bo'sh ro'yxat."""
    parts = []
    for match in _PHONE_RE.findall(str(value or "")):
        digits = re.sub(r"\D", "", match)
        if len(digits) == 12 and digits.startswith("998"):
            local = digits[3:]
            parts.append((f"+998 {local[:2]} {local[2:5]} {local[5:7]} {local[7:]}", "+" + digits))
        elif len(digits) == 9:
            parts.append((f"+998 {digits[:2]} {digits[2:5]} {digits[5:7]} {digits[7:]}", "+998" + digits))
        elif 7 <= len(digits) <= 15:
            parts.append((match.strip(), ("+" if match.strip().startswith("+") else "") + digits))
    return parts


def phone_text(value) -> str:
    """Telefon ustunining matn ko'rinishi (Excel, Word, PDF, chat uchun)."""
    parts = phone_parts(value)
    return ", ".join(text for text, _tel in parts) if parts else str(value or "")


def date_text(value) -> str:
    """"2026-10-04 16:26" -> "04.10.2026 16:26"; sana bo'lmasa — o'zi."""
    match = _ISO_DATE_RE.match(str(value or ""))
    if not match:
        return str(value or "")
    year, month, day, hour, minute = match.groups()
    return f"{day}.{month}.{year}" + (f" {hour}:{minute}" if hour else "")


def date_sort_key(value) -> str:
    match = _ISO_DATE_RE.match(str(value or ""))
    return "".join(part or "00" for part in match.groups()) if match else ""


def split_path(value) -> tuple[str, str]:
    """Pog'onali nom ("A / B / C") -> (oxirgi bo'g'in, undan oldingilari "A › B")."""
    parts = [p.strip() for p in str(value or "").split(PATH_SEP) if p.strip()]
    if len(parts) <= 1:
        return str(value or ""), ""
    return parts[-1], " › ".join(parts[:-1])


def display(value, kind: str) -> str:
    """Qiymatning oddiy matn ko'rinishi (HTML'dan boshqa formatlar uchun)."""
    if value is None or value is False or value == "":
        return ""
    if kind == "phone":
        return phone_text(value)
    if kind in ("date", "datetime"):
        return date_text(value)
    return str(value)


# ── Tuzilma ─────────────────────────────────────────────────────────────────

def _kind(field_name: str, ftype: str, values: list) -> str:
    if ftype in ("date", "datetime"):
        return ftype
    if ftype in ("monetary", "float", "integer"):
        return "number"
    if ftype == "boolean":
        return "bool"
    texts = [v for v in values if isinstance(v, str) and v]
    if _PHONE_FIELD_RE.search(field_name) and any(phone_parts(v) for v in texts):
        return "phone"
    if "email" in field_name.lower() and any("@" in v for v in texts):
        return "email"
    if ftype == "many2one" and texts and sum(PATH_SEP in v for v in texts) * 3 >= len(texts):
        return "path"
    return "text"


def _common_prefix(paths: list[list[str]]) -> int:
    """Hamma pog'onali nomlarga umumiy boshlang'ich bo'g'inlar soni (har birida kamida bitta
    bo'g'in qolishi sharti bilan) — guruh nomlari "Rahbariyat / Operatsion / ..." bilan
    boshlanaversa, shu takroriy qism sarlavhalardan olib tashlanadi."""
    if len(paths) < 2:
        return 0
    limit = min(len(p) for p in paths) - 1
    count = 0
    while count < limit and len({tuple(p[:count + 1]) for p in paths}) == 1:
        count += 1
    return count


def build(rows: list[dict], columns: list[tuple[str, str]], types: dict[str, str] | None = None,
          group_by: str = "") -> ListLayout:
    """Qatorlardan ro'yxat tuzilmasini yasaydi. `group_by` ustunlar ichida bo'lsa — qatorlar shu
    maydon bo'yicha guruhlanadi (guruhlar nomi bo'yicha, "ko'rsatilmagan" oxirida) va u jadval
    ustunlaridan olinadi."""
    types = types or {}
    labels = dict(columns)
    kinds = {name: _kind(name, types.get(name) or "", [r.get(name) for r in rows]) for name, _label in columns}
    if not group_by or group_by not in labels or len(columns) < 2:
        return ListLayout(columns=list(columns), kinds=kinds, groups=[Group("", "", "", list(rows))])

    buckets: dict[str, list[dict]] = {}
    for row in rows:
        value = row.get(group_by)
        key = "" if value in (None, False, "") else str(value).strip()
        buckets.setdefault(key, []).append(row)
    names = sorted((name for name in buckets if name), key=str.casefold)
    paths = [[p.strip() for p in name.split(PATH_SEP) if p.strip()] or [name] for name in names]
    skip = _common_prefix(paths)
    parents = [path[skip:-1] for path in paths]
    # Guruhlarning yarmidan ko'pida uchraydigan bo'g'in ("Operatsion") hech narsani ajratmaydi —
    # sarlavha yonidagi izohdan olib tashlanadi (to'liq nom `full`da qoladi)
    usage: dict[str, int] = {}
    for segments in parents:
        for segment in set(segments):
            usage[segment] = usage.get(segment, 0) + 1
    common = {segment for segment, count in usage.items() if len(paths) >= 4 and count * 2 > len(paths)}
    # Izohda eng yaqin ikki yuqori bo'g'in qoladi — to'liq zanjir sarlavhani bosib ketadi
    groups = [Group(title=path[-1], context=" › ".join([s for s in segments if s not in common][-2:]), full=name,
                    rows=buckets[name])
              for name, path, segments in zip(names, paths, parents)]
    if "" in buckets:
        groups.append(Group(title=NO_GROUP, context="", full="", rows=buckets[""]))
    return ListLayout(columns=[c for c in columns if c[0] != group_by],
                      kinds={k: v for k, v in kinds.items() if k != group_by},
                      groups=groups, group_label=str(labels[group_by]))
