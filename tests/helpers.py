"""Shared test helpers for building scenario telemetry, federation setup, and
full pipeline runs. Not itself a test module (no ``test_`` functions)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent import Alert, RCAAgentRuntime
from agent.runtime import PipelineResult
from federation import (
    AccessScope,
    FederationCatalog,
    FederationQueryEngine,
    TenantRegistration,
    write_tenant_store,
)
from otel_mock import DEMO_TOPOLOGIES, GroundTruth, TelemetryGenerator
from otel_mock.models import TenantTopology

DEFAULT_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
DEFAULT_END = DEFAULT_START + timedelta(minutes=12)
DEFAULT_SEED = 11


@dataclass
class ScenarioFixture:
    tenant_id: str
    topology: TenantTopology
    truth: GroundTruth
    catalog: FederationCatalog
    query_engine: FederationQueryEngine
    scope: AccessScope
    db_path: str


def build_scenario_fixture(
    tmp_dir: Path,
    scenario_key: str,
    tenant_id: str = "acme",
    seed: int = DEFAULT_SEED,
    start: datetime = DEFAULT_START,
    end: datetime = DEFAULT_END,
) -> ScenarioFixture:
    """Generate one labeled scenario, write it into an isolated per-tenant
    DuckDB file, and wire up a federation catalog + query engine over it."""
    topology = DEMO_TOPOLOGIES[tenant_id]
    generator = TelemetryGenerator(topology, seed=seed)
    logs, traces, metrics, truth = generator.generate_with_scenario(
        scenario_key, start, end, interval_s=15
    )

    db_path = str(tmp_dir / f"{tenant_id}_{scenario_key}.duckdb")
    write_tenant_store(db_path, logs, traces, metrics)

    catalog = FederationCatalog()
    catalog.register_tenant(TenantRegistration(tenant_id=tenant_id, db_path=db_path))
    query_engine = FederationQueryEngine(catalog)
    scope = AccessScope(principal="rca-agent", allowed_tenant_ids={tenant_id})

    return ScenarioFixture(
        tenant_id=tenant_id,
        topology=topology,
        truth=truth,
        catalog=catalog,
        query_engine=query_engine,
        scope=scope,
        db_path=db_path,
    )


def run_pipeline(fixture: ScenarioFixture, alert_delay_s: int = 30) -> PipelineResult:
    """Run the full RCA agent pipeline for a scenario fixture, firing the
    alert a short delay into the injected failure window (simulating a
    monitor noticing the symptom before all evidence has accumulated)."""
    runtime = RCAAgentRuntime(
        query_engine=fixture.query_engine,
        topology_catalog={fixture.tenant_id: fixture.topology},
    )
    truth = fixture.truth
    alert_service = truth.affected_services[0] if truth.affected_services else fixture.topology.services[0]
    alert = Alert(
        tenant_id=fixture.tenant_id,
        service=alert_service,
        severity="ERROR",
        message=f"elevated error rate detected on {alert_service}",
        timestamp=truth.injected_at + timedelta(seconds=alert_delay_s),
    )
    return runtime.investigate(alert, fixture.scope)


ALL_SCENARIO_KEYS = [
    "cascading_timeout",
    "memory_leak",
    "bad_deploy",
    "downstream_dependency_outage",
    "noisy_neighbor",
]
