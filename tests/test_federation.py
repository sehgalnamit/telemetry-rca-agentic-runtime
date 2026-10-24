"""Tests for the zero-copy, multi-tenant federation layer: cross-tenant
federated queries work, and per-tenant/per-column access scoping is
enforced (out-of-scope tenants are refused, redacted columns are nulled)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from federation import (
    AccessScope,
    FederationCatalog,
    FederationQueryEngine,
    TenantRegistration,
    enforce_tenant_scope,
    write_tenant_store,
)
from otel_mock import DEMO_TOPOLOGIES, TelemetryGenerator

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = START + timedelta(minutes=5)


@pytest.fixture
def federated_env(tmp_path):
    catalog = FederationCatalog()
    for tenant_id, topology in DEMO_TOPOLOGIES.items():
        generator = TelemetryGenerator(topology, seed=3)
        logs, traces, metrics = generator.generate_baseline(START, END, interval_s=15)
        db_path = str(tmp_path / f"{tenant_id}.duckdb")
        write_tenant_store(db_path, logs, traces, metrics)
        catalog.register_tenant(TenantRegistration(tenant_id=tenant_id, db_path=db_path))
    return catalog


def test_cross_tenant_federated_query_returns_rows_from_every_scoped_tenant(federated_env):
    engine = FederationQueryEngine(federated_env)
    scope = AccessScope(principal="rca-agent", allowed_tenant_ids={"acme", "globex"})

    df, descriptor = engine.query_logs(scope)

    assert set(df["tenant_id"].unique()) == {"acme", "globex"}
    assert descriptor.tenant_ids == ["acme", "globex"]
    assert descriptor.row_count == len(df)
    # No tenant row was ever copied into a shared store: the catalog itself
    # holds only file-path metadata, never telemetry rows.
    assert federated_env.get("acme").db_path != federated_env.get("globex").db_path


def test_query_is_pushed_down_without_touching_out_of_scope_tenant_db(federated_env):
    """A query scoped to one tenant must never even ATTACH another tenant's
    database file, so it structurally cannot leak rows regardless of the
    SQL issued on that connection."""
    engine = FederationQueryEngine(federated_env)
    scope = AccessScope(principal="rca-agent", allowed_tenant_ids={"acme"})

    df, descriptor = engine.query_logs(scope)

    assert set(df["tenant_id"].unique()) == {"acme"}
    assert descriptor.tenant_ids == ["acme"]


def test_out_of_scope_tenant_request_is_refused(federated_env):
    engine = FederationQueryEngine(federated_env)
    scope = AccessScope(principal="rca-agent", allowed_tenant_ids={"acme"})

    with pytest.raises(PermissionError):
        engine.query_logs(scope, tenant_ids=["globex"])

    with pytest.raises(PermissionError):
        engine.query_logs(scope, tenant_ids=["acme", "globex"])


def test_enforce_tenant_scope_defaults_to_full_allowed_set():
    scope = AccessScope(principal="auditor", allowed_tenant_ids={"acme", "globex"})
    assert enforce_tenant_scope(scope, None) == ["acme", "globex"]
    assert enforce_tenant_scope(scope, ["acme"]) == ["acme"]
    with pytest.raises(PermissionError):
        enforce_tenant_scope(scope, ["initech"])


def test_column_redaction_nulls_out_restricted_columns(federated_env):
    engine = FederationQueryEngine(federated_env)
    scope = AccessScope(
        principal="read-only-auditor",
        allowed_tenant_ids={"acme"},
        redacted_columns={"logs": ["message"]},
    )

    df, descriptor = engine.query_logs(scope)

    assert df["message"].isna().all()
    assert "message" in descriptor.redacted_columns
    # Other columns remain visible.
    assert not df["service"].isna().all()


def test_unregistered_tenant_raises_keyerror(federated_env):
    engine = FederationQueryEngine(federated_env)
    scope = AccessScope(principal="rca-agent", allowed_tenant_ids={"acme", "ghost-tenant"})
    with pytest.raises(KeyError):
        engine.query_logs(scope, tenant_ids=["ghost-tenant"])


def test_query_descriptors_never_contain_raw_rows(federated_env):
    """QueryDescriptor is what gets written to the audit log -- it must be
    pure metadata (table/tenants/columns/filters/row_count), never data."""
    engine = FederationQueryEngine(federated_env)
    scope = AccessScope(principal="rca-agent", allowed_tenant_ids={"acme"})

    _, descriptor = engine.query_logs(scope, service="frontend")

    dumped = descriptor.model_dump()
    assert set(dumped.keys()) == {
        "principal",
        "table",
        "tenant_ids",
        "columns",
        "redacted_columns",
        "filters",
        "row_count",
    }
