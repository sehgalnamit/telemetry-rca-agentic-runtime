"""Tests for the deterministic rule execution engine, scored against the
ground-truth labels attached by the OTel mock generator (precision/recall
over the full labeled-scenario catalog)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from rules import RuleEngine, build_evidence_bundle
from tests.helpers import ALL_SCENARIO_KEYS, build_scenario_fixture


def _bundle_for(tmp_path, scenario_key):
    fixture = build_scenario_fixture(tmp_path, scenario_key)
    truth = fixture.truth

    baseline_start = fixture.truth.window_start - timedelta(minutes=3)
    baseline_end = fixture.truth.window_start - timedelta(seconds=30)
    incident_start = truth.window_start
    incident_end = truth.window_end

    logs_b, _ = fixture.query_engine.query_logs(fixture.scope, start=baseline_start, end=baseline_end)
    traces_b, _ = fixture.query_engine.query_traces(fixture.scope, start=baseline_start, end=baseline_end)
    metrics_b, _ = fixture.query_engine.query_metrics(fixture.scope, start=baseline_start, end=baseline_end)
    logs_i, _ = fixture.query_engine.query_logs(fixture.scope, start=incident_start, end=incident_end)
    traces_i, _ = fixture.query_engine.query_traces(fixture.scope, start=incident_start, end=incident_end)
    metrics_i, _ = fixture.query_engine.query_metrics(fixture.scope, start=incident_start, end=incident_end)

    primary = truth.root_cause_service if truth.root_cause_service in fixture.topology.services else None
    dependency_edges = {
        (a, b) for a, deps in fixture.topology.dependencies.items() for b in deps
    }
    bundle = build_evidence_bundle(
        tenant_id=fixture.tenant_id,
        primary_service=primary,
        primary_label=truth.root_cause_service,
        services=fixture.topology.services,
        baseline_start=baseline_start,
        baseline_end=baseline_end,
        incident_start=incident_start,
        incident_end=incident_end,
        baseline_logs=logs_b,
        baseline_traces=traces_b,
        baseline_metrics=metrics_b,
        incident_logs=logs_i,
        incident_traces=traces_i,
        incident_metrics=metrics_i,
        dependency_edges=dependency_edges,
    )
    return bundle, truth


@pytest.mark.parametrize("scenario_key", ALL_SCENARIO_KEYS)
def test_rule_engine_surfaces_correct_root_cause_as_top_verdict(tmp_path, scenario_key):
    bundle, truth = _bundle_for(tmp_path, scenario_key)
    engine = RuleEngine()

    verdicts = engine.evaluate(bundle)
    top = verdicts[0]

    assert top.matched, f"expected a matching rule for {scenario_key}, got none (top={top})"
    assert top.root_cause_type == truth.root_cause_type
    assert top.confidence > 0.0


def test_rule_engine_precision_and_recall_across_all_scenarios(tmp_path):
    """Treats each scenario as one labeled example: recall = fraction where
    the correct rule fired at all; precision = fraction of *fired* top
    verdicts that were correct. Both should be perfect on this canonical,
    well-separated scenario set."""
    engine = RuleEngine()
    true_positives = 0
    fired = 0
    total = len(ALL_SCENARIO_KEYS)

    for scenario_key in ALL_SCENARIO_KEYS:
        bundle, truth = _bundle_for(tmp_path, scenario_key)
        top = engine.evaluate(bundle)[0]
        if top.matched:
            fired += 1
            if top.root_cause_type == truth.root_cause_type:
                true_positives += 1

    recall = true_positives / total
    precision = (true_positives / fired) if fired else 0.0

    assert recall == 1.0, f"recall={recall}, expected perfect recall on labeled scenario set"
    assert precision == 1.0, f"precision={precision}, expected perfect precision on labeled scenario set"


def test_rule_engine_rationale_is_explainable(tmp_path):
    bundle, truth = _bundle_for(tmp_path, "cascading_timeout")
    engine = RuleEngine()
    top = engine.evaluate(bundle)[0]

    assert top.rule_id
    assert top.rationale
    assert "signals matched" in top.rationale
