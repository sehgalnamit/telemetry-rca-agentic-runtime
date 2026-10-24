"""Tests for the LLM-jury evaluator gate: panel aggregation, dissent
tracking, and the rule+jury decision-combination policy."""

from __future__ import annotations

from datetime import datetime, timezone

from evaluator import ConservativeSkepticJudge, EvidenceCoverageJudge, JuryPanel, RuleAlignmentJudge, combine_decision
from evaluator.models import JuryVerdict
from rules.features import EvidenceBundle
from rules.models import RuleVerdict


def _bundle() -> EvidenceBundle:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return EvidenceBundle(
        tenant_id="acme",
        primary_service="pricing",
        primary_label="pricing",
        baseline_start=now,
        baseline_end=now,
        incident_start=now,
        incident_end=now,
    )


def test_jury_unanimous_support_yields_high_consensus():
    verdict = RuleVerdict(
        rule_id="cascading_timeout_v1",
        root_cause_type="cascading_timeout",
        candidate_service="pricing",
        matched=True,
        confidence=0.9,
        matched_signals=["a", "b", "c"],
        unmatched_signals=[],
        rationale="all signals matched",
    )
    jury = JuryPanel()
    result = jury.convene(verdict, _bundle())

    assert result.consensus_supports is True
    assert result.mean_confidence > 0.5
    assert result.agreement_ratio == 1.0
    assert result.dissent_notes == []


def test_conservative_judge_dissents_on_partial_match():
    """When one (optional) signal didn't match, the rule still fires (ratio
    above its threshold) but the stricter ConservativeSkepticJudge should
    withhold support -- this is the designed diversity/dissent behavior."""
    verdict = RuleVerdict(
        rule_id="memory_leak_v1",
        root_cause_type="memory_leak",
        candidate_service="pricing",
        matched=True,
        confidence=0.57,
        matched_signals=["metric_trend_ratio_gte:primary:0", "metric_increasing:primary:1"],
        unmatched_signals=["log_pattern:primary:2"],
        rationale="2/3 signals matched",
    )
    jury = JuryPanel()
    result = jury.convene(verdict, _bundle())

    skeptic_votes = [v for v in result.votes if v.judge_id == ConservativeSkepticJudge.judge_id]
    assert len(skeptic_votes) == 1
    assert skeptic_votes[0].supports is False
    assert any("conservative_skeptic" in note for note in result.dissent_notes)


def test_jury_rejects_unmatched_rule():
    verdict = RuleVerdict(
        rule_id="bad_deploy_v1",
        root_cause_type="bad_deploy",
        candidate_service="pricing",
        matched=False,
        confidence=0.0,
        matched_signals=[],
        unmatched_signals=["attribute_changed:primary:0"],
        rationale="no signals matched",
    )
    jury = JuryPanel(judges=[RuleAlignmentJudge(), EvidenceCoverageJudge()])
    result = jury.convene(verdict, _bundle())

    assert result.consensus_supports is False
    assert result.mean_confidence == 0.0


def test_combine_decision_auto_remediate_when_both_high_confidence():
    rule_verdict = RuleVerdict(
        rule_id="r", root_cause_type="cascading_timeout", candidate_service="svc",
        matched=True, confidence=0.9, matched_signals=["a"], unmatched_signals=[], rationale="x",
    )
    jury_verdict = JuryVerdict(votes=[], consensus_supports=True, mean_confidence=0.9, agreement_ratio=1.0)

    decision = combine_decision(rule_verdict, jury_verdict)
    assert decision.action == "auto_remediate"


def test_combine_decision_alert_when_moderate_confidence():
    rule_verdict = RuleVerdict(
        rule_id="r", root_cause_type="memory_leak", candidate_service="svc",
        matched=True, confidence=0.6, matched_signals=["a"], unmatched_signals=[], rationale="x",
    )
    jury_verdict = JuryVerdict(votes=[], consensus_supports=True, mean_confidence=0.6, agreement_ratio=1.0)

    decision = combine_decision(rule_verdict, jury_verdict)
    assert decision.action == "alert"


def test_combine_decision_escalates_on_disagreement():
    rule_verdict = RuleVerdict(
        rule_id="r", root_cause_type="noisy_neighbor", candidate_service="svc",
        matched=True, confidence=0.9, matched_signals=["a"], unmatched_signals=[], rationale="x",
    )
    jury_verdict = JuryVerdict(votes=[], consensus_supports=False, mean_confidence=0.3, agreement_ratio=0.67)

    decision = combine_decision(rule_verdict, jury_verdict)
    assert decision.action == "escalate"


def test_combine_decision_escalates_on_low_confidence_even_if_both_agree():
    rule_verdict = RuleVerdict(
        rule_id="r", root_cause_type="noisy_neighbor", candidate_service="svc",
        matched=True, confidence=0.3, matched_signals=["a"], unmatched_signals=[], rationale="x",
    )
    jury_verdict = JuryVerdict(votes=[], consensus_supports=True, mean_confidence=0.3, agreement_ratio=1.0)

    decision = combine_decision(rule_verdict, jury_verdict)
    assert decision.action == "escalate"
