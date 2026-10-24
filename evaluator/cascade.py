"""Confidence-Gated Routing cascade for root-cause hypotheses.

Implements TypeSafe's own documented architectural pattern,
"Confidence-Gated Routing" (https://docs.typesafe.ai/patterns/confidence-routing),
as the primary validation gate for this repo's ranked RCA hypotheses:

    [ RCA Hypothesis ] -> [ Jev Confidence-Gated Decision Gate ]
                                   |
         --(high confidence, agrees with rule engine)--> auto accept/escalate
                                   |
         --(low/ambiguous confidence, OR disagreement
            with the rule engine)--> [ LLM-Jury appeals court ]
                                      -> final verdict + rationale + audit

High confidence + agreement is the fast, cheap, real-time path appropriate
for production self-healing monitoring: Jev alone gates the decision and
the (comparatively slower, more expensive) LLM-jury is never invoked. Low
confidence or disagreement escalates to the existing jury
(``evaluator.jury.JuryPanel``) as an "appeals court" for a nuanced textual
rationale -- exactly the "medium/low confidence -> fall back to a human or
a different system" half of TypeSafe's documented pattern.

Only metadata (candidate labels + rule-engine confidence scores) is ever
placed in the ``state`` handed to the Jev client -- never raw tenant
telemetry rows, preserving the zero-copy principle used throughout
``federation/`` and ``audit/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Optional

from evaluator.decision import (
    DEFAULT_AUTO_REMEDIATE_THRESHOLD,
    DEFAULT_LOW_CONFIDENCE_THRESHOLD,
    combine_decision,
)
from evaluator.jev_gate import ChoiceAnswer, ChoiceQuestion, JevClient, ScoreAnswer, ScoreQuestion
from evaluator.jury import JuryPanel
from evaluator.models import FinalDecision, JuryVerdict

if TYPE_CHECKING:
    from agent.runtime import HypothesisAssessment

DEFAULT_SCORE_RUBRIC = [
    "unsupported",
    "weakly_supported",
    "moderately_supported",
    "strongly_supported",
    "fully_supported",
]


class CascadePath(str, Enum):
    JEV_ONLY = "jev_only"
    JEV_PLUS_JURY = "jev_plus_jury"


@dataclass
class CascadeResult:
    path: CascadePath
    jev_choice_answer: ChoiceAnswer
    jev_score_answer: ScoreAnswer
    jev_agrees_with_rule_engine: bool
    jev_high_confidence: bool
    jury_verdict: Optional[JuryVerdict]
    final_decision: FinalDecision
    rationale: str


def _hypothesis_key(assessment: "HypothesisAssessment") -> str:
    """Composite key used for Jev's Choice question options and for the
    agreement check. ``candidate_label`` alone is not always unique (the
    same service can be the candidate for several different hypothesis
    *types*, e.g. both a ``memory_leak`` and a ``cascading_timeout``
    hypothesis can both target service ``pricing``), so collapsing to just
    the label would silently merge distinct hypotheses together."""
    return f"{assessment.hypothesis.root_cause_type}::{assessment.hypothesis.candidate_label}"


class ConfidenceGatedCascade:
    """Routes a ranked set of hypothesis assessments through Jev first, and
    only escalates to the LLM-jury when Jev's confidence is low/ambiguous
    or it disagrees with the deterministic rule engine's top pick."""

    def __init__(
        self,
        jev_client: JevClient,
        jury: Optional[JuryPanel] = None,
        high_confidence_threshold: float = 0.75,
        score_rubric: Optional[list[str]] = None,
        auto_remediate_threshold: float = DEFAULT_AUTO_REMEDIATE_THRESHOLD,
        low_confidence_threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
        max_candidates: int = 5,
    ):
        self.jev_client = jev_client
        self.jury = jury or JuryPanel()
        self.high_confidence_threshold = high_confidence_threshold
        self.score_rubric = score_rubric or list(DEFAULT_SCORE_RUBRIC)
        self.auto_remediate_threshold = auto_remediate_threshold
        self.low_confidence_threshold = low_confidence_threshold
        self.max_candidates = max_candidates

    def _build_state_and_questions(self, assessments: list["HypothesisAssessment"]):
        top = assessments[0]
        top_key = _hypothesis_key(top)
        candidates = {
            _hypothesis_key(a): a.rule_verdict.confidence for a in assessments[: self.max_candidates]
        }
        # Ensure the top candidate is always present even if max_candidates
        # somehow excluded it (shouldn't happen since assessments are
        # pre-sorted, but keep this invariant explicit and cheap to check).
        candidates.setdefault(top_key, top.rule_verdict.confidence)

        state = {
            "candidates": candidates,
            "top_candidate": top_key,
        }
        questions = {
            "top_choice": ChoiceQuestion(
                instructions=(
                    "Which candidate root-cause hypothesis is best supported by the "
                    "gathered evidence (rule-engine confidence per candidate is provided)?"
                ),
                criteria={
                    key: f"root cause type '{key.split('::', 1)[0]}' on service "
                    f"'{key.split('::', 1)[1]}'"
                    for key in candidates
                },
            ),
            "top_score": ScoreQuestion(
                instructions=(
                    "How well-supported is the top-ranked hypothesis by the gathered "
                    "evidence, on a rubric from unsupported to fully supported?"
                ),
                criteria=self.score_rubric,
            ),
        }
        return state, questions

    def evaluate(self, assessments: list["HypothesisAssessment"]) -> CascadeResult:
        if not assessments:
            raise ValueError("cannot evaluate an empty assessment list")

        top = assessments[0]
        state, questions = self._build_state_and_questions(assessments)
        answers = self.jev_client.system_one(state=state, questions=questions)
        choice_answer = answers["top_choice"]
        score_answer = answers["top_score"]
        assert isinstance(choice_answer, ChoiceAnswer)
        assert isinstance(score_answer, ScoreAnswer)

        agrees = choice_answer.choice == _hypothesis_key(top)
        jev_high_confidence = (
            choice_answer.confidence >= self.high_confidence_threshold
            and score_answer.confidence >= self.high_confidence_threshold
        )

        if agrees and jev_high_confidence:
            # Fast path: Jev alone gates the decision; the jury is never
            # invoked. We still reuse the existing, already-tested
            # rule+jury combination policy by synthesizing a
            # jury-verdict-shaped object from Jev's own answers, so the
            # auto_remediate/alert thresholds stay one single source of
            # truth regardless of which gate produced the confidence.
            pseudo_jury_verdict = JuryVerdict(
                votes=[],
                consensus_supports=True,
                mean_confidence=(choice_answer.confidence + score_answer.confidence) / 2.0,
                agreement_ratio=1.0,
                dissent_notes=[],
            )
            final_decision = combine_decision(
                top.rule_verdict,
                pseudo_jury_verdict,
                auto_remediate_threshold=self.auto_remediate_threshold,
                low_confidence_threshold=self.low_confidence_threshold,
                root_cause_type=top.hypothesis.root_cause_type,
                candidate_label=top.hypothesis.candidate_label,
            )
            rationale = (
                f"Jev agreed with the rule engine's top pick ('{top.hypothesis.candidate_label}') "
                f"with high confidence (choice={choice_answer.confidence:.2f}, "
                f"score={score_answer.confidence:.2f} >= {self.high_confidence_threshold}); "
                "jury appeals court was not invoked."
            )
            return CascadeResult(
                path=CascadePath.JEV_ONLY,
                jev_choice_answer=choice_answer,
                jev_score_answer=score_answer,
                jev_agrees_with_rule_engine=agrees,
                jev_high_confidence=jev_high_confidence,
                jury_verdict=None,
                final_decision=final_decision,
                rationale=rationale,
            )

        # Slow path: escalate to the jury appeals court.
        jury_verdict = self.jury.convene(top.rule_verdict, top.bundle)
        final_decision = combine_decision(
            top.rule_verdict,
            jury_verdict,
            auto_remediate_threshold=self.auto_remediate_threshold,
            low_confidence_threshold=self.low_confidence_threshold,
            root_cause_type=top.hypothesis.root_cause_type,
            candidate_label=top.hypothesis.candidate_label,
        )
        if not agrees:
            reason = (
                f"Jev chose '{choice_answer.choice}' while the rule engine's top pick was "
                f"'{top.hypothesis.candidate_label}' -- disagreement"
            )
        else:
            reason = (
                f"Jev confidence was low/ambiguous (choice={choice_answer.confidence:.2f}, "
                f"score={score_answer.confidence:.2f}, threshold={self.high_confidence_threshold})"
            )
        rationale = f"{reason}; escalated to the LLM-jury appeals court."
        return CascadeResult(
            path=CascadePath.JEV_PLUS_JURY,
            jev_choice_answer=choice_answer,
            jev_score_answer=score_answer,
            jev_agrees_with_rule_engine=agrees,
            jev_high_confidence=jev_high_confidence,
            jury_verdict=jury_verdict,
            final_decision=final_decision,
            rationale=rationale,
        )
