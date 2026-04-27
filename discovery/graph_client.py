"""Thin wrapper around azure-mgmt-resourcegraph with paging."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import (
    QueryRequest,
    QueryRequestOptions,
    ResultFormat,
)

from .auth import get_credential

# ARG hard caps a single page at 1000 rows.
_PAGE_SIZE = 1000


@dataclass(frozen=True)
class GraphScope:
    """Scope of a discovery sweep.

    Provide either a list of subscription IDs or a list of management
    group IDs. If both are empty, ARG queries the caller's tenant scope
    (all subscriptions the principal can read).
    """

    subscriptions: tuple[str, ...] = ()
    management_groups: tuple[str, ...] = ()


class GraphClient:
    def __init__(self, scope: GraphScope) -> None:
        self._scope = scope
        self._client = ResourceGraphClient(credential=get_credential())

    def query(self, kql: str) -> list[dict]:
        """Run a KQL query and return all rows (handles paging)."""
        rows: list[dict] = []
        skip_token: str | None = None

        while True:
            options = QueryRequestOptions(
                top=_PAGE_SIZE,
                skip_token=skip_token,
                result_format=ResultFormat.object_array,
            )
            request = QueryRequest(
                query=kql,
                subscriptions=list(self._scope.subscriptions) or None,
                management_groups=list(self._scope.management_groups) or None,
                options=options,
            )
            response = self._client.resources(request)
            rows.extend(response.data or [])
            skip_token = getattr(response, "skip_token", None)
            if not skip_token:
                break

        return rows


def iter_resources(rows: Iterable[dict]) -> Iterable[dict]:
    """Normalize ARG rows: ensure tags is a dict, type is lowercase."""
    for row in rows:
        row = dict(row)
        row["type"] = (row.get("type") or "").lower()
        if row.get("tags") is None:
            row["tags"] = {}
        yield row
