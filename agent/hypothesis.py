"""Hypothesis generation ("triage" + "hypothesize" steps of the agentic loop).

Given an :class:`~agent.models.Alert` and the tenant's service-dependency
*topology metadata* (never raw telemetry), proposes a bounded set of
candidate root-cause hypotheses worth gathering evidence for. Topology is
structural metadata the control plane legitimately knows (akin to a schema
catalog entry), distinct from the tenant's actual telemetry rows, which stay
in the tenant's own store until a scoped federated query is issued.
"""

from __future__ import annotations

import uuid

from agent.models import Alert, Hypothesis
from common.graph import (
    callers_of,
    external_dependencies_of,
    shared_node_unrelated_peers,
    transitive_callers_of,
)
from otel_mock.models import TenantTopology


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def generate_hypotheses(topology: TenantTopology, alert: Alert) -> list[Hypothesis]:
    """Propose candidate root-cause hypotheses for ``alert``.

    Candidates considered: the alerting service itself, and its direct
    dependencies (any of which may be where a problem actually originates),
    for the "self-contained" scenario types; the alerting service's direct
    external dependencies for outage hypotheses; and same-node/unrelated
    peers of the alerting service for noisy-neighbor hypotheses.
    """
    hypotheses: list[Hypothesis] = []
    candidates = [alert.service] + list(topology.dependencies.get(alert.service, []))
    candidates = [c for c in candidates if c in topology.services]
    seen_candidates: set[str] = set()
    for candidate in candidates:
        if candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)

        affected = {candidate} | transitive_callers_of(topology.dependencies, candidate)
        hypotheses.append(
            Hypothesis(
                hypothesis_id=_new_id("hyp"),
                root_cause_type="cascading_timeout",
                candidate_service=candidate,
                candidate_label=candidate,
                services_to_investigate=sorted(affected),
                rationale=(
                    f"'{candidate}' may be experiencing elevated latency that cascades "
                    f"into its upstream callers {sorted(affected - {candidate})}."
                ),
            )
        )
        hypotheses.append(
            Hypothesis(
                hypothesis_id=_new_id("hyp"),
                root_cause_type="memory_leak",
                candidate_service=candidate,
                candidate_label=candidate,
                services_to_investigate=[candidate],
                rationale=f"'{candidate}' may be exhibiting a gradual memory leak.",
            )
        )
        direct_callers = set(callers_of(topology.dependencies, candidate))
        hypotheses.append(
            Hypothesis(
                hypothesis_id=_new_id("hyp"),
                root_cause_type="bad_deploy",
                candidate_service=candidate,
                candidate_label=candidate,
                services_to_investigate=sorted({candidate} | direct_callers),
                rationale=f"'{candidate}' may have received a bad deploy/config change.",
            )
        )

    for candidate in seen_candidates:
        for dependency in external_dependencies_of(
            topology.dependencies, topology.services, candidate
        ):
            callers = set(callers_of(topology.dependencies, dependency)) | {candidate}
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=_new_id("hyp"),
                    root_cause_type="downstream_dependency_outage",
                    candidate_service=None,
                    candidate_label=dependency,
                    services_to_investigate=sorted(callers),
                    rationale=(
                        f"External dependency '{dependency}' may be unavailable, "
                        f"causing failures in its callers {sorted(callers)}."
                    ),
                )
            )

    for peer in shared_node_unrelated_peers(topology.nodes, topology.dependencies, alert.service):
        hypotheses.append(
            Hypothesis(
                hypothesis_id=_new_id("hyp"),
                root_cause_type="noisy_neighbor",
                candidate_service=peer,
                candidate_label=peer,
                services_to_investigate=sorted({peer, alert.service}),
                rationale=(
                    f"'{peer}' shares infrastructure with '{alert.service}' with no "
                    "logical call dependency between them, suggesting resource contention."
                ),
            )
        )

    return hypotheses
