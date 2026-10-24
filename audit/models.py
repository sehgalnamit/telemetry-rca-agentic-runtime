"""Typed models for the hash-chained audit log."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AuditEntryType(str, Enum):
    ALERT_RECEIVED = "alert_received"
    HYPOTHESIS = "hypothesis"
    EVIDENCE_QUERY = "evidence_query"
    RULE_VERDICT = "rule_verdict"
    JURY_VERDICT = "jury_verdict"
    FINAL_DECISION = "final_decision"


class AuditEntry(BaseModel):
    """One immutable, hash-chained audit log entry.

    ``payload`` must never contain raw tenant telemetry rows -- only
    metadata (hypothesis descriptions, query scope/shape, verdicts,
    decisions). This is what lets the audit trail itself remain zero-copy:
    it is fully centralizable/shareable without ever having copied a single
    tenant data row out of that tenant's own store.
    """

    index: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    entry_type: AuditEntryType
    tenant_id: str
    payload: dict[str, Any]
    prev_hash: str
    entry_hash: str = ""


class VerificationResult(BaseModel):
    ok: bool
    checked_entries: int
    broken_at_index: int | None = None
    detail: str = ""
