"""End-to-end pipeline tests: inject a labeled failure scenario, run the
full agentic RCA pipeline (triage -> hypothesize -> federated evidence ->
rule engine -> jury gate -> decision -> audit), and verify the correct
root cause is surfaced with an intact audit trail."""

from __future__ import annotations

import pytest

from audit.models import AuditEntryType

from tests.helpers import ALL_SCENARIO_KEYS, build_scenario_fixture, run_pipeline


@pytest.mark.parametrize("scenario_key", ALL_SCENARIO_KEYS)
def test_pipeline_surfaces_correct_root_cause(tmp_path, scenario_key):
    fixture = build_scenario_fixture(tmp_path, scenario_key)
    result = run_pipeline(fixture)

    top = result.top_assessment
    assert top.hypothesis.root_cause_type == fixture.truth.root_cause_type
    assert top.hypothesis.candidate_label == fixture.truth.root_cause_service
    assert result.final_decision.action in {"auto_remediate", "alert"}


@pytest.mark.parametrize("scenario_key", ALL_SCENARIO_KEYS)
def test_pipeline_audit_trail_is_intact_and_complete(tmp_path, scenario_key):
    fixture = build_scenario_fixture(tmp_path, scenario_key)
    result = run_pipeline(fixture)

    verification = result.audit_log.verify_chain()
    assert verification.ok is True

    entry_types = {e.entry_type for e in result.audit_log.entries}
    assert entry_types == {
        AuditEntryType.ALERT_RECEIVED,
        AuditEntryType.HYPOTHESIS,
        AuditEntryType.EVIDENCE_QUERY,
        AuditEntryType.RULE_VERDICT,
        AuditEntryType.JURY_VERDICT,
        AuditEntryType.FINAL_DECISION,
    }


def test_pipeline_audit_trail_never_contains_raw_telemetry_text(tmp_path):
    """The audit trail must preserve zero-copy even for its own records: no
    entry payload should ever embed a raw log message string pulled from
    tenant data (only structured metadata about what was queried/decided)."""
    fixture = build_scenario_fixture(tmp_path, "downstream_dependency_outage")
    result = run_pipeline(fixture)

    # A phrase that only appears inside generated LogRecord.message text,
    # never in any hypothesis/query/verdict/decision metadata field.
    needle = "connection refused calling dependency"
    for entry in result.audit_log.entries:
        assert needle not in str(entry.payload)


def test_pipeline_tamper_detection_end_to_end(tmp_path):
    fixture = build_scenario_fixture(tmp_path, "bad_deploy")
    result = run_pipeline(fixture)
    assert result.audit_log.verify_chain().ok is True

    # Simulate an attacker/operator silently editing a past rule verdict to
    # hide that a bad deploy was in fact detected.
    for entry in result.audit_log.entries:
        if entry.entry_type == AuditEntryType.FINAL_DECISION:
            entry.payload["action"] = "no_action"
            break

    verification = result.audit_log.verify_chain()
    assert verification.ok is False
