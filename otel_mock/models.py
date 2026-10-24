"""Typed data models for synthetic OTel-like telemetry."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Severity(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    FATAL = "FATAL"


class SpanStatus(str, Enum):
    OK = "OK"
    ERROR = "ERROR"
    UNSET = "UNSET"


class LogRecord(BaseModel):
    """A single synthetic OTel log record, scoped to one tenant."""

    tenant_id: str
    service: str
    timestamp: datetime
    severity: Severity
    message: str
    trace_id: Optional[str] = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class TraceSpan(BaseModel):
    """A single synthetic OTel span, scoped to one tenant."""

    tenant_id: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    service: str
    operation: str
    start_time: datetime
    duration_ms: float
    status: SpanStatus = SpanStatus.OK
    attributes: dict[str, Any] = Field(default_factory=dict)


class MetricPoint(BaseModel):
    """A single synthetic OTel metric data point, scoped to one tenant."""

    tenant_id: str
    service: str
    metric_name: str
    timestamp: datetime
    value: float
    unit: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)


class TenantTopology(BaseModel):
    """Describes a tenant's simulated microservice graph.

    ``dependencies`` maps a service name to the list of downstream services it
    calls (i.e. an edge ``a -> b`` means ``a`` depends on ``b``).
    """

    tenant_id: str
    services: list[str]
    dependencies: dict[str, list[str]] = Field(default_factory=dict)
    nodes: dict[str, str] = Field(default_factory=dict)
    """Optional service -> shared-infra-node mapping, used by scenarios like
    noisy-neighbor resource contention where two logically-unrelated services
    happen to share underlying compute."""

    def downstream_of(self, service: str) -> list[str]:
        return self.dependencies.get(service, [])


class GroundTruth(BaseModel):
    """Ground-truth root-cause label attached to an injected failure scenario.

    This is never consumed by the RCA pipeline itself -- it exists purely so
    tests / the demo script can score pipeline output against a known answer.
    """

    scenario: str
    tenant_id: str
    root_cause_service: str
    root_cause_type: str
    affected_services: list[str]
    description: str
    injected_at: datetime
    window_start: datetime
    window_end: datetime
