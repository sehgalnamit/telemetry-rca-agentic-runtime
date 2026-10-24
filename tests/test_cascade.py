"""Tests for the Confidence-Gated Routing cascade: unit-level routing
decisions with hand-constructed assessments, plus integration-level
checks across the 5 labeled scenarios through the full agent pipeline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent import Alert
from agent.models import Hypothesis
from agent.runtime import HypothesisAssessment, RCAAgentRuntime
from evaluator import LocalJevStandIn
from evaluator.cascade import CascadePath, ConfidenceGatedCascade
from evaluator.jury import JuryPanel
from rules.features import EvidenceBundle
from rules.models import RuleVerdict
from tests.helpers import ALL_SCENARIO_KEYS, build_scenario_fixture

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _assessment(root_cause_type: str, candidate_label: str, confidence: float, matched: bool) -> HypothesisAssessment:
    hypothesis = Hypothesis(
        hypothesis_id=f"hyp-{root_cause_type}-{candidate_label}",
        root_cause_type=root_cause_type,
        candidate_service=candidate_label,
        candidate_label=candidate_label,
        services_to_investigate=[candidate_label],
        rationale="test",
    )
    verdict = RuleVerdict(
        rule_id=f"{root_cause_type}_v1",
        root_cause_type=root_cause_type,
        candidate_service=candidate_label,
        matched=matched,
        confidence=confidence,
        matched_signals=["a"] if matched else [],
        unmatched_signals=[] if matched else ["a"],
        rationale="test",
    )
    bundle = EvidenceBundle(
        tenant_id="acme",
        primary_service=candidate_label,
        primary_label=candidate_label,
        baseline_start=NOW,
        baseline_end=NOW,
        incident_start=NOW,
        incident_end=NOW,
    )
    return HypothesisAssessment(hypothesis=hypothesis, rule_verdict=verdict, bundle=bundle)


# ---------------------------------------------------------------------------
# Unit-level routing tests with hand-constructed assessments
# ---------------------------------------------------------------------------


def test_cascade_takes_jev_only_path_on_high_confidence_agreement():
    assessments = [
        _assessment("cascading_timeout", "pricing", 0.95, True),
        _assessment("memory_leak", "pricing", 0.0, False),
    ]
    cascade = ConfidenceGatedCascade(jev_client=LocalJevStandIn())
    result = cascade.evaluate(assessments)

    assert result.path == CascadePath.JEV_ONLY
    assert result.jev_agrees_with_rule_engine is True
    assert result.jev_high_confidence is True
    assert result.jury_verdict is None
    assert result.final_decision.action in {"auto_remediate", "alert"}


def test_cascade_escalates_to_jury_on_ambiguous_confidence():
    # A rule confidence right near 0.5 is maximally ambiguous for the Score
    # question (see test_jev_gate.py), so this should never clear the
    # high-confidence bar regardless of threshold tuning headroom.
    assessments = [
        _assessment("memory_leak", "pricing", 0.5, True),
        _assessment("cascading_timeout", "pricing", 0.3, False),
    ]
    cascade = ConfidenceGatedCascade(jev_client=LocalJevStandIn(), high_confidence_threshold=0.75)
    result = cascade.evaluate(assessments)

    assert result.path == CascadePath.JEV_PLUS_JURY
    assert result.jury_verdict is not None


def test_cascade_escalates_to_jury_on_disagreement():
    # Force Jev's Choice answer to something that cannot be the real top
    # candidate's key, regardless of the stand-in's own softmax outcome.
    class AlwaysDisagreeJevClient(LocalJevStandIn):
        def system_one(self, state, questions):
            answers = super().system_one(state, questions)
            from evaluator.jev_gate import ChoiceAnswer

            answers["top_choice"] = ChoiceAnswer(
                choice="__never_the_real_top__",
                probabilities={"__never_the_real_top__": 1.0},
                confidence=1.0,
            )
            return answers

    assessments = [
        _assessment("cascading_timeout", "pricing", 0.95, True),
        _assessment("memory_leak", "pricing", 0.0, False),
    ]
    cascade = ConfidenceGatedCascade(jev_client=AlwaysDisagreeJevClient())
    result = cascade.evaluate(assessments)

    assert result.path == CascadePath.JEV_PLUS_JURY
    assert result.jev_agrees_with_rule_engine is False
    assert result.jury_verdict is not None


def test_cascade_distinguishes_same_label_different_hypothesis_types():
    """Regression test: candidate_label collisions across different
    root_cause_types for the same service must not silently merge
    distinct hypotheses' confidences together."""
    assessments = [
        _assessment("memory_leak", "pricing", 0.9, True),
        _assessment("cascading_timeout", "pricing", 0.0, False),
        _assessment("bad_deploy", "pricing", 0.0, False),
    ]
    cascade = ConfidenceGatedCascade(jev_client=LocalJevStandIn())
    state, _questions = cascade._build_state_and_questions(assessments)

    # All three candidates share candidate_label "pricing" but must remain
    # distinguishable in the state handed to Jev.
    assert len(state["candidates"]) == 3
    assert state["candidates"]["memory_leak::pricing"] == 0.9
    assert state["candidates"]["cascading_timeout::pricing"] == 0.0
    assert state["top_candidate"] == "memory_leak::pricing"


def test_cascade_raises_on_empty_assessments():
    cascade = ConfidenceGatedCascade(jev_client=LocalJevStandIn())
    with pytest.raises(ValueError):
        cascade.evaluate([])


# ---------------------------------------------------------------------------
# Integration: full pipeline, all 5 labeled scenarios
# ---------------------------------------------------------------------------


def _run_cascade_pipeline(tmp_path, scenario_key):
    fixture = build_scenario_fixture(tmp_path, scenario_key)
    runtime = RCAAgentRuntime(
        query_engine=fixture.query_engine, topology_catalog={fixture.tenant_id: fixture.topology}
    )
    truth = fixture.truth
    alert_service = truth.affected_services[0] if truth.affected_services else fixture.topology.services[0]
    alert = Alert(
        tenant_id=fixture.tenant_id,
        service=alert_service,
        severity="ERROR",
        message=f"elevated error rate detected on {alert_service}",
        timestamp=truth.injected_at + timedelta(seconds=30),
    )
    cascade = ConfidenceGatedCascade(jev_client=LocalJevStandIn(), jury=JuryPanel())
    result = runtime.investigate_with_cascade(alert, fixture.scope, cascade)
    return fixture, result


def test_cascade_pipeline_surfaces_correct_root_cause_for_every_scenario(tmp_path):
    for scenario_key in ALL_SCENARIO_KEYS:
        fixture, result = _run_cascade_pipeline(tmp_path, scenario_key)
        top = result.top_assessment
        assert top.hypothesis.root_cause_type == fixture.truth.root_cause_type, scenario_key
        assert top.hypothesis.candidate_label == fixture.truth.root_cause_service, scenario_key
        assert result.cascade_result.jev_agrees_with_rule_engine is True, scenario_key


def test_cascade_pipeline_exercises_both_routing_paths_across_scenarios(tmp_path):
    """Across this repo's 5 canonical labeled scenarios, both the fast
    Jev-only path and the Jev+jury-appeal path are actually exercised --
    not just theoretically possible."""
    paths_seen = set()
    for scenario_key in ALL_SCENARIO_KEYS:
        _fixture, result = _run_cascade_pipeline(tmp_path, scenario_key)
        paths_seen.add(result.cascade_result.path)

    assert CascadePath.JEV_ONLY in paths_seen
    assert CascadePath.JEV_PLUS_JURY in paths_seen


def test_cascade_pipeline_audit_trail_records_which_path_was_used(tmp_path):
    for scenario_key in ALL_SCENARIO_KEYS:
        _fixture, result = _run_cascade_pipeline(tmp_path, scenario_key)

        verification = result.audit_log.verify_chain()
        assert verification.ok is True, scenario_key

        jev_entries = [e for e in result.audit_log.entries if e.entry_type.value == "jev_verdict"]
        assert len(jev_entries) == 1, scenario_key
        recorded_path = jev_entries[0].payload["path"]
        assert recorded_path == result.cascade_result.path.value, scenario_key

        jury_entries = [e for e in result.audit_log.entries if e.entry_type.value == "jury_verdict"]
        if result.cascade_result.path == CascadePath.JEV_ONLY:
            assert len(jury_entries) == 0, scenario_key
        else:
            assert len(jury_entries) == 1, scenario_key

        final_entries = [e for e in result.audit_log.entries if e.entry_type.value == "final_decision"]
        assert len(final_entries) == 1, scenario_key
        assert final_entries[0].payload["path"] == result.cascade_result.path.value, scenario_key


def test_cascade_pipeline_never_leaks_raw_telemetry_into_jev_or_audit_payloads(tmp_path):
    _fixture, result = _run_cascade_pipeline(tmp_path, "downstream_dependency_outage")
    needle = "connection refused calling dependency"
    for entry in result.audit_log.entries:
        assert needle not in str(entry.payload)
