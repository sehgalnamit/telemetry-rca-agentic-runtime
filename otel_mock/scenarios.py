"""Catalog of injectable, labeled failure scenarios.

Each :class:`FailureScenario` is pure metadata (key, human-readable name, the
root-cause *type* a correct RCA pipeline should surface, and a description).
The actual synthetic-signal injection logic lives in
:mod:`otel_mock.generator`, keyed off ``FailureScenario.key``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FailureScenario:
    key: str
    name: str
    root_cause_type: str
    description: str


CASCADING_TIMEOUT = FailureScenario(
    key="cascading_timeout",
    name="Cascading Timeout",
    root_cause_type="cascading_timeout",
    description=(
        "A root-cause service becomes slow; every upstream caller's spans "
        "inherit elevated latency and eventually time out, producing a "
        "cascade of ERROR spans and WARN/ERROR logs across the call chain."
    ),
)

MEMORY_LEAK = FailureScenario(
    key="memory_leak",
    name="Memory Leak",
    root_cause_type="memory_leak",
    description=(
        "A service's heap/RSS metric climbs monotonically across the "
        "window. As it approaches a ceiling, GC-pause-like latency spikes "
        "and eventual error/restart log lines appear on that same service."
    ),
)

BAD_DEPLOY = FailureScenario(
    key="bad_deploy",
    name="Bad Deploy / Config Change",
    root_cause_type="bad_deploy",
    description=(
        "A service's deploy_version/config attribute flips mid-window. "
        "Immediately after the flip, that service's error rate jumps for "
        "the remainder of the window while other services are unaffected "
        "except through normal call-chain error propagation."
    ),
)

DOWNSTREAM_DEPENDENCY_OUTAGE = FailureScenario(
    key="downstream_dependency_outage",
    name="Downstream Dependency Outage",
    root_cause_type="downstream_dependency_outage",
    description=(
        "An external/downstream dependency becomes fully unavailable for a "
        "sub-window. Every service that calls it directly fails fast with "
        "connection-refused-style errors; the dependency itself emits no "
        "telemetry of its own (it is external to the tenant)."
    ),
)

NOISY_NEIGHBOR = FailureScenario(
    key="noisy_neighbor",
    name="Noisy Neighbor Resource Contention",
    root_cause_type="noisy_neighbor",
    description=(
        "A co-located but functionally unrelated service spikes CPU/IO "
        "metrics on a shared node. An unrelated service sharing that node "
        "label shows correlated latency/error degradation despite no "
        "logical dependency between the two, pointing at infra contention "
        "rather than application logic."
    ),
)

SCENARIOS: dict[str, FailureScenario] = {
    s.key: s
    for s in (
        CASCADING_TIMEOUT,
        MEMORY_LEAK,
        BAD_DEPLOY,
        DOWNSTREAM_DEPENDENCY_OUTAGE,
        NOISY_NEIGHBOR,
    )
}
