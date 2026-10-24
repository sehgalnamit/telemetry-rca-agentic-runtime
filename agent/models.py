"""Typed models for the RCA agent runtime's inputs and outputs."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Alert(BaseModel):
    """An incoming anomaly/alert that triggers the agentic RCA loop."""

    tenant_id: str
    service: str
    severity: str
    message: str
    timestamp: datetime


class Hypothesis(BaseModel):
    """A candidate root-cause explanation the agent will gather evidence for."""

    hypothesis_id: str
    root_cause_type: str
    candidate_service: Optional[str]
    """The tenant's own service hypothesized as the root cause, or ``None``
    when the hypothesis targets an external dependency (not an instrumented
    service -- see ``downstream_dependency_outage``)."""
    candidate_label: str
    services_to_investigate: list[str] = Field(default_factory=list)
    rationale: str
