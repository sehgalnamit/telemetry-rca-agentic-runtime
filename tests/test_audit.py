"""Tests for the hash-chained, append-only audit log: chain integrity and
tamper detection."""

from __future__ import annotations

from audit import AuditEntryType, AuditLogStore


def test_fresh_chain_verifies_ok():
    store = AuditLogStore()
    store.append(AuditEntryType.ALERT_RECEIVED, tenant_id="acme", payload={"service": "pricing"})
    store.append(AuditEntryType.HYPOTHESIS, tenant_id="acme", payload={"root_cause_type": "memory_leak"})
    store.append(AuditEntryType.FINAL_DECISION, tenant_id="acme", payload={"action": "alert"})

    result = store.verify_chain()
    assert result.ok is True
    assert result.checked_entries == 3
    assert result.broken_at_index is None


def test_each_entry_links_to_previous_hash():
    store = AuditLogStore()
    e0 = store.append(AuditEntryType.ALERT_RECEIVED, tenant_id="acme", payload={})
    e1 = store.append(AuditEntryType.HYPOTHESIS, tenant_id="acme", payload={})

    assert e1.prev_hash == e0.entry_hash
    assert e0.entry_hash != ""
    assert e0.entry_hash != e1.entry_hash


def test_tampered_payload_is_detected():
    store = AuditLogStore()
    store.append(AuditEntryType.ALERT_RECEIVED, tenant_id="acme", payload={"severity": "WARN"})
    store.append(AuditEntryType.HYPOTHESIS, tenant_id="acme", payload={"root_cause_type": "memory_leak"})
    store.append(AuditEntryType.FINAL_DECISION, tenant_id="acme", payload={"action": "escalate"})

    assert store.verify_chain().ok is True

    # Tamper: silently rewrite an earlier entry's payload (e.g. to hide an
    # escalate-worthy decision) without touching anything else.
    store._entries[0].payload["severity"] = "INFO"

    result = store.verify_chain()
    assert result.ok is False
    assert result.broken_at_index == 0


def test_reordered_entries_are_detected():
    store = AuditLogStore()
    store.append(AuditEntryType.ALERT_RECEIVED, tenant_id="acme", payload={"n": 1})
    store.append(AuditEntryType.HYPOTHESIS, tenant_id="acme", payload={"n": 2})
    store.append(AuditEntryType.FINAL_DECISION, tenant_id="acme", payload={"n": 3})

    # Swap the last two entries' positions in the in-memory list, which
    # breaks the prev_hash link chain.
    store._entries[1], store._entries[2] = store._entries[2], store._entries[1]

    result = store.verify_chain()
    assert result.ok is False
    assert result.broken_at_index == 2


def test_appended_entry_after_tamper_still_detects_original_break():
    store = AuditLogStore()
    store.append(AuditEntryType.ALERT_RECEIVED, tenant_id="acme", payload={"n": 1})
    store._entries[0].payload["n"] = 999
    store.append(AuditEntryType.HYPOTHESIS, tenant_id="acme", payload={"n": 2})

    result = store.verify_chain()
    assert result.ok is False
    assert result.broken_at_index == 0


def test_export_jsonl_writes_one_line_per_entry(tmp_path):
    store = AuditLogStore()
    store.append(AuditEntryType.ALERT_RECEIVED, tenant_id="acme", payload={"n": 1})
    store.append(AuditEntryType.HYPOTHESIS, tenant_id="acme", payload={"n": 2})

    out_path = tmp_path / "audit.jsonl"
    store.export_jsonl(str(out_path))

    lines = out_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
