"""Typed models for the LLM-jury evaluator gate."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class JudgeVerdict(BaseModel):
    """One judge's independent vote on whether the top-ranked hypothesis is
    well-supported by the gathered evidence."""

    judge_id: str
    supports: bool
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


class JuryVerdict(BaseModel):
    """Aggregated panel outcome: majority vote + mean confidence + any
    dissenting rationale, so disagreement is visible rather than smoothed
    away by a single pass/fail number."""

    votes: list[JudgeVerdict]
    consensus_supports: bool
    mean_confidence: float
    agreement_ratio: float
    """Fraction of judges that voted with the majority."""
    dissent_notes: list[str] = Field(default_factory=list)


class FinalDecision(BaseModel):
    """The combined rule-engine + jury outcome."""

    action: str
    """One of: 'auto_remediate', 'alert', 'escalate'."""
    rule_confidence: float
    jury_confidence: float
    rule_matched: bool
    jury_supports: bool
    rationale: str
    root_cause_type: Optional[str] = None
    candidate_label: Optional[str] = None
