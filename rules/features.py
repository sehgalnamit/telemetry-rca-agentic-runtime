"""Builds deterministic evidence features from federated query results.

The RCA agent gathers two snapshots via the federation layer for the
services relevant to a hypothesis: a *baseline* window (an earlier, presumed
healthy period) and an *incident* window (around the alert). This module
turns the raw (already tenant-scoped, read-only) DataFrames returned by
``federation.query_layer`` into a compact, structured :class:`EvidenceBundle`
that the deterministic rule engine and the LLM-jury evaluator both reason
over -- never the raw per-row telemetry itself.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd


@dataclass
class ServiceFeatures:
    service: str
    error_rate_baseline: float = 0.0
    error_rate_incident: float = 0.0
    latency_avg_baseline: float = 0.0
    latency_avg_incident: float = 0.0
    metric_avg_baseline: dict[str, float] = field(default_factory=dict)
    metric_avg_incident: dict[str, float] = field(default_factory=dict)
    metric_incident_max: dict[str, float] = field(default_factory=dict)
    metric_incident_first_half_avg: dict[str, float] = field(default_factory=dict)
    metric_incident_second_half_avg: dict[str, float] = field(default_factory=dict)
    incident_log_messages: list[tuple[str, str]] = field(default_factory=list)
    attribute_values_baseline: dict[str, set] = field(default_factory=dict)
    attribute_values_incident: dict[str, set] = field(default_factory=dict)
    node: Optional[str] = None

    @property
    def latency_ratio(self) -> float:
        if self.latency_avg_baseline <= 0:
            return 1.0 if self.latency_avg_incident <= 0 else float("inf")
        return self.latency_avg_incident / self.latency_avg_baseline

    @property
    def error_rate_delta(self) -> float:
        return self.error_rate_incident - self.error_rate_baseline

    def metric_trend_ratio(self, metric_name: str) -> float:
        base = self.metric_avg_baseline.get(metric_name, 0.0)
        incident = self.metric_avg_incident.get(metric_name, 0.0)
        if base <= 0:
            return 1.0 if incident <= 0 else float("inf")
        return incident / base

    def metric_increasing(self, metric_name: str, factor_gte: float) -> bool:
        first = self.metric_incident_first_half_avg.get(metric_name, 0.0)
        second = self.metric_incident_second_half_avg.get(metric_name, 0.0)
        if first <= 0:
            return second > 0
        return second >= first * factor_gte

    def attribute_changed(self, attribute: str) -> bool:
        before = self.attribute_values_baseline.get(attribute, set())
        after = self.attribute_values_incident.get(attribute, set())
        return bool(after) and after != before

    def matches_log_pattern(self, pattern: str, severity: Optional[str] = None) -> bool:
        regex = re.compile(pattern, re.IGNORECASE)
        for sev, message in self.incident_log_messages:
            if severity and sev != severity:
                continue
            if regex.search(message):
                return True
        return False


@dataclass
class EvidenceBundle:
    tenant_id: str
    primary_service: Optional[str]
    """The hypothesis's candidate root-cause service, or ``None`` when the
    hypothesis targets an external dependency not represented in telemetry
    (e.g. downstream_dependency_outage)."""
    primary_label: str
    """Human-readable candidate root cause identifier (service name, or the
    external dependency name for outage hypotheses)."""
    baseline_start: datetime
    baseline_end: datetime
    incident_start: datetime
    incident_end: datetime
    services: dict[str, ServiceFeatures] = field(default_factory=dict)
    dependency_edges: set[tuple[str, str]] = field(default_factory=set)
    """(caller, callee) structural call-graph metadata, known to the control
    plane independent of any tenant's raw telemetry rows (it describes the
    topology, not the data)."""

    def primary_features(self) -> Optional[ServiceFeatures]:
        if self.primary_service is None:
            return None
        return self.services.get(self.primary_service)

    def other_features(self) -> list[ServiceFeatures]:
        return [f for svc, f in self.services.items() if svc != self.primary_service]

    def are_connected(self, a: str, b: str) -> bool:
        return (a, b) in self.dependency_edges or (b, a) in self.dependency_edges


def _attribute_value_sets(df: pd.DataFrame) -> dict[str, set]:
    out: dict[str, set] = {}
    if df.empty or "attributes_json" not in df.columns:
        return out
    for raw in df["attributes_json"]:
        try:
            attrs = json.loads(raw) if isinstance(raw, str) else {}
        except (TypeError, ValueError):
            attrs = {}
        for k, v in attrs.items():
            out.setdefault(k, set()).add(str(v))
    return out


def build_evidence_bundle(
    *,
    tenant_id: str,
    primary_service: Optional[str],
    primary_label: str,
    services: list[str],
    baseline_start: datetime,
    baseline_end: datetime,
    incident_start: datetime,
    incident_end: datetime,
    baseline_logs: pd.DataFrame,
    baseline_traces: pd.DataFrame,
    baseline_metrics: pd.DataFrame,
    incident_logs: pd.DataFrame,
    incident_traces: pd.DataFrame,
    incident_metrics: pd.DataFrame,
    dependency_edges: Optional[set[tuple[str, str]]] = None,
) -> EvidenceBundle:
    bundle = EvidenceBundle(
        tenant_id=tenant_id,
        primary_service=primary_service,
        primary_label=primary_label,
        baseline_start=baseline_start,
        baseline_end=baseline_end,
        incident_start=incident_start,
        incident_end=incident_end,
        dependency_edges=dependency_edges or set(),
    )

    for svc in services:
        feat = ServiceFeatures(service=svc)

        b_traces = baseline_traces[baseline_traces["service"] == svc] if not baseline_traces.empty else baseline_traces
        i_traces = incident_traces[incident_traces["service"] == svc] if not incident_traces.empty else incident_traces

        if len(b_traces):
            feat.error_rate_baseline = float((b_traces["status"] == "ERROR").mean())
            feat.latency_avg_baseline = float(b_traces["duration_ms"].mean())
        if len(i_traces):
            feat.error_rate_incident = float((i_traces["status"] == "ERROR").mean())
            feat.latency_avg_incident = float(i_traces["duration_ms"].mean())

        feat.attribute_values_baseline = _attribute_value_sets(b_traces)
        feat.attribute_values_incident = _attribute_value_sets(i_traces)

        b_metrics = baseline_metrics[baseline_metrics["service"] == svc] if not baseline_metrics.empty else baseline_metrics
        i_metrics = incident_metrics[incident_metrics["service"] == svc] if not incident_metrics.empty else incident_metrics

        if len(b_metrics):
            for name, grp in b_metrics.groupby("metric_name"):
                feat.metric_avg_baseline[name] = float(grp["value"].mean())
        if len(i_metrics):
            i_metrics = i_metrics.sort_values("timestamp")
            for name, grp in i_metrics.groupby("metric_name"):
                feat.metric_avg_incident[name] = float(grp["value"].mean())
                feat.metric_incident_max[name] = float(grp["value"].max())
                mid = len(grp) // 2 or 1
                first_half = grp.iloc[:mid]
                second_half = grp.iloc[mid:] if len(grp) > mid else grp.iloc[:mid]
                feat.metric_incident_first_half_avg[name] = float(first_half["value"].mean())
                feat.metric_incident_second_half_avg[name] = float(second_half["value"].mean())
            node_rows = i_metrics[i_metrics["attributes_json"].notna()]
            for raw in node_rows["attributes_json"]:
                try:
                    attrs = json.loads(raw) if isinstance(raw, str) else {}
                except (TypeError, ValueError):
                    attrs = {}
                if "node" in attrs:
                    feat.node = attrs["node"]
                    break

        i_logs = incident_logs[incident_logs["service"] == svc] if not incident_logs.empty else incident_logs
        if len(i_logs):
            feat.incident_log_messages = list(zip(i_logs["severity"], i_logs["message"]))

        bundle.services[svc] = feat

    return bundle
