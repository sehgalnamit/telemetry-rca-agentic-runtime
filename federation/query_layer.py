"""Federated, zero-copy query engine.

:class:`FederationQueryEngine` answers cross-tenant questions by opening a
fresh, ephemeral DuckDB connection and ``ATTACH``-ing, **read-only**, only
the tenant database files that the caller's :class:`~federation.access_control.AccessScope`
permits -- never all registered tenants, and never into a shared/persistent
store. Query execution (filtering, aggregation) is pushed down into DuckDB
and runs directly against each tenant's own file. No tenant row is ever
copied into a process-wide or shared table.

This is the local, single-machine functional analog of the production
federation primitives documented in ``docs/ZERO_COPY_DATA_MODEL.md``
(Azure ADX cross-database queries / Synapse serverless external tables /
Fabric OneLake shortcuts, and GCP BigQuery Omni / BigLake authorized views).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import duckdb
import pandas as pd
from pydantic import BaseModel, Field

from federation.access_control import AccessScope, enforce_tenant_scope
from federation.catalog import FederationCatalog
from federation.tenant_store import TABLE_COLUMNS


class QueryDescriptor(BaseModel):
    """Metadata-only record of a federated query, safe to persist verbatim
    in the audit log (``audit/``) because it never carries raw tenant rows.
    """

    principal: str
    table: str
    tenant_ids: list[str]
    columns: list[str]
    redacted_columns: list[str] = Field(default_factory=list)
    filters: dict[str, str] = Field(default_factory=dict)
    row_count: int = 0


def _schema_alias(tenant_id: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in tenant_id)
    return f"tenant_{safe}"


@dataclass
class FederationQueryEngine:
    catalog: FederationCatalog
    _descriptors: list[QueryDescriptor] = field(default_factory=list)

    def _build_column_sql(
        self, scope: AccessScope, table: str, columns: Optional[list[str]]
    ) -> tuple[str, list[str], list[str]]:
        all_cols = TABLE_COLUMNS[table]
        selected = [c for c in (columns or all_cols) if c in all_cols]
        if not selected:
            raise ValueError(f"no valid columns requested for table '{table}'")
        redacted = set(scope.redacted_columns.get(table, [])) & set(selected)
        parts = [f"NULL AS {c}" if c in redacted else c for c in selected]
        return ", ".join(parts), selected, sorted(redacted)

    def _open_scoped_connection(self, tenant_ids: list[str]) -> duckdb.DuckDBPyConnection:
        """Open a fresh connection and attach *only* the in-scope tenant DBs,
        each read-only. Databases outside the scope are never attached, so
        even hand-written SQL on this connection cannot reach them."""
        con = duckdb.connect(":memory:")
        for tid in tenant_ids:
            reg = self.catalog.get(tid)
            alias = _schema_alias(tid)
            con.execute(f"ATTACH '{reg.db_path}' AS {alias} (READ_ONLY)")
        return con

    def query_table(
        self,
        scope: AccessScope,
        table: str,
        *,
        tenant_ids: Optional[list[str]] = None,
        columns: Optional[list[str]] = None,
        where_sql: Optional[str] = None,
        params: Optional[list] = None,
        limit: Optional[int] = None,
    ) -> tuple[pd.DataFrame, QueryDescriptor]:
        """Run a federated, read-only query across in-scope tenant stores.

        ``where_sql`` may only reference whitelisted column names (enforced
        implicitly since it is applied to the already-column-scoped result)
        and should use ``?`` placeholders with ``params`` for literal values
        -- standard SQL parameterization, never string-interpolated values.
        """
        if table not in TABLE_COLUMNS:
            raise ValueError(f"unknown table '{table}'")

        resolved_tenant_ids = enforce_tenant_scope(scope, tenant_ids)
        col_sql, selected_cols, redacted_cols = self._build_column_sql(scope, table, columns)

        con = self._open_scoped_connection(resolved_tenant_ids)
        try:
            branches = [
                f"SELECT {col_sql} FROM {_schema_alias(tid)}.{table}" for tid in resolved_tenant_ids
            ]
            sql = f"SELECT * FROM ({' UNION ALL '.join(branches)}) AS federated"
            if where_sql:
                sql += f" WHERE {where_sql}"
            if limit:
                sql += f" LIMIT {int(limit)}"
            df = con.execute(sql, params or []).fetchdf()
        finally:
            con.close()

        if "attributes_json" in df.columns:
            df = df.copy()
            df["attributes"] = df["attributes_json"].apply(
                lambda s: json.loads(s) if isinstance(s, str) else {}
            )

        descriptor = QueryDescriptor(
            principal=scope.principal,
            table=table,
            tenant_ids=resolved_tenant_ids,
            columns=selected_cols,
            redacted_columns=redacted_cols,
            filters={"where_sql": where_sql or "", "limit": str(limit or "")},
            row_count=len(df),
        )
        self._descriptors.append(descriptor)
        return df, descriptor

    # ------------------------------------------------------------------
    # Typed convenience wrappers used by the RCA agent runtime
    # ------------------------------------------------------------------
    def query_logs(
        self,
        scope: AccessScope,
        *,
        tenant_ids: Optional[list[str]] = None,
        service: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        severity: Optional[str] = None,
    ) -> tuple[pd.DataFrame, QueryDescriptor]:
        clauses, params = [], []
        if service:
            clauses.append("service = ?")
            params.append(service)
        if start:
            clauses.append("timestamp >= ?")
            params.append(start)
        if end:
            clauses.append("timestamp <= ?")
            params.append(end)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        where_sql = " AND ".join(clauses) if clauses else None
        return self.query_table(
            scope, "logs", tenant_ids=tenant_ids, where_sql=where_sql, params=params
        )

    def query_traces(
        self,
        scope: AccessScope,
        *,
        tenant_ids: Optional[list[str]] = None,
        service: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        status: Optional[str] = None,
    ) -> tuple[pd.DataFrame, QueryDescriptor]:
        clauses, params = [], []
        if service:
            clauses.append("service = ?")
            params.append(service)
        if start:
            clauses.append("start_time >= ?")
            params.append(start)
        if end:
            clauses.append("start_time <= ?")
            params.append(end)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where_sql = " AND ".join(clauses) if clauses else None
        return self.query_table(
            scope, "traces", tenant_ids=tenant_ids, where_sql=where_sql, params=params
        )

    def query_metrics(
        self,
        scope: AccessScope,
        *,
        tenant_ids: Optional[list[str]] = None,
        service: Optional[str] = None,
        metric_name: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> tuple[pd.DataFrame, QueryDescriptor]:
        clauses, params = [], []
        if service:
            clauses.append("service = ?")
            params.append(service)
        if metric_name:
            clauses.append("metric_name = ?")
            params.append(metric_name)
        if start:
            clauses.append("timestamp >= ?")
            params.append(start)
        if end:
            clauses.append("timestamp <= ?")
            params.append(end)
        where_sql = " AND ".join(clauses) if clauses else None
        return self.query_table(
            scope, "metrics", tenant_ids=tenant_ids, where_sql=where_sql, params=params
        )

    @property
    def query_log(self) -> list[QueryDescriptor]:
        """All :class:`QueryDescriptor` metadata emitted so far (no raw data)."""
        return list(self._descriptors)
