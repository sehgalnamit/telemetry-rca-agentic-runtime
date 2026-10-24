"""Immutable, hash-chained, append-only audit log.

Every hypothesis, federated evidence query (metadata only -- see
``federation.query_layer.QueryDescriptor``), rule-engine verdict, jury
verdict, and final decision made by the RCA agent is recorded here. Each
entry embeds the SHA-256 hash of the previous entry, so any retroactive
edit to an earlier entry changes its hash and breaks every subsequent link
-- detectable by :meth:`AuditLogStore.verify_chain`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

from audit.models import AuditEntry, AuditEntryType, VerificationResult

GENESIS_HASH = "0" * 64


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))


def _compute_hash(index: int, timestamp: datetime, entry_type: str, tenant_id: str,
                   payload: dict, prev_hash: str) -> str:
    material = _canonical_json(
        {
            "index": index,
            "timestamp": timestamp.isoformat(),
            "entry_type": entry_type,
            "tenant_id": tenant_id,
            "payload": payload,
            "prev_hash": prev_hash,
        }
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class AuditLogStore:
    """In-memory, hash-chained audit log (optionally persisted to a JSONL
    file via :meth:`export_jsonl`)."""

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    def append(
        self,
        entry_type: AuditEntryType,
        tenant_id: str,
        payload: dict,
        timestamp: Optional[datetime] = None,
    ) -> AuditEntry:
        index = len(self._entries)
        prev_hash = self._entries[-1].entry_hash if self._entries else GENESIS_HASH
        ts = timestamp or datetime.now(timezone.utc)
        entry_hash = _compute_hash(index, ts, entry_type.value, tenant_id, payload, prev_hash)
        entry = AuditEntry(
            index=index,
            timestamp=ts,
            entry_type=entry_type,
            tenant_id=tenant_id,
            payload=payload,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )
        self._entries.append(entry)
        return entry

    @property
    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def verify_chain(self) -> VerificationResult:
        """Walk the full chain recomputing each entry's hash. Detects both
        (a) a tampered payload/field on an existing entry, and (b) a broken
        prev_hash link (e.g. an entry spliced out or reordered)."""
        prev_hash = GENESIS_HASH
        for entry in self._entries:
            if entry.prev_hash != prev_hash:
                return VerificationResult(
                    ok=False,
                    checked_entries=entry.index,
                    broken_at_index=entry.index,
                    detail=(
                        f"entry {entry.index} prev_hash does not match the actual hash of "
                        f"entry {entry.index - 1} (chain link broken or entries reordered)"
                    ),
                )
            recomputed = _compute_hash(
                entry.index, entry.timestamp, entry.entry_type.value, entry.tenant_id,
                entry.payload, entry.prev_hash,
            )
            if recomputed != entry.entry_hash:
                return VerificationResult(
                    ok=False,
                    checked_entries=entry.index,
                    broken_at_index=entry.index,
                    detail=f"entry {entry.index} hash does not match its recomputed content hash (tampered)",
                )
            prev_hash = entry.entry_hash

        return VerificationResult(ok=True, checked_entries=len(self._entries), detail="chain intact")

    def export_jsonl(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for entry in self._entries:
                f.write(entry.model_dump_json())
                f.write("\n")
