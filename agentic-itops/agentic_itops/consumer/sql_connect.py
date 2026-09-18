from __future__ import annotations

import re
from typing import Callable, TYPE_CHECKING
from uuid import UUID

from .contracts import ContractError

if TYPE_CHECKING:
    from mssql_python import Connection


def managed_identity_sql_factory(
    server: str, database: str, client_id: str,
) -> Callable[[], Connection]:
    if not isinstance(server, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9-]{0,62}\.database\.windows\.net", server, re.IGNORECASE,
    ):
        raise ContractError("an explicit Azure commercial SQL server hostname is required")
    if not isinstance(database, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", database):
        raise ContractError("invalid SQL database name")
    try:
        identity = UUID(client_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ContractError("a user-assigned managed identity client ID is required") from exc
    if identity.int == 0:
        raise ContractError("managed identity client ID cannot be a placeholder")

    import mssql_python

    # Keep each storage transaction on a short-lived, explicitly selected identity connection.
    mssql_python.pooling(enabled=False)
    connection_string = (
        f"Server=tcp:{server},1433;Database={database};Encrypt=yes;TrustServerCertificate=no;"
        f"Authentication=ActiveDirectoryMSI;UID={identity};"
    )

    def connect():
        connection = mssql_python.connect(connection_string, timeout=15,
                                          autocommit=False, native_uuid=False)
        connection.timeout = 30
        return connection

    return connect
