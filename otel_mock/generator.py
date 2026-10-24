"""Deterministic synthetic telemetry generator with failure-scenario injection.

The generator produces baseline (healthy) telemetry for every service in a
tenant's :class:`~otel_mock.models.TenantTopology`, and can additionally
inject one labeled :class:`~otel_mock.scenarios.FailureScenario` into a
sub-window of the generated time range. Everything is seeded so that the
same ``(topology, seed, scenario)`` always reproduces byte-identical output,
which is required for the pipeline-scoring tests.

Per-timestamp telemetry is built as a *request tree*: for every top-level
service, a synthetic request recursively fans out into that service's
declared dependencies, producing a connected set of spans (and, on error,
logs) that share a single ``trace_id``. Failure-scenario modifiers (error
probability boost, latency multiplier, forced errors, extra log lines) are
always expressed as **per-service dictionaries** keyed by service name and
looked up using the service actually being emitted at each recursion level
-- never inherited as a value from whichever top-level service started the
recursion. This avoids diluting a scenario's signal when the affected
service is reached indirectly (e.g. service A's request tree calling into
the root-cause service two hops down).
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta
from typing import Optional

from otel_mock.models import (
    GroundTruth,
    LogRecord,
    MetricPoint,
    Severity,
    SpanStatus,
    TenantTopology,
    TraceSpan,
)
from otel_mock.scenarios import SCENARIOS

_BASE_LATENCY_MIN_MS = 20.0
_BASE_LATENCY_MAX_MS = 120.0


def _stable_unit_interval(key: str) -> float:
    """Deterministic pseudo-random float in [0, 1) derived from a string key."""
    h = 0
    for ch in key:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return (h % 10_000) / 10_000.0


def _baseline_latency_ms(service: str) -> float:
    frac = _stable_unit_interval(service)
    return _BASE_LATENCY_MIN_MS + frac * (_BASE_LATENCY_MAX_MS - _BASE_LATENCY_MIN_MS)


def _callers_of(topology: TenantTopology, target: str) -> list[str]:
    """Direct upstream services that call ``target``."""
    return [svc for svc, deps in topology.dependencies.items() if target in deps]


def _transitive_callers_of(topology: TenantTopology, target: str) -> set[str]:
    seen: set[str] = set()
    frontier = [target]
    while frontier:
        current = frontier.pop()
        for caller in _callers_of(topology, current):
            if caller not in seen:
                seen.add(caller)
                frontier.append(caller)
    return seen


class TelemetryGenerator:
    """Generates synthetic OTel-like telemetry for a single tenant."""

    def __init__(self, topology: TenantTopology, seed: int = 42):
        self.topology = topology
        self.seed = seed
        self._rng = random.Random(seed)

    # ------------------------------------------------------------------
    # Baseline (healthy) generation
    # ------------------------------------------------------------------
    def generate_baseline(
        self,
        start: datetime,
        end: datetime,
        interval_s: int = 15,
    ) -> tuple[list[LogRecord], list[TraceSpan], list[MetricPoint]]:
        logs: list[LogRecord] = []
        traces: list[TraceSpan] = []
        metrics: list[MetricPoint] = []

        t = start
        while t < end:
            for service in self.topology.services:
                self._emit_request_tree_for_entry(service, t, logs, traces)
                metrics.extend(self._baseline_metrics_for(service, t))
            t += timedelta(seconds=interval_s)
        return logs, traces, metrics

    def _baseline_metrics_for(self, service: str, t: datetime) -> list[MetricPoint]:
        tenant = self.topology.tenant_id
        base_cpu = 20.0 + 30.0 * _stable_unit_interval(service + "cpu")
        base_mem = 128.0 + 256.0 * _stable_unit_interval(service + "mem")

        def jitter(scale: float) -> float:
            return self._rng.uniform(-scale, scale)

        return [
            MetricPoint(
                tenant_id=tenant,
                service=service,
                metric_name="cpu_utilization_pct",
                timestamp=t,
                value=max(0.0, base_cpu + jitter(3.0)),
                unit="percent",
            ),
            MetricPoint(
                tenant_id=tenant,
                service=service,
                metric_name="memory_rss_mb",
                timestamp=t,
                value=max(0.0, base_mem + jitter(5.0)),
                unit="MB",
            ),
            MetricPoint(
                tenant_id=tenant,
                service=service,
                metric_name="request_latency_ms_p99",
                timestamp=t,
                value=max(1.0, _baseline_latency_ms(service) + jitter(5.0)),
                unit="ms",
            ),
            MetricPoint(
                tenant_id=tenant,
                service=service,
                metric_name="error_rate",
                timestamp=t,
                value=max(0.0, 0.01 + jitter(0.005)),
                unit="ratio",
            ),
        ]

    def _emit_request_tree_for_entry(
        self,
        entry_service: str,
        timestamp: datetime,
        logs: list[LogRecord],
        traces: list[TraceSpan],
        error_boost_by_service: Optional[dict[str, float]] = None,
        latency_multiplier_by_service: Optional[dict[str, float]] = None,
        force_error_services: Optional[set[str]] = None,
        extra_log_by_service: Optional[dict[str, tuple[Severity, str]]] = None,
        extra_attributes_by_service: Optional[dict[str, dict]] = None,
    ) -> None:
        trace_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"{self.topology.tenant_id}:{entry_service}:{timestamp.isoformat()}"
        ).hex
        self._emit_span(
            service=entry_service,
            trace_id=trace_id,
            timestamp=timestamp,
            logs=logs,
            traces=traces,
            error_boost_by_service=error_boost_by_service or {},
            latency_multiplier_by_service=latency_multiplier_by_service or {},
            parent_span_id=None,
            force_error_services=force_error_services,
            extra_log_by_service=extra_log_by_service,
            extra_attributes_by_service=extra_attributes_by_service or {},
        )

    def _emit_span(
        self,
        service: str,
        trace_id: str,
        timestamp: datetime,
        logs: list[LogRecord],
        traces: list[TraceSpan],
        error_boost_by_service: dict[str, float],
        latency_multiplier_by_service: dict[str, float],
        parent_span_id: Optional[str],
        force_error_services: Optional[set[str]],
        extra_log_by_service: Optional[dict[str, tuple[Severity, str]]],
        extra_attributes_by_service: dict[str, dict],
    ) -> None:
        """Emit exactly one span for ``service`` (and recurse into its own
        declared dependencies), with every modifier looked up by the
        *current* service name so nothing is diluted by recursion depth."""
        span_id = uuid.uuid4().hex[:16]
        latency_multiplier = latency_multiplier_by_service.get(service, 1.0)
        error_boost = error_boost_by_service.get(service, 0.0)
        base_latency = _baseline_latency_ms(service) * latency_multiplier
        duration = max(1.0, self._rng.gauss(base_latency, base_latency * 0.1))

        forced_error = bool(force_error_services and service in force_error_services)
        is_error = forced_error or (self._rng.random() < (0.01 + error_boost))
        status = SpanStatus.ERROR if is_error else SpanStatus.OK

        attributes = dict(extra_attributes_by_service.get(service, {}))
        traces.append(
            TraceSpan(
                tenant_id=self.topology.tenant_id,
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=parent_span_id,
                service=service,
                operation=f"{service}.handle_request",
                start_time=timestamp,
                duration_ms=duration,
                status=status,
                attributes=attributes,
            )
        )

        if is_error:
            logs.append(
                LogRecord(
                    tenant_id=self.topology.tenant_id,
                    service=service,
                    timestamp=timestamp,
                    severity=Severity.ERROR,
                    message=f"request failed in {service}",
                    trace_id=trace_id,
                    attributes=dict(attributes),
                )
            )
        if extra_log_by_service and service in extra_log_by_service:
            severity, message = extra_log_by_service[service]
            logs.append(
                LogRecord(
                    tenant_id=self.topology.tenant_id,
                    service=service,
                    timestamp=timestamp,
                    severity=severity,
                    message=message,
                    trace_id=trace_id,
                    attributes=dict(attributes),
                )
            )

        for dep in self.topology.downstream_of(service):
            if dep not in self.topology.services:
                # External dependency (not one of this tenant's own
                # instrumented services): we deliberately emit no telemetry
                # for it -- only the caller's own span/log reflects the
                # failed call. This matches the downstream-outage scenario
                # description ("the dependency itself emits no telemetry").
                continue
            self._emit_span(
                service=dep,
                trace_id=trace_id,
                timestamp=timestamp + timedelta(milliseconds=duration * 0.1),
                logs=logs,
                traces=traces,
                error_boost_by_service=error_boost_by_service,
                latency_multiplier_by_service=latency_multiplier_by_service,
                parent_span_id=span_id,
                force_error_services=force_error_services,
                extra_log_by_service=extra_log_by_service,
                extra_attributes_by_service=extra_attributes_by_service,
            )

    # ------------------------------------------------------------------
    # Scenario-injected generation
    # ------------------------------------------------------------------
    def generate_with_scenario(
        self,
        scenario_key: str,
        start: datetime,
        end: datetime,
        interval_s: int = 15,
        root_cause_service: Optional[str] = None,
        external_dependency: Optional[str] = None,
        noisy_service: Optional[str] = None,
        victim_service: Optional[str] = None,
    ) -> tuple[list[LogRecord], list[TraceSpan], list[MetricPoint], GroundTruth]:
        if scenario_key not in SCENARIOS:
            raise ValueError(f"Unknown scenario: {scenario_key}")

        total_seconds = (end - start).total_seconds()
        window_start = start + timedelta(seconds=total_seconds * 0.33)
        window_end = start + timedelta(seconds=total_seconds * 0.66)

        handler = getattr(self, f"_inject_{scenario_key}")
        return handler(
            start=start,
            end=end,
            interval_s=interval_s,
            window_start=window_start,
            window_end=window_end,
            root_cause_service=root_cause_service,
            external_dependency=external_dependency,
            noisy_service=noisy_service,
            victim_service=victim_service,
        )

    def _default_root_service(self, root_cause_service: Optional[str]) -> str:
        if root_cause_service:
            return root_cause_service
        # Deterministically pick a leaf-ish service (one with no dependents is
        # most illustrative for cascading effects); fall back to the last
        # declared service.
        for svc in reversed(self.topology.services):
            if self.topology.dependencies.get(svc) == [] or svc not in self.topology.dependencies:
                return svc
        return self.topology.services[-1]

    def _scaled_baseline_metrics(
        self, service: str, t: datetime, latency_mult: float, error_boost: float
    ) -> list[MetricPoint]:
        points = self._baseline_metrics_for(service, t)
        scaled = []
        for p in points:
            if p.metric_name == "request_latency_ms_p99":
                p = p.model_copy(update={"value": p.value * latency_mult})
            elif p.metric_name == "error_rate":
                p = p.model_copy(update={"value": min(1.0, p.value + error_boost)})
            scaled.append(p)
        return scaled

    def _inject_cascading_timeout(
        self, start, end, interval_s, window_start, window_end, root_cause_service, **_
    ):
        root = self._default_root_service(root_cause_service)
        affected = {root} | _transitive_callers_of(self.topology, root)
        upstream = affected - {root}

        logs: list[LogRecord] = []
        traces: list[TraceSpan] = []
        metrics: list[MetricPoint] = []

        t = start
        while t < end:
            in_window = window_start <= t <= window_end
            lat_by_service = {root: 20.0} if in_window else {}
            force_error = set(upstream) if in_window else None
            extra_log = None
            if in_window:
                extra_log = {root: (Severity.WARN, f"{root} latency degraded sharply")}
                extra_log.update(
                    {
                        s: (Severity.ERROR, f"timeout waiting on downstream dependency {root}")
                        for s in upstream
                    }
                )

            for service in self.topology.services:
                self._emit_request_tree_for_entry(
                    service,
                    t,
                    logs,
                    traces,
                    error_boost_by_service={},
                    latency_multiplier_by_service=lat_by_service,
                    force_error_services=force_error,
                    extra_log_by_service=extra_log,
                )
                boost_lat = 20.0 if (in_window and service == root) else 1.0
                boost_err = 0.6 if (in_window and service in upstream) else 0.0
                metrics.extend(self._scaled_baseline_metrics(service, t, boost_lat, boost_err))
            t += timedelta(seconds=interval_s)

        truth = GroundTruth(
            scenario="cascading_timeout",
            tenant_id=self.topology.tenant_id,
            root_cause_service=root,
            root_cause_type="cascading_timeout",
            affected_services=sorted(affected),
            description=SCENARIOS["cascading_timeout"].description,
            injected_at=window_start,
            window_start=window_start,
            window_end=window_end,
        )
        return logs, traces, metrics, truth

    def _inject_memory_leak(
        self, start, end, interval_s, window_start, window_end, root_cause_service, **_
    ):
        root = self._default_root_service(root_cause_service)
        total = (window_end - window_start).total_seconds() or 1.0

        logs: list[LogRecord] = []
        traces: list[TraceSpan] = []
        metrics: list[MetricPoint] = []

        t = start
        while t < end:
            in_window = window_start <= t <= window_end
            progress = ((t - window_start).total_seconds() / total) if in_window else 0.0
            progress = min(max(progress, 0.0), 1.0)
            late_phase = in_window and progress > 0.8

            lat_by_service = {root: 1.0 + progress * 4.0} if late_phase else {}
            err_by_service = {root: 0.3} if late_phase else {}
            extra_log = (
                {root: (Severity.ERROR, "GC pause exceeded threshold / OutOfMemoryError risk")}
                if late_phase
                else None
            )

            for service in self.topology.services:
                self._emit_request_tree_for_entry(
                    service,
                    t,
                    logs,
                    traces,
                    error_boost_by_service=err_by_service,
                    latency_multiplier_by_service=lat_by_service,
                    extra_log_by_service=extra_log,
                )
                base_mem = 128.0 + 256.0 * _stable_unit_interval(service + "mem")
                if service == root and in_window:
                    mem_value = base_mem + progress * base_mem * 6.0
                else:
                    mem_value = base_mem + self._rng.uniform(-5.0, 5.0)
                metrics.append(
                    MetricPoint(
                        tenant_id=self.topology.tenant_id,
                        service=service,
                        metric_name="memory_rss_mb",
                        timestamp=t,
                        value=max(0.0, mem_value),
                        unit="MB",
                    )
                )
                lat_mult = lat_by_service.get(service, 1.0)
                err_boost = err_by_service.get(service, 0.0)
                for p in self._baseline_metrics_for(service, t):
                    if p.metric_name == "memory_rss_mb":
                        continue
                    if p.metric_name == "request_latency_ms_p99":
                        p = p.model_copy(update={"value": p.value * lat_mult})
                    if p.metric_name == "error_rate":
                        p = p.model_copy(update={"value": min(1.0, p.value + err_boost)})
                    metrics.append(p)
            t += timedelta(seconds=interval_s)

        truth = GroundTruth(
            scenario="memory_leak",
            tenant_id=self.topology.tenant_id,
            root_cause_service=root,
            root_cause_type="memory_leak",
            affected_services=[root],
            description=SCENARIOS["memory_leak"].description,
            injected_at=window_start,
            window_start=window_start,
            window_end=window_end,
        )
        return logs, traces, metrics, truth

    def _inject_bad_deploy(
        self, start, end, interval_s, window_start, window_end, root_cause_service, **_
    ):
        root = self._default_root_service(root_cause_service)
        flip_time = window_start + (window_end - window_start) / 2
        callers = set(_callers_of(self.topology, root))

        logs: list[LogRecord] = []
        traces: list[TraceSpan] = []
        metrics: list[MetricPoint] = []

        t = start
        while t < end:
            after_flip = t >= flip_time
            version = "v1.2.4-bad" if after_flip else "v1.2.3"
            err_by_service: dict[str, float] = {}
            if after_flip:
                err_by_service[root] = 0.5
                for c in callers:
                    err_by_service[c] = 0.15
            attrs_by_service = {root: {"deploy_version": version}}
            extra_log = None
            if after_flip and t == flip_time:
                extra_log = {root: (Severity.WARN, f"deploy_version changed to {version}")}

            for service in self.topology.services:
                self._emit_request_tree_for_entry(
                    service,
                    t,
                    logs,
                    traces,
                    error_boost_by_service=err_by_service,
                    extra_attributes_by_service=attrs_by_service,
                    extra_log_by_service=extra_log,
                )
                err_boost = err_by_service.get(service, 0.0)
                for p in self._baseline_metrics_for(service, t):
                    if p.metric_name == "error_rate" and err_boost:
                        p = p.model_copy(update={"value": min(1.0, p.value + err_boost)})
                    metrics.append(p)
            t += timedelta(seconds=interval_s)

        truth = GroundTruth(
            scenario="bad_deploy",
            tenant_id=self.topology.tenant_id,
            root_cause_service=root,
            root_cause_type="bad_deploy",
            affected_services=sorted({root} | callers),
            description=SCENARIOS["bad_deploy"].description,
            injected_at=flip_time,
            window_start=window_start,
            window_end=window_end,
        )
        return logs, traces, metrics, truth

    def _inject_downstream_dependency_outage(
        self, start, end, interval_s, window_start, window_end, external_dependency, **_
    ):
        dependency = external_dependency or "external-payment-gateway"
        callers = {
            svc for svc, deps in self.topology.dependencies.items() if dependency in deps
        }
        if not callers:
            # No service explicitly depends on it in the topology; attach it
            # to the first service to still exercise the scenario.
            callers = {self.topology.services[0]}

        logs: list[LogRecord] = []
        traces: list[TraceSpan] = []
        metrics: list[MetricPoint] = []

        t = start
        while t < end:
            in_window = window_start <= t <= window_end
            err_by_service = {c: 0.9 for c in callers} if in_window else {}
            attrs_by_service = {c: {"dependency": dependency} for c in callers} if in_window else {}
            extra_log = (
                {
                    c: (Severity.ERROR, f"connection refused calling dependency {dependency}")
                    for c in callers
                }
                if in_window
                else None
            )

            for service in self.topology.services:
                self._emit_request_tree_for_entry(
                    service,
                    t,
                    logs,
                    traces,
                    error_boost_by_service=err_by_service,
                    extra_attributes_by_service=attrs_by_service,
                    extra_log_by_service=extra_log,
                )
                err_boost = err_by_service.get(service, 0.0)
                for p in self._baseline_metrics_for(service, t):
                    if p.metric_name == "error_rate" and err_boost:
                        p = p.model_copy(update={"value": min(1.0, p.value + err_boost)})
                    metrics.append(p)
            t += timedelta(seconds=interval_s)

        truth = GroundTruth(
            scenario="downstream_dependency_outage",
            tenant_id=self.topology.tenant_id,
            root_cause_service=dependency,
            root_cause_type="downstream_dependency_outage",
            affected_services=sorted(callers),
            description=SCENARIOS["downstream_dependency_outage"].description,
            injected_at=window_start,
            window_start=window_start,
            window_end=window_end,
        )
        return logs, traces, metrics, truth

    def _default_noisy_neighbor_pair(self) -> tuple[str, str]:
        """Pick two services that share infrastructure (same ``node``) but
        have no direct call-dependency edge between them -- i.e. a genuine
        "noisy neighbor" pair, not two services that are merely slow because
        one legitimately calls the other."""
        by_node: dict[str, list[str]] = {}
        for svc in self.topology.services:
            node = self.topology.nodes.get(svc, f"node-{svc}")
            by_node.setdefault(node, []).append(svc)
        for _node, svcs in sorted(by_node.items()):
            for a in svcs:
                for b in svcs:
                    if a == b:
                        continue
                    if b in self.topology.dependencies.get(a, []):
                        continue
                    if a in self.topology.dependencies.get(b, []):
                        continue
                    return a, b
        # No same-node, unrelated pair exists in this topology; fall back to
        # the first two declared services (scenario will still run, just
        # without the "shared infra, no logical dependency" nuance).
        if len(self.topology.services) > 1:
            return self.topology.services[0], self.topology.services[1]
        return self.topology.services[0], self.topology.services[0]

    def _inject_noisy_neighbor(
        self, start, end, interval_s, window_start, window_end, noisy_service, victim_service, **_
    ):
        if noisy_service and victim_service:
            noisy, victim = noisy_service, victim_service
        else:
            noisy, victim = self._default_noisy_neighbor_pair()
        shared_node = self.topology.nodes.get(noisy, f"node-{noisy}")

        logs: list[LogRecord] = []
        traces: list[TraceSpan] = []
        metrics: list[MetricPoint] = []

        t = start
        while t < end:
            in_window = window_start <= t <= window_end
            lat_by_service = {victim: 3.0} if in_window else {}
            err_by_service = {victim: 0.1} if in_window else {}
            extra_log = (
                {victim: (Severity.WARN, f"degraded performance on shared node {shared_node}")}
                if in_window
                else None
            )
            attrs_by_service = {
                svc: {"node": self.topology.nodes.get(svc, f"node-{svc}")}
                for svc in self.topology.services
            }

            for service in self.topology.services:
                self._emit_request_tree_for_entry(
                    service,
                    t,
                    logs,
                    traces,
                    error_boost_by_service=err_by_service,
                    latency_multiplier_by_service=lat_by_service,
                    extra_log_by_service=extra_log,
                    extra_attributes_by_service=attrs_by_service,
                )
                for p in self._baseline_metrics_for(service, t):
                    metrics.append(p)
                if in_window and service == noisy:
                    metrics.append(
                        MetricPoint(
                            tenant_id=self.topology.tenant_id,
                            service=noisy,
                            metric_name="cpu_utilization_pct",
                            timestamp=t,
                            value=95.0 + self._rng.uniform(-2.0, 2.0),
                            unit="percent",
                            attributes={"node": shared_node},
                        )
                    )
                if in_window and service == victim:
                    lat_mult = lat_by_service.get(victim, 1.0)
                    metrics.append(
                        MetricPoint(
                            tenant_id=self.topology.tenant_id,
                            service=victim,
                            metric_name="request_latency_ms_p99",
                            timestamp=t,
                            value=_baseline_latency_ms(victim) * lat_mult,
                            unit="ms",
                            attributes={"node": self.topology.nodes.get(victim, f"node-{victim}")},
                        )
                    )
            t += timedelta(seconds=interval_s)

        truth = GroundTruth(
            scenario="noisy_neighbor",
            tenant_id=self.topology.tenant_id,
            root_cause_service=noisy,
            root_cause_type="noisy_neighbor",
            affected_services=[victim],
            description=SCENARIOS["noisy_neighbor"].description,
            injected_at=window_start,
            window_start=window_start,
            window_end=window_end,
        )
        return logs, traces, metrics, truth
