"""Zero-copy, multi-tenant federated query layer (the centerpiece of this repo).

Each tenant's OTel-like telemetry lives in its own, fully isolated DuckDB
file (``tenant_store.py``). A lightweight, metadata-only catalog
(``catalog.py``) tracks *where* each tenant's store lives and what tables it
exposes -- it never stores a single telemetry row. The query layer
(``query_layer.py``) answers cross-tenant questions by ``ATTACH``-ing only
the tenant databases a caller is authorized for (``access_control.py``) into
an ephemeral, read-only DuckDB connection and pushing the query down into
those files -- no tenant row is ever copied into a shared store.

See ``docs/ZERO_COPY_DATA_MODEL.md`` for the full design, including how this
local DuckDB analog maps onto real Azure (ADX / Lighthouse / Fabric OneLake
shortcuts / Synapse serverless external tables) and GCP (BigQuery Omni /
Analytics Hub / BigLake authorized views) zero-copy federation primitives.
"""

from federation.access_control import AccessScope, enforce_tenant_scope
from federation.catalog import FederationCatalog, TenantRegistration
from federation.query_layer import FederationQueryEngine, QueryDescriptor
from federation.tenant_store import TABLE_COLUMNS, write_tenant_store

__all__ = [
    "AccessScope",
    "enforce_tenant_scope",
    "FederationCatalog",
    "TenantRegistration",
    "FederationQueryEngine",
    "QueryDescriptor",
    "TABLE_COLUMNS",
    "write_tenant_store",
]
