"""Immutable, hash-chained, append-only audit log for RCA pipeline
traceability. Payloads are always metadata (never raw tenant telemetry
rows), preserving zero-copy even in the audit trail itself.
"""

from audit.log_store import AuditLogStore
from audit.models import AuditEntry, AuditEntryType, VerificationResult

__all__ = ["AuditLogStore", "AuditEntry", "AuditEntryType", "VerificationResult"]
