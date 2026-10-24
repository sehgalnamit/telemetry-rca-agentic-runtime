"""Factory helpers that build the demo multi-tenant topology used across
tests, the local demo script, and the federation layer bootstrap."""

from __future__ import annotations

from otel_mock.models import TenantTopology

ACME_TOPOLOGY = TenantTopology(
    tenant_id="acme",
    services=["frontend", "checkout", "inventory", "payments-adapter", "pricing"],
    dependencies={
        "frontend": ["checkout", "pricing"],
        "checkout": ["inventory", "payments-adapter"],
        "pricing": [],
        "inventory": [],
        "payments-adapter": ["external-payment-gateway"],
    },
    nodes={
        "frontend": "node-a",
        "checkout": "node-a",
        "inventory": "node-b",
        "payments-adapter": "node-b",
        "pricing": "node-c",
    },
)

GLOBEX_TOPOLOGY = TenantTopology(
    tenant_id="globex",
    services=["gateway", "orders", "billing", "catalog"],
    dependencies={
        "gateway": ["orders", "catalog"],
        "orders": ["billing"],
        "billing": [],
        "catalog": [],
    },
    nodes={
        "gateway": "node-x",
        "orders": "node-y",
        "billing": "node-y",
        "catalog": "node-z",
    },
)

INITECH_TOPOLOGY = TenantTopology(
    tenant_id="initech",
    services=["api-gateway", "reports", "auth"],
    dependencies={
        "api-gateway": ["reports", "auth"],
        "reports": [],
        "auth": [],
    },
    nodes={
        "api-gateway": "node-p",
        "reports": "node-q",
        "auth": "node-q",
    },
)

DEMO_TOPOLOGIES: dict[str, TenantTopology] = {
    t.tenant_id: t for t in (ACME_TOPOLOGY, GLOBEX_TOPOLOGY, INITECH_TOPOLOGY)
}
