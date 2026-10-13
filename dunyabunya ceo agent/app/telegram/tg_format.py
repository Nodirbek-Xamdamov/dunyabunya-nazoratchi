"""Claude javobini Telegram HTML formatiga moslash.

Claude ko'pincha Markdown yozadi (`**qalin**`, `### sarlavha`, `| jadval |`), bot esa
parse_mode=HTML bilan yuboradi — natijada foydalanuvchi xom `**` va `|---|` belgilarni
ko'rardi. Bundan tashqari matndagi oddiy `<`/`&` (masalan "summa < 5 mln") HTML
parse xatosiga olib kelib, butun javob tagsiz fallback'ga tushib qolardi.

`to_telegram_html()`:
- model yozgan ruxsatli HTML taglarni (<b>, <i>, <a href>, <code>, <pre>...) saqlaydi;
- qolgan `<`, `>`, `&` belgilarni ekranlaydi;
- Markdown'ni Telegram HTML'ga o'giradi (qalin, kursiv, kod, havola, sarlavha, ro'yxat);
- Markdown jadvalni tor bo'lsa <pre> ichida tekislangan jadvalga, keng bo'lsa ro'yxatga
  aylantiradi (Telegram jadvallarni qo'llab-quvvatlamaydi).

`split_message()`: 4096 belgidan uzun javobni kesib tashlash o'rniga qator
chegaralarida bir nechta xabarga bo'ladi.
"""

from __future__ import annotations

import html
import re

TG_LIMIT = 4000
_PRE_TABLE_MAX_WIDTH = 44

_ALLOWED_TAG_RE = re.compile(
    r"</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|blockquote|tg-spoiler)>"
    r"|<a\s+href=\"[^\"<>]*\">|</a>",
    re.IGNORECASE,
)
_ENTITY_RE = re.compile(r"&(?:amp|lt|gt|quot|#\d+|#x[0-9a-fA-F]+);")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _placeholder(store: list[str], value: str) -> str:
    store.append(value)
    return f"\x00{len(store) - 1}\x00"


def _restore(text: str, store: list[str]) -> str:
    # Joy egallovchilar ichma-ich bo'lishi mumkin (masalan jadval ichidagi kod) — takrorlaymiz
    for _ in range(3):
        new = re.sub(r"\x00(\d+)\x00", lambda m: store[int(m.group(1))], text)
        if new == text:
            break
        text = new
    return text


def _strip_md(cell: str) -> str:
    cell = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), cell)
    cell = re.sub(r"`([^`]+)`", r"\1", cell)
    cell = re.sub(r"<[^>]+>", "", cell)
    return html.unescape(cell).strip()


def _render_table(lines: list[str]) -> str:
    rows = []
    for ln in lines:
        if _TABLE_SEP_RE.match(ln):
            continue
        s = ln.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        rows.append([_strip_md(c) for c in s.split("|")])
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    widths = [max(len(r[i]) for r in rows) for i in range(ncol)]
    total = sum(widths) + 2 * (ncol - 1)

    if total <= _PRE_TABLE_MAX_WIDTH:
        out = []
        for idx, r in enumerate(rows):
            out.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip())
            if idx == 0 and len(rows) > 1:
                out.append("-" * total)
        return "<pre>" + html.escape("\n".join(out), quote=False) + "</pre>"

    # Keng jadval — telefonda <pre> buzilib ketadi, shuning uchun ro'yxat ko'rinishida
    header, body = rows[0], rows[1:]
    if not body:
        return html.escape(" · ".join(c for c in header if c), quote=False)
    out = []
    for r in body:
        cells = list(r)
        prefix = ""
        # "#" ustuni (tartib raqami) bo'lsa — uni raqam sifatida oldinga qo'yamiz
        if header and header[0].strip() in {"#", "№", "N"} and len(cells) > 1:
            prefix = f"{cells[0]}. " if cells[0] else ""
            cells, hdr = cells[1:], header[1:]
        else:
            hdr = header
        first = html.escape(cells[0], quote=False) if cells else ""
        rest = [
            f"{html.escape(h, quote=False)}: {html.escape(c, quote=False)}" if h else html.escape(c, quote=False)
            for h, c in zip(hdr[1:], cells[1:]) if c
        ]
        line = f"{prefix}<b>{first}</b>"
        if rest:
            line += " — " + "; ".join(rest)
        out.append(line)
    return "\n".join(out)


def to_telegram_html(text: str) -> str:
    if not text:
        return text
    store: list[str] = []

    # 1) Kod bloklari va inline kod — ichidagi hech narsa o'zgartirilmaydi
    text = re.sub(
        r"```[a-zA-Z0-9_+-]*\n?(.*?)```",
        lambda m: _placeholder(store, "<pre>" + html.escape(html.unescape(m.group(1).strip("\n")), quote=False) + "</pre>"),
        text, flags=re.DOTALL,
    )
    text = re.sub(
        r"`([^`\n]+)`",
        lambda m: _placeholder(store, "<code>" + html.escape(html.unescape(m.group(1)), quote=False) + "</code>"),
        text,
    )

    # 2) Markdown jadvallar (ketma-ket "|" bilan boshlanadigan qatorlar, 2-qator ajratgich)
    lines = text.split("\n")
    out_lines: list[str] = []
    i = 0
    while i < len(lines):
        if (lines[i].lstrip().startswith("|") and i + 1 < len(lines)
                and _TABLE_SEP_RE.match(lines[i + 1])):
            j = i
            block = []
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                block.append(lines[j])
                j += 1
            out_lines.append(_placeholder(store, _render_table(block)))
            i = j
            continue
        out_lines.append(lines[i])
        i += 1
    text = "\n".join(out_lines)

    # 3) Model yozgan ruxsatli HTML taglar va tayyor entity'lar himoyalanadi, qolgani ekranlanadi
    text = _ALLOWED_TAG_RE.sub(lambda m: _placeholder(store, m.group(0)), text)
    text = _ENTITY_RE.sub(lambda m: _placeholder(store, m.group(0)), text)
    text = html.escape(text, quote=False)

    # 4) Markdown -> HTML
    text = re.sub(
        r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)",
        lambda m: f'<a href="{m.group(2).replace(chr(34), "%22")}">{m.group(1)}</a>',
        text,
    )
    text = re.sub(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?<![\w_])__(.+?)__(?![\w_])", r"<b>\1</b>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    text = re.sub(r"^(\s*)[-*•]\s+", r"\1• ", text, flags=re.MULTILINE)
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return _restore(text, store).strip()


def split_message(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Qator chegaralarida bo'lish; <pre> bloki bo'linib qolsa yopib/qayta ochiladi."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # juda uzun bitta qator — majburan kesiladi
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)

    fixed: list[str] = []
    carry_pre = False
    for c in chunks:
        if carry_pre:
            c = "<pre>" + c
        opens, closes = c.count("<pre>"), c.count("</pre>")
        carry_pre = opens > closes
        if carry_pre:
            c += "</pre>"
        fixed.append(c)
    return fixed
