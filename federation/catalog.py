"""Lightweight, metadata-only federation catalog.

The catalog never stores a single telemetry row. It only tracks, per tenant:
*where* that tenant's own DuckDB file lives and which tables it exposes. This
is the local analog of a cross-cloud metadata/discovery layer (e.g. a Fabric
OneLake shortcut registry, a BigQuery Omni/Analytics Hub listing, or an Azure
Lighthouse delegation registry) -- it is pure pointers + schema, never data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class TenantRegistration:
    tenant_id: str
    db_path: str
    tables: list[str] = field(default_factory=lambda: ["logs", "traces", "metrics"])
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cloud_analog: str = "local-duckdb"
    """Free-text note on which real-world zero-copy primitive this
    registration models (e.g. 'azure-adx-database', 'gcp-bigquery-omni').
    Informational only; never affects query behavior in the local demo."""


class FederationCatalog:
    """In-memory (optionally persisted) registry of tenant store locations.

    This is intentionally *not* a data store. It is the schema/metadata
    catalog that the federated query engine consults to know which DuckDB
    file to ``ATTACH`` for a given tenant id -- nothing more.
    """

    def __init__(self) -> None:
        self._tenants: dict[str, TenantRegistration] = {}

    def register_tenant(self, registration: TenantRegistration) -> None:
        self._tenants[registration.tenant_id] = registration

    def get(self, tenant_id: str) -> TenantRegistration:
        if tenant_id not in self._tenants:
            raise KeyError(f"tenant '{tenant_id}' is not registered in the federation catalog")
        return self._tenants[tenant_id]

    def all_tenant_ids(self) -> list[str]:
        return sorted(self._tenants.keys())

    def __contains__(self, tenant_id: str) -> bool:
        return tenant_id in self._tenants
