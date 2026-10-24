"""Combines the deterministic rule-engine confidence with the jury
consensus into one final, auditable decision.

Policy (deliberately simple and explainable, not a learned/opaque score):
  - Both the rule engine and the jury agree the hypothesis is supported,
    and both are highly confident -> auto_remediate.
  - Both agree it is supported, but confidence is only moderate -> alert
    (surface to a human dashboard, do not act automatically).
  - They disagree, or either side's confidence is low -> escalate to a
    human-in-the-loop reviewer.
"""

from __future__ import annotations

from typing import Optional

from evaluator.models import FinalDecision, JuryVerdict
from rules.models import RuleVerdict

DEFAULT_AUTO_REMEDIATE_THRESHOLD = 0.8
DEFAULT_LOW_CONFIDENCE_THRESHOLD = 0.5


def combine_decision(
    rule_verdict: RuleVerdict,
    jury_verdict: JuryVerdict,
    *,
    auto_remediate_threshold: float = DEFAULT_AUTO_REMEDIATE_THRESHOLD,
    low_confidence_threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
    root_cause_type: Optional[str] = None,
    candidate_label: Optional[str] = None,
) -> FinalDecision:
    both_agree = rule_verdict.matched and jury_verdict.consensus_supports
    both_low_confidence = (
        rule_verdict.confidence < low_confidence_threshold
        or jury_verdict.mean_confidence < low_confidence_threshold
    )

    if both_agree and not both_low_confidence:
        if (
            rule_verdict.confidence >= auto_remediate_threshold
            and jury_verdict.mean_confidence >= auto_remediate_threshold
        ):
            action = "auto_remediate"
            rationale = (
                "rule engine and jury both support the hypothesis with high confidence "
                f"(rule={rule_verdict.confidence:.2f}, jury={jury_verdict.mean_confidence:.2f})"
            )
        else:
            action = "alert"
            rationale = (
                "rule engine and jury agree the hypothesis is supported, but confidence is "
                f"moderate (rule={rule_verdict.confidence:.2f}, jury={jury_verdict.mean_confidence:.2f}); "
                "surfacing for review rather than auto-remediating"
            )
    else:
        action = "escalate"
        if not both_agree:
            rationale = (
                f"rule engine (matched={rule_verdict.matched}) and jury "
                f"(consensus_supports={jury_verdict.consensus_supports}) disagree; "
                "escalating to human-in-the-loop"
            )
        else:
            rationale = (
                "rule engine and jury agree but confidence is below the low-confidence "
                f"threshold ({low_confidence_threshold}); escalating to human-in-the-loop"
            )

    return FinalDecision(
        action=action,
        rule_confidence=rule_verdict.confidence,
        jury_confidence=jury_verdict.mean_confidence,
        rule_matched=rule_verdict.matched,
        jury_supports=jury_verdict.consensus_supports,
        rationale=rationale,
        root_cause_type=root_cause_type,
        candidate_label=candidate_label,
    )
