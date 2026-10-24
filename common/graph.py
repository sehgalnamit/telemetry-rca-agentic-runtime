"""Reusable, generic call-graph helpers.

Deliberately separate from ``otel_mock`` (which knows the injected ground
truth) and from ``agent`` (which only ever sees telemetry + topology
metadata): these are plain graph algorithms over a service dependency map,
shared because both the synthetic-data injector and the RCA agent need to
reason about "who calls whom" -- never about injected failure semantics.
"""

from __future__ import annotations


def callers_of(dependencies: dict[str, list[str]], target: str) -> list[str]:
    """Direct upstream services that call ``target``."""
    return [svc for svc, deps in dependencies.items() if target in deps]


def transitive_callers_of(dependencies: dict[str, list[str]], target: str) -> set[str]:
    seen: set[str] = set()
    frontier = [target]
    while frontier:
        current = frontier.pop()
        for caller in callers_of(dependencies, current):
            if caller not in seen:
                seen.add(caller)
                frontier.append(caller)
    return seen


def external_dependencies_of(
    dependencies: dict[str, list[str]], services: list[str], service: str
) -> list[str]:
    """Dependencies of ``service`` that are not themselves known/instrumented
    services -- i.e. external systems the control plane holds no telemetry
    for directly, only caller-side evidence."""
    return [d for d in dependencies.get(service, []) if d not in services]


def shared_node_unrelated_peers(
    nodes: dict[str, str], dependencies: dict[str, list[str]], service: str
) -> list[str]:
    """Services that share ``service``'s infra node but have no direct call
    dependency to/from it in either direction (candidate noisy-neighbor
    peers)."""
    node = nodes.get(service)
    if node is None:
        return []
    peers = []
    for svc, svc_node in nodes.items():
        if svc == service or svc_node != node:
            continue
        if svc in dependencies.get(service, []) or service in dependencies.get(svc, []):
            continue
        peers.append(svc)
    return peers
