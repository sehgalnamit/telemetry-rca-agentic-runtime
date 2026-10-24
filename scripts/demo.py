#!/usr/bin/env python
"""Demo script: inject a labeled failure scenario and run the full agentic
RCA pipeline end-to-end, printing the root-cause verdict and audit trail.

Usage:
    python scripts/demo.py --scenario cascading_timeout
    python scripts/demo.py --all
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from agent import Alert, RCAAgentRuntime  # noqa: E402
from federation import (  # noqa: E402
    AccessScope,
    FederationCatalog,
    FederationQueryEngine,
    TenantRegistration,
    write_tenant_store,
)
from otel_mock import DEMO_TOPOLOGIES, SCENARIOS, TelemetryGenerator  # noqa: E402

console = Console(width=120)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "tenants"


def run_demo(scenario_key: str, tenant_id: str = "acme", seed: int = 11) -> bool:
    console.rule(f"[bold cyan]Scenario: {scenario_key}[/bold cyan]")

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(minutes=12)

    topology = DEMO_TOPOLOGIES[tenant_id]
    generator = TelemetryGenerator(topology, seed=seed)
    logs, traces, metrics, truth = generator.generate_with_scenario(
        scenario_key, start, end, interval_s=15
    )
    console.print(
        f"Generated {len(logs)} logs, {len(traces)} spans, {len(metrics)} metric points "
        f"for tenant [bold]{tenant_id}[/bold] (ground truth root cause: "
        f"[bold red]{truth.root_cause_service}[/bold red] / {truth.root_cause_type})"
    )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_path = str(DATA_DIR / f"{tenant_id}_{scenario_key}.duckdb")
    write_tenant_store(db_path, logs, traces, metrics)
    console.print(f"Wrote tenant-isolated DuckDB store: [italic]{db_path}[/italic] (never copied elsewhere)")

    catalog = FederationCatalog()
    catalog.register_tenant(TenantRegistration(tenant_id=tenant_id, db_path=db_path))
    query_engine = FederationQueryEngine(catalog)
    scope = AccessScope(principal="rca-agent", allowed_tenant_ids={tenant_id})

    runtime = RCAAgentRuntime(query_engine=query_engine, topology_catalog={tenant_id: topology})

    alert_service = truth.affected_services[0] if truth.affected_services else topology.services[0]
    alert = Alert(
        tenant_id=tenant_id,
        service=alert_service,
        severity="ERROR",
        message=f"elevated error rate detected on {alert_service}",
        timestamp=truth.injected_at + timedelta(seconds=30),
    )
    console.print(f"\n[bold]Alert fired:[/bold] {alert.message} at {alert.timestamp.isoformat()}")

    result = runtime.investigate(alert, scope)

    table = Table(title="Ranked Hypotheses")
    table.add_column("Rank")
    table.add_column("Root Cause Type")
    table.add_column("Candidate")
    table.add_column("Rule Matched")
    table.add_column("Rule Confidence")
    for i, assessment in enumerate(result.assessments[:5], start=1):
        v = assessment.rule_verdict
        table.add_row(str(i), v.root_cause_type, v.candidate_service, str(v.matched), f"{v.confidence:.2f}")
    console.print(table)

    top = result.top_assessment
    console.print(
        f"\n[bold green]Top hypothesis:[/bold green] {top.hypothesis.root_cause_type} "
        f"-> {top.hypothesis.candidate_label} (rule confidence={top.rule_verdict.confidence:.2f})"
    )
    console.print(f"[bold]Jury:[/bold] consensus_supports={result.jury_verdict.consensus_supports}, "
                  f"mean_confidence={result.jury_verdict.mean_confidence:.2f}, "
                  f"dissent_notes={len(result.jury_verdict.dissent_notes)}")
    console.print(f"[bold]Final decision:[/bold] [yellow]{result.final_decision.action}[/yellow] -- "
                  f"{result.final_decision.rationale}")

    verification = result.audit_log.verify_chain()
    console.print(
        f"\n[bold]Audit log:[/bold] {verification.checked_entries} entries, "
        f"chain_intact={verification.ok} ({verification.detail})"
    )

    correct = (
        top.hypothesis.root_cause_type == truth.root_cause_type
        and top.hypothesis.candidate_label == truth.root_cause_service
    )
    status = "[bold green]CORRECT[/bold green]" if correct else "[bold red]INCORRECT[/bold red]"
    console.print(f"\nScoring against ground truth: {status} "
                  f"(expected {truth.root_cause_type}/{truth.root_cause_service})")
    return correct


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=list(SCENARIOS), help="run a single scenario")
    parser.add_argument("--all", action="store_true", help="run every scenario in the catalog")
def run_cross_tenant_federation_demo(seed: int = 11) -> None:
    """Demonstrates the zero-copy federation layer in isolation: each
    tenant's telemetry lives in its own DuckDB file, a scoped query reads
    across several of them without copying rows, and an out-of-scope
    request is structurally refused."""
    console.rule("[bold cyan]Zero-Copy Cross-Tenant Federation[/bold cyan]")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(minutes=5)

    catalog = FederationCatalog()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for tenant_id, topology in DEMO_TOPOLOGIES.items():
        generator = TelemetryGenerator(topology, seed=seed)
        logs, traces, metrics = generator.generate_baseline(start, end, interval_s=15)
        db_path = str(DATA_DIR / f"{tenant_id}_federation_demo.duckdb")
        write_tenant_store(db_path, logs, traces, metrics)
        catalog.register_tenant(TenantRegistration(tenant_id=tenant_id, db_path=db_path))
        console.print(f"  tenant [bold]{tenant_id}[/bold]: own isolated store at {db_path}")

    engine = FederationQueryEngine(catalog)
    scope = AccessScope(principal="rca-agent", allowed_tenant_ids={"acme", "globex"})
    df, descriptor = engine.query_traces(scope)
    console.print(
        f"\nFederated query across {descriptor.tenant_ids}: {descriptor.row_count} rows returned "
        f"(tenants present in result: {sorted(df['tenant_id'].unique())}) -- no row was copied into a "
        "shared store; each tenant's own file was attached read-only and queried in place."
    )

    try:
        engine.query_traces(scope, tenant_ids=["initech"])
    except PermissionError as exc:
        console.print(f"\n[bold green]Out-of-scope request correctly refused:[/bold green] {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=list(SCENARIOS), help="run a single scenario")
    parser.add_argument("--all", action="store_true", help="run every scenario in the catalog")
    parser.add_argument("--tenant", default="acme", choices=list(DEMO_TOPOLOGIES))
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument(
        "--cross-tenant-federation-demo",
        action="store_true",
        help="also run the standalone zero-copy cross-tenant federation demo",
    )
    args = parser.parse_args()

    scenarios = list(SCENARIOS) if args.all or not args.scenario else [args.scenario]

    results = {key: run_demo(key, tenant_id=args.tenant, seed=args.seed) for key in scenarios}

    if args.cross_tenant_federation_demo:
        run_cross_tenant_federation_demo(seed=args.seed)

    console.rule("[bold]Summary[/bold]")
    for key, correct in results.items():
        console.print(f"  {key:32s} {'OK' if correct else 'MISMATCH'}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
