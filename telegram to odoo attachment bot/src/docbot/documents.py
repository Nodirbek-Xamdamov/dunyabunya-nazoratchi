"""Document kinds from config/documents.yaml: which Odoo record a code text or a document number points to.

Nothing here talks to Odoo. The result is a ``RecordRef`` (look the record up by id + token, or by
name); ``pipeline.finder`` performs the lookup. Keeping the two apart lets the refs be stored in the
job (JSON) and looked up again after a restart or an Odoo outage.
"""

import re
from dataclasses import asdict, dataclass
from typing import Any

from docbot.config import DocumentsConfig, DocumentType

# Latin letters that people type with a Cyrillic keyboard layout by accident (they look the same).
_CYRILLIC_LOOKALIKES = str.maketrans("АВЕКМНОРСТХУ", "ABEKMHOPCTXY")
_NUMBER_PREFIXES = re.compile(r"^(?:№|#|NO\.|N\.)\s*")


@dataclass(frozen=True)
class RecordRef:
    """How to find one Odoo record: by ``record_id`` (optionally proven by ``token``) or by ``name``."""

    doc: str  # DocumentType.key
    model: str
    record_id: int | None = None
    token: str | None = None
    name: str | None = None
    source: str = ""  # the text it was read from

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecordRef":
        return cls(
            doc=data["doc"],
            model=data["model"],
            record_id=data.get("record_id"),
            token=data.get("token"),
            name=data.get("name"),
            source=data.get("source") or "",
        )

    @property
    def label(self) -> str:
        return self.name or f"{self.model}#{self.record_id}"


def normalize_number(text: str) -> str:
    """ "№ s 82409 " -> "S82409"; "од/out/01514" -> "OD/OUT/01514" (Cyrillic look-alikes become Latin)."""
    text = text.strip().upper().translate(_CYRILLIC_LOOKALIKES)
    text = _NUMBER_PREFIXES.sub("", text)
    return re.sub(r"\s+", "", text)


class Documents:
    """The configured document kinds, with the matching rules applied to code texts and numbers."""

    def __init__(self, config: DocumentsConfig):
        self.kinds: list[DocumentType] = list(config.documents)
        self._by_key = {kind.key: kind for kind in self.kinds}
        self._by_model = {}
        for kind in self.kinds:
            self._by_model.setdefault(kind.model, kind)
        # Numbers inside free text (a PDF's text layer): not glued to other letters, digits or slashes.
        self._in_text = [(kind, re.compile(rf"(?<![A-Z0-9/])(?:{kind.number})(?![A-Z0-9/])")) for kind in self.kinds]

    def get(self, key: str) -> DocumentType:
        return self._by_key[key]

    def for_model(self, model: str | None) -> DocumentType | None:
        return self._by_model.get(model) if model else None

    def title(self, model: str | None) -> str:
        kind = self.for_model(model)
        return kind.title if kind else (model or "hujjat")

    def from_code(self, text: str) -> RecordRef | None:
        """A QR/barcode text -> the record it names, or None if it is not one of our documents (e.g. a website link)."""
        text = text.strip()
        for kind in self.kinds:
            for pattern in kind.codes:
                match = re.search(pattern, text)
                if match is None:
                    continue
                groups = match.groupdict()
                if groups.get("id"):
                    return RecordRef(kind.key, kind.model, record_id=int(groups["id"]), token=groups.get("token"), source=text)
                if groups.get("name"):
                    return RecordRef(kind.key, kind.model, name=normalize_number(groups["name"]), source=text)
        return None

    def from_number(self, text: str) -> list[RecordRef]:
        """A document number (read by the AI or typed by a person) -> a lookup per kind whose number shape fits."""
        number = normalize_number(text)
        return [
            RecordRef(kind.key, kind.model, name=number, source=text)
            for kind in self.kinds
            if number and re.fullmatch(kind.number, number)
        ]

    def find_numbers(self, text: str) -> list[RecordRef]:
        """Every document number that appears in a longer text (PDF text layer), first occurrence first."""
        found: dict[tuple[str, str], tuple[int, RecordRef]] = {}
        upper = text.upper()
        for kind, pattern in self._in_text:
            for match in pattern.finditer(upper):
                key = (kind.key, match.group(0))
                if key not in found:
                    found[key] = (match.start(), RecordRef(kind.key, kind.model, name=match.group(0), source=match.group(0)))
        return [ref for _, ref in sorted(found.values(), key=lambda item: item[0])]

    def examples(self) -> str:
        """Short list for messages to people: "S82409, P32200 yoki OD/OUT/01234"."""
        samples = {"sale": "S82409", "purchase": "P32200", "picking": "OD/OUT/01234"}
        values = [samples.get(kind.key, kind.title) for kind in self.kinds]
        return values[0] if len(values) == 1 else ", ".join(values[:-1]) + " yoki " + values[-1]
