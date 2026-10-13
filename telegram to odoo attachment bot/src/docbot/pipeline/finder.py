"""Looking ``RecordRef``s up in Odoo (shared by the worker steps and the sender's answers)."""

import logging
from typing import Any

from docbot.config import DocumentType
from docbot.documents import Documents, RecordRef
from docbot.odoo import OdooClient, OdooRecord

log = logging.getLogger(__name__)


class RecordFinder:
    def __init__(self, odoo: OdooClient, documents: Documents):
        self._odoo = odoo
        self.documents = documents

    @staticmethod
    def _fields(kind: DocumentType) -> list[str]:
        return [f for f in (kind.partner_field, kind.total_field, kind.person_field) if f]

    async def find(self, ref: RecordRef) -> OdooRecord | None:
        """The record ``ref`` points to. A QR token that does not match (e.g. a print from another database) finds nothing."""
        kind = self.documents.get(ref.doc)
        fields = self._fields(kind)
        if ref.record_id is not None:
            if ref.token is not None:
                fields.append(kind.token_field)
            record = await self._odoo.read_by_id(kind.model, ref.record_id, fields)
            if record is None:
                log.info("%s id %s not found in Odoo", kind.model, ref.record_id)
                return None
            if ref.token is not None and record.values.get(kind.token_field) != ref.token:
                log.warning("%s %s: QR token does not match the record (printed from another database?)", kind.model, record.name)
                return None
            return record
        if ref.name:
            records = await self._odoo.find_by_name(kind.model, ref.name, fields)
            if len(records) > 1:
                log.warning("%s: %s records are named %r", kind.model, len(records), ref.name)
            return records[0] if len(records) == 1 else None
        return None

    async def find_all(self, refs: list[RecordRef]) -> list[OdooRecord]:
        """Distinct records the refs point to, in the order of the refs."""
        found: dict[tuple[str, int], OdooRecord] = {}
        for ref in refs:
            record = await self.find(ref)
            if record is not None:
                found.setdefault((record.model, record.id), record)
        return list(found.values())

    async def partner_of(self, record: OdooRecord, kind: DocumentType) -> dict[str, Any] | None:
        partner = record.many2one(kind.partner_field)
        return await self._odoo.partner(partner[0]) if partner else None
