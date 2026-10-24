"""Tenant store writer: materializes generated telemetry into a tenant's own,
fully isolated DuckDB file.

This module is the only place that writes raw telemetry rows anywhere, and it
always writes them into a *single tenant's own* database file -- never into a
shared/central store. This is what makes the federation layer "zero-copy":
the central control plane (``federation.query_layer``) only ever attaches and
reads these per-tenant files in place.
"""

from __future__ import annotations

import json
import os
from typing import Iterable

import duckdb

from otel_mock.models import LogRecord, MetricPoint, TraceSpan

TABLE_COLUMNS: dict[str, list[str]] = {
    "logs": [
        "tenant_id",
        "service",
        "timestamp",
        "severity",
        "message",
        "trace_id",
        "attributes_json",
    ],
    "traces": [
        "tenant_id",
        "trace_id",
        "span_id",
        "parent_span_id",
        "service",
        "operation",
        "start_time",
        "duration_ms",
        "status",
        "attributes_json",
    ],
    "metrics": [
        "tenant_id",
        "service",
        "metric_name",
        "timestamp",
        "value",
        "unit",
        "attributes_json",
    ],
}

_CREATE_TABLE_SQL = {
    "logs": """
        CREATE TABLE IF NOT EXISTS logs (
            tenant_id VARCHAR,
            service VARCHAR,
            timestamp TIMESTAMP,
            severity VARCHAR,
            message VARCHAR,
            trace_id VARCHAR,
            attributes_json VARCHAR
        )
    """,
    "traces": """
        CREATE TABLE IF NOT EXISTS traces (
            tenant_id VARCHAR,
            trace_id VARCHAR,
            span_id VARCHAR,
            parent_span_id VARCHAR,
            service VARCHAR,
            operation VARCHAR,
            start_time TIMESTAMP,
            duration_ms DOUBLE,
            status VARCHAR,
            attributes_json VARCHAR
        )
    """,
    "metrics": """
        CREATE TABLE IF NOT EXISTS metrics (
            tenant_id VARCHAR,
            service VARCHAR,
            metric_name VARCHAR,
            timestamp TIMESTAMP,
            value DOUBLE,
            unit VARCHAR,
            attributes_json VARCHAR
        )
    """,
}


def write_tenant_store(
    db_path: str,
    logs: Iterable[LogRecord],
    traces: Iterable[TraceSpan],
    metrics: Iterable[MetricPoint],
    *,
    overwrite: bool = True,
) -> None:
    """Write a tenant's telemetry into its own DuckDB file.

    Pass ``db_path=":memory:"`` style paths are not supported here on purpose
    -- tenant stores are always on-disk files to model "lives in the
    tenant's own storage account/project" even in the local demo.
    """
    if overwrite and db_path != ":memory:" and os.path.exists(db_path):
        os.remove(db_path)
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    con = duckdb.connect(db_path)
    try:
        for table, ddl in _CREATE_TABLE_SQL.items():
            con.execute(ddl)

        log_rows = [
            (
                r.tenant_id,
                r.service,
                r.timestamp,
                r.severity.value,
                r.message,
                r.trace_id,
                json.dumps(r.attributes, default=str),
            )
            for r in logs
        ]
        if log_rows:
            con.executemany(
                f"INSERT INTO logs ({', '.join(TABLE_COLUMNS['logs'])}) "
                f"VALUES ({', '.join(['?'] * len(TABLE_COLUMNS['logs']))})",
                log_rows,
            )

        trace_rows = [
            (
                r.tenant_id,
                r.trace_id,
                r.span_id,
                r.parent_span_id,
                r.service,
                r.operation,
                r.start_time,
                r.duration_ms,
                r.status.value,
                json.dumps(r.attributes, default=str),
            )
            for r in traces
        ]
        if trace_rows:
            con.executemany(
                f"INSERT INTO traces ({', '.join(TABLE_COLUMNS['traces'])}) "
                f"VALUES ({', '.join(['?'] * len(TABLE_COLUMNS['traces']))})",
                trace_rows,
            )

        metric_rows = [
            (
                r.tenant_id,
                r.service,
                r.metric_name,
                r.timestamp,
                r.value,
                r.unit,
                json.dumps(r.attributes, default=str),
            )
            for r in metrics
        ]
        if metric_rows:
            con.executemany(
                f"INSERT INTO metrics ({', '.join(TABLE_COLUMNS['metrics'])}) "
                f"VALUES ({', '.join(['?'] * len(TABLE_COLUMNS['metrics']))})",
                metric_rows,
            )
    finally:
        con.close()
