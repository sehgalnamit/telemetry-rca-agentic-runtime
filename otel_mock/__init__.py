"""OTel Mock Generator.

Generates realistic-looking synthetic OpenTelemetry logs, traces, and metrics
for a simulated multi-tenant, multi-service environment, with the ability to
inject labeled failure scenarios carrying ground-truth root-cause metadata.
"""

from otel_mock.generator import TelemetryGenerator
from otel_mock.models import GroundTruth, LogRecord, MetricPoint, TenantTopology, TraceSpan
from otel_mock.scenarios import SCENARIOS, FailureScenario
from otel_mock.topology import DEMO_TOPOLOGIES

__all__ = [
    "TelemetryGenerator",
    "GroundTruth",
    "LogRecord",
    "MetricPoint",
    "TraceSpan",
    "TenantTopology",
    "FailureScenario",
    "SCENARIOS",
    "DEMO_TOPOLOGIES",
]
