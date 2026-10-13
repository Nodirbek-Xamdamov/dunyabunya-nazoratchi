"""Cross-checking what the AI read against the Odoo record its document number points to.

A misread digit in the number points to some other, random record, and that record's customer is almost
never the one on the paper. So the customer (its name, or its phone) is the identity check: if it agrees, the
record is the right one even when the total differs (the order was changed after printing) or the printed
phone is a delivery contact. A customer that clearly contradicts means a wrong record. Without a customer,
any agreeing field with no contradiction is enough. Names are compared fuzzily (the AI misspells, e.g.
"Dilorhod" for "Dilshod", 0.88 similar), the phone by its last 9 digits, the total exactly.

Real cases (2026-09-28, production): S87632 customer+phone+manager agreed, total differed; P33803 and S87571
customer+manager agreed, printed phone differed. The first rule ("no contradiction at all") rejected all
three and asked for a clearer photo of perfectly clear pictures.
"""

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from docbot.ai import Extraction
from docbot.config import DocumentType
from docbot.odoo import OdooRecord

NAME_MATCH = 0.80  # at or above: the same name, misread a little
NAME_CONFLICT = 0.50  # below: a different name

_APOSTROPHES = re.compile(r"[‘’ʻʼ'`´]")
_NOT_WORD = re.compile(r"[^A-Z0-9 ]+")


def normalize_name(text: str) -> str:
    text = _APOSTROPHES.sub("", text.upper())
    return " ".join(_NOT_WORD.sub(" ", text).split())


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize_name(a), normalize_name(b)).ratio()


def phone_tail(text: str | None) -> str:
    digits = re.sub(r"\D", "", text or "")
    return digits[-9:] if len(digits) >= 7 else ""


@dataclass(frozen=True)
class Verdict:
    matched: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        if "partner" in self.conflicts:
            return False  # another customer: a misread number
        if "partner" in self.matched or "phone" in self.matched:
            return True  # the customer is the identity; total/manager may have changed since printing
        return bool(self.matched) and not self.conflicts

    def as_dict(self) -> dict[str, Any]:
        return {"matched": self.matched, "conflicts": self.conflicts, "details": self.details}

    def reason(self) -> str:
        """Why it was not attached automatically, for the sender."""
        if self.conflicts:
            return "mos kelmadi: " + ", ".join(_LABELS[name] for name in self.conflicts)
        return "tekshirish uchun ma'lumot o'qilmadi"


_LABELS = {"total": "summa", "partner": "xaridor", "phone": "telefon", "person": "menejer"}


def verify(read: Extraction, record: OdooRecord, kind: DocumentType, partner: dict[str, Any] | None) -> Verdict:
    matched: list[str] = []
    conflicts: list[str] = []
    details: dict[str, Any] = {}

    def names(label: str, ours: str | None, theirs: str | None) -> None:
        if not ours or not theirs:
            return
        score = round(similarity(ours, theirs), 2)
        details[label] = score
        if score >= NAME_MATCH:
            matched.append(label)
        elif score < NAME_CONFLICT:
            conflicts.append(label)

    if kind.total_field and read.total is not None:
        value = record.values.get(kind.total_field)
        if isinstance(value, int | float):
            details["total"] = [read.total, value]
            (matched if abs(read.total - value) < 1 else conflicts).append("total")

    names("partner", read.partner, partner.get("name") if partner else None)

    tail = phone_tail(read.phone)
    if tail and partner:
        known = {phone_tail(partner.get("phone")), phone_tail(partner.get("mobile"))} - {""}
        if known:
            details["phone"] = tail in known
            if tail in known:
                matched.append("phone")
            # A different phone is no contradiction: the paper often shows the delivery contact's number
            # (S86574, S86443, S85476 on 2026-09-28 were right documents stopped by exactly that).

    person = record.many2one(kind.person_field)
    names("person", read.person, person[1] if person else None)

    return Verdict(matched=matched, conflicts=conflicts, details=details)
