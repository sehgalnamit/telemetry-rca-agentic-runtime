"""Typed models for the deterministic rule DSL.

Rules are declarative YAML documents (see ``rules/definitions/default_rules.yaml``)
describing a *signature*: a set of conditions over evidence features that, if
all (or a configured fraction of) they hold, indicate a specific root-cause
type with a fixed, auditable confidence score.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

SignalTarget = Literal["primary", "other"]

SignalType = Literal[
    "latency_ratio_gte",
    "error_rate_incident_gte",
    "error_rate_delta_gte",
    "log_pattern",
    "metric_trend_ratio_gte",
    "metric_increasing",
    "attribute_changed",
    "metric_incident_max_gte",
    "peer_shared_node_degraded",
]


class SignalCondition(BaseModel):
    """A single, atomic condition evaluated against evidence features."""

    type: SignalType
    target: SignalTarget = "primary"
    gte: Optional[float] = None
    factor_gte: Optional[float] = None
    metric_name: Optional[str] = None
    attribute: Optional[str] = None
    pattern: Optional[str] = None
    severity: Optional[str] = None
    required: bool = True
    """If False, this signal contributes to the match ratio but a rule can
    still fire with it unmatched, as long as ``min_match_ratio`` is met."""


class Rule(BaseModel):
    id: str
    root_cause_type: str
    description: str
    confidence: float = Field(ge=0.0, le=1.0)
    signals: list[SignalCondition]
    min_match_ratio: float = 1.0
    """Fraction of signals (weighted equally) that must match for the rule
    to fire. Required signals that don't match always veto the rule
    regardless of this ratio."""


class RuleVerdict(BaseModel):
    """Deterministic, explainable output of evaluating one rule."""

    rule_id: str
    root_cause_type: str
    candidate_service: str
    matched: bool
    confidence: float
    matched_signals: list[str]
    unmatched_signals: list[str]
    rationale: str
