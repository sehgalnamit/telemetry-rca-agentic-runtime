"""Access scoping for the federation layer.

An :class:`AccessScope` is the *only* thing that determines which tenants'
data a given query is allowed to touch, and which columns within those
tenants' tables are visible. It models the least-privilege, read-only,
delegated-access pattern that Azure Lighthouse / GCP cross-project IAM
provide in the real-world architecture (see
``docs/ZERO_COPY_DATA_MODEL.md``): the control plane is handed a scoped
credential/role, never a copy of the data.
"""

from __future__ import annotations

from typing import Iterable, Optional

from pydantic import BaseModel, Field


class AccessScope(BaseModel):
    """A least-privilege, read-only access grant for the RCA control plane.

    Attributes:
        principal: identifies who/what is querying (e.g. ``"rca-agent"``).
        allowed_tenant_ids: the only tenants this principal may query.
        redacted_columns: table -> list of columns that must be nulled out
            in query results for this principal, even though the tenant is
            otherwise in scope (column-level scoping).
    """

    principal: str
    allowed_tenant_ids: set[str]
    redacted_columns: dict[str, list[str]] = Field(default_factory=dict)
    description: str = ""


def enforce_tenant_scope(
    scope: AccessScope, requested_tenant_ids: Optional[Iterable[str]]
) -> list[str]:
    """Validate a requested tenant-id set against an :class:`AccessScope`.

    Returns the sorted, de-duplicated list of tenant ids that are safe to
    query. Raises :class:`PermissionError` if any requested tenant is
    outside the granted scope. If ``requested_tenant_ids`` is ``None``, the
    full allowed set is returned (i.e. "all tenants I'm allowed to see").
    """
    if requested_tenant_ids is None:
        return sorted(scope.allowed_tenant_ids)

    requested = set(requested_tenant_ids)
    out_of_scope = requested - scope.allowed_tenant_ids
    if out_of_scope:
        raise PermissionError(
            f"principal '{scope.principal}' requested tenants outside its granted scope: "
            f"{sorted(out_of_scope)} (allowed: {sorted(scope.allowed_tenant_ids)})"
        )
    return sorted(requested)
