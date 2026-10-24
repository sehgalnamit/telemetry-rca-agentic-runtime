"""Tests for the OTel mock telemetry generator: determinism and
ground-truth labeling of injected failure scenarios."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from otel_mock import DEMO_TOPOLOGIES, SCENARIOS, TelemetryGenerator

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = START + timedelta(minutes=10)


def _dump_all(logs, traces, metrics) -> str:
    parts = []
    for r in logs:
        parts.append(r.model_dump_json())
    for r in traces:
        parts.append(r.model_dump_json())
    for r in metrics:
        parts.append(r.model_dump_json())
    return "\n".join(parts)


def test_baseline_generation_is_deterministic():
    topology = DEMO_TOPOLOGIES["acme"]
    gen_a = TelemetryGenerator(topology, seed=123)
    gen_b = TelemetryGenerator(topology, seed=123)

    logs_a, traces_a, metrics_a = gen_a.generate_baseline(START, END, interval_s=15)
    logs_b, traces_b, metrics_b = gen_b.generate_baseline(START, END, interval_s=15)

    assert _dump_all(logs_a, traces_a, metrics_a) == _dump_all(logs_b, traces_b, metrics_b)
    assert len(traces_a) > 0


def test_baseline_generation_differs_across_seeds():
    topology = DEMO_TOPOLOGIES["acme"]
    gen_a = TelemetryGenerator(topology, seed=1)
    gen_b = TelemetryGenerator(topology, seed=2)

    logs_a, traces_a, metrics_a = gen_a.generate_baseline(START, END, interval_s=15)
    logs_b, traces_b, metrics_b = gen_b.generate_baseline(START, END, interval_s=15)

    assert _dump_all(logs_a, traces_a, metrics_a) != _dump_all(logs_b, traces_b, metrics_b)


@pytest.mark.parametrize("scenario_key", list(SCENARIOS))
def test_scenario_generation_is_deterministic(scenario_key):
    topology = DEMO_TOPOLOGIES["acme"]
    gen_a = TelemetryGenerator(topology, seed=7)
    gen_b = TelemetryGenerator(topology, seed=7)

    logs_a, traces_a, metrics_a, truth_a = gen_a.generate_with_scenario(scenario_key, START, END)
    logs_b, traces_b, metrics_b, truth_b = gen_b.generate_with_scenario(scenario_key, START, END)

    assert _dump_all(logs_a, traces_a, metrics_a) == _dump_all(logs_b, traces_b, metrics_b)
    assert truth_a.model_dump() == truth_b.model_dump()


@pytest.mark.parametrize("scenario_key", list(SCENARIOS))
def test_scenario_ground_truth_matches_catalog(scenario_key):
    topology = DEMO_TOPOLOGIES["acme"]
    gen = TelemetryGenerator(topology, seed=7)
    _, _, _, truth = gen.generate_with_scenario(scenario_key, START, END)

    assert truth.scenario == scenario_key
    assert truth.root_cause_type == SCENARIOS[scenario_key].root_cause_type
    assert truth.tenant_id == topology.tenant_id
    assert truth.root_cause_service
    assert len(truth.affected_services) >= 1
    assert truth.window_start < truth.window_end


def test_unknown_scenario_raises():
    topology = DEMO_TOPOLOGIES["acme"]
    gen = TelemetryGenerator(topology, seed=1)
    with pytest.raises(ValueError):
        gen.generate_with_scenario("not_a_real_scenario", START, END)


def test_external_dependency_emits_no_telemetry_of_its_own():
    """The downstream_dependency_outage scenario's dependency is external to
    the tenant's instrumented services -- it must never appear as a
    ``service`` in generated spans/logs, only be named in caller evidence."""
    topology = DEMO_TOPOLOGIES["acme"]
    gen = TelemetryGenerator(topology, seed=7)
    logs, traces, _metrics, truth = gen.generate_with_scenario(
        "downstream_dependency_outage", START, END
    )
    dependency = truth.root_cause_service
    assert all(span.service != dependency for span in traces)
    assert all(log.service != dependency for log in logs)
    # But at least one caller log does name the dependency.
    assert any(dependency in log.message for log in logs)
