"""Hash-ledgered SQL Server migrations, with no automatic adoption of existing schemas."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from uuid import UUID


class MigrationError(RuntimeError):
    pass


def sql_failure_metadata(exc: Exception) -> dict:
    text = " ".join(str(getattr(exc, name, ""))[:8192] for name in ("driver_error", "ddbc_error"))
    lowered = text.lower()
    categories = {
        "no-results": ("no results", "not a query", "no result set", "no resultset"),
        "syntax": ("incorrect syntax", "syntax error"),
        "invalid-column": ("invalid column",),
        "invalid-object": ("invalid object",),
        "cross-column-check": ("column check constraint", "references another column"),
        "already-exists": ("already exists", "already an object"),
        "permission": ("permission was denied", "permission denied"),
        "parameter": ("parameter",),
        "transaction": ("transaction",),
    }
    tokens = re.findall(r"(?:syntax near|column name|object name)\s+'([^'\r\n]{1,128})'", text, re.I)
    return {
        "provider_error_type": type(exc).__name__,
        "categories": [name for name, phrases in categories.items() if any(p in lowered for p in phrases)],
        "sql_states": sorted(set(re.findall(r"(?:SQLState|SQLSTATE)\s*[=:]\s*([A-Z0-9]{5})", text))),
        "native_codes": sorted(set(re.findall(r"(?:NativeError|ErrorCode|native error)\s*[=:]\s*(\d{1,8})", text, re.I))),
        "source_token_sha256": [sha256(token.encode()).hexdigest() for token in tokens],
    }


@dataclass(frozen=True)
class Migration:
    name: str
    path: str
    sentinel: str


MIGRATIONS = (
    Migration("consumer/001", "agentic_itops/consumer/migrations/001_workflow.sql", "dbo.consumer_schema"),
    Migration("consumer/002", "agentic_itops/consumer/migrations/002_job_scopes.sql", "dbo.consumer_jobs.scope_key"),
    Migration("review/001", "agentic_itops/review/migrations/001_review.sql", "review.incidents"),
    Migration("review/002", "agentic_itops/review/migrations/002_publisher.sql", "review.publisher_jobs"),
    Migration("review/003", "agentic_itops/review/migrations/003_search_projection.sql", "review.publisher_projections"),
    Migration("runtime/001", "agentic_itops/consumer/runtime_sql/budget.sql", "dbo.itops_runtime_month"),
    Migration("application/001", "agentic_itops/consumer/runtime_sql/application.sql", "dbo.consumer_application_runs"),
    Migration("authority/001", "deployment/sql/001_runtime_boundaries.sql", "dbo.itops_runtime_boundary_version"),
)


def split_batches(text: str) -> list[str]:
    """Recognize GO outside strings, quoted identifiers and nested comments."""
    batches, lines = [], []
    quote, depth = None, 0
    for line in text.splitlines(keepends=True):
        if quote is None and depth == 0:
            if re.fullmatch(r"\s*(?:GO\s*(?:--[^\r\n]*)?|-- batch)\s*", line, re.I):
                if "".join(lines).strip():
                    batches.append("".join(lines).strip())
                lines = []
                continue
            if re.match(r"\s*(?:GO\b|:|!!)", line, re.I):
                raise MigrationError("SQLCMD directives and GO repeat counts are prohibited")
        lines.append(line)
        i = 0
        while i < len(line):
            pair, char = line[i:i + 2], line[i]
            if depth:
                if pair == "/*":
                    depth += 1
                    i += 2
                elif pair == "*/":
                    depth -= 1
                    i += 2
                else:
                    i += 1
            elif quote:
                close = "]" if quote == "[" else quote
                if char == close:
                    if pair == close * 2:
                        i += 2
                        continue
                    quote = None
                i += 1
            elif pair == "--":
                break
            elif pair == "/*":
                depth, i = 1, i + 2
            else:
                if char in ("'", '"', "["):
                    quote = char
                i += 1
    if quote or depth:
        raise MigrationError("unterminated SQL literal, identifier or comment")
    if "".join(lines).strip():
        batches.append("".join(lines).strip())
    return batches


def _drain(cursor) -> None:
    while cursor.nextset():
        pass


def apply_migrations(connection, root: Path, migrations=MIGRATIONS) -> list[dict]:
    """Own explicit SQL transactions; outer transaction absorbs migration-local commits."""
    connection.autocommit = True
    cursor = connection.cursor()
    acquired = False
    receipts = []
    phase = "acquire-lock"
    try:
        cursor.execute(
            "SET XACT_ABORT ON; SET NOCOUNT ON; "
            "DECLARE @r int; EXEC @r=sys.sp_getapplock "
            "@Resource=N'itops-deployment-migrations', @LockMode='Exclusive', "
            "@LockOwner='Session', @LockTimeout=0; SELECT @r;"
        )
        if cursor.fetchone()[0] < 0:
            raise MigrationError("another migration session holds the deployment lock")
        acquired = True
        phase = "migration-ledger"
        cursor.execute(
            "IF OBJECT_ID(N'dbo.itops_deployment_migrations',N'U') IS NULL "
            "CREATE TABLE dbo.itops_deployment_migrations ("
            "migration_id varchar(160) COLLATE Latin1_General_100_BIN2 NOT NULL PRIMARY KEY,"
            "source_sha256 char(64) NOT NULL, applied_at datetime2(7) NOT NULL "
            "CONSTRAINT DF_itops_deployment_applied DEFAULT SYSUTCDATETIME());"
        )
        for migration in migrations:
            phase = f"read-ledger:{migration.name}"
            payload = (root / migration.path).read_bytes()
            digest = sha256(payload).hexdigest()
            batches = split_batches(payload.decode("utf-8-sig"))
            if not batches:
                raise MigrationError(f"empty migration: {migration.name}")
            cursor.execute("SELECT source_sha256 FROM dbo.itops_deployment_migrations WHERE migration_id=?",
                           migration.name)
            row = cursor.fetchone()
            if row:
                if row[0] != digest:
                    raise MigrationError(f"applied migration hash changed: {migration.name}")
                receipts.append({"migration": migration.name, "sha256": digest, "status": "already-applied"})
                continue
            if migration.sentinel.count(".") == 2:
                phase = f"read-sentinel:{migration.name}"
                table, column = migration.sentinel.rsplit(".", 1)
                cursor.execute("SELECT COL_LENGTH(?,?)", table, column)
            else:
                phase = f"read-sentinel:{migration.name}"
                cursor.execute("SELECT OBJECT_ID(?,N'U')", migration.sentinel)
            if cursor.fetchone()[0] is not None:
                raise MigrationError(f"untracked schema exists; explicit reconciliation required: {migration.name}")
            cursor.execute("BEGIN TRANSACTION")
            try:
                for index, batch in enumerate(batches):
                    phase = f"apply:{migration.name}:{index + 1}"
                    cursor.execute(batch)
                    _drain(cursor)
                phase = f"commit:{migration.name}"
                cursor.execute("SELECT @@TRANCOUNT, XACT_STATE()")
                if tuple(cursor.fetchone()) != (1, 1):
                    raise MigrationError(f"migration escaped the owned transaction: {migration.name}")
                cursor.execute(
                    "INSERT INTO dbo.itops_deployment_migrations(migration_id,source_sha256) VALUES (?,?)",
                    migration.name, digest,
                )
                cursor.execute("COMMIT TRANSACTION")
            except BaseException:
                cursor.execute("IF @@TRANCOUNT>0 ROLLBACK TRANSACTION")
                raise
            receipts.append({"migration": migration.name, "sha256": digest, "status": "applied"})
    except MigrationError:
        raise
    except Exception as exc:
        raise MigrationError(json.dumps({"phase": phase, **sql_failure_metadata(exc)}, sort_keys=True)) from exc
    finally:
        if acquired:
            cursor.execute("EXEC sys.sp_releaseapplock @Resource=N'itops-deployment-migrations', @LockOwner='Session'")
        cursor.close()
    return receipts


def sql_identity_statement(name: str, client_id: str) -> str:
    if not re.fullmatch(r"itops_(?:review|worker|tools|publisher)", name):
        raise MigrationError("SQL principal must be one of the four owned runtime users")
    principal = UUID(client_id)
    if not principal.int:
        raise MigrationError("zero principal IDs are prohibited")
    sid = "0x" + principal.bytes_le.hex()
    return (
        f"IF DATABASE_PRINCIPAL_ID(N'{name}') IS NULL "
        f"CREATE USER [{name}] WITH SID={sid}, TYPE=E, DEFAULT_SCHEMA=dbo; "
        f"IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name=N'{name}' "
        f"AND sid={sid} AND type='E') THROW 51020, 'Existing runtime user identity differs', 1;"
    )


def apply_grants(connection, client_ids: dict[str, str], allowlist: dict[str, dict[str, list[str]]]) -> None:
    """Only a source-controlled object allowlist supplies permission/object names."""
    if set(client_ids) != set(allowlist) or set(client_ids) != {"review", "worker", "tools", "publisher"}:
        raise MigrationError("exactly the four runtime identity/grant profiles are required")
    valid = {"SELECT", "INSERT", "UPDATE", "EXECUTE"}
    statements = []
    expected = set()
    for service, objects in allowlist.items():
        user = f"itops_{service}"
        statements.append(sql_identity_statement(user, client_ids[service]))
        for obj, permissions in objects.items():
            if not re.fullmatch(r"(?:dbo|review)\.[A-Za-z][A-Za-z0-9_]*", obj):
                raise MigrationError("grant object must be an explicit owned table or stored procedure")
            if not permissions or not set(permissions) <= valid or len(set(permissions)) != len(permissions):
                raise MigrationError("invalid object grant")
            schema, name = obj.split(".")
            statements.append(f"GRANT {','.join(permissions)} ON OBJECT::[{schema}].[{name}] TO [{user}];")
            expected.update((user, schema, name, permission) for permission in permissions)
    connection.autocommit = True
    cursor = connection.cursor()
    cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION")
    try:
        cursor.execute(
            "IF OBJECT_ID(N'dbo.itops_runtime_boundary_version',N'U') IS NULL "
            "THROW 51021, 'Runtime authority migration must precede grants', 1; "
            "IF (SELECT COUNT(*) FROM sys.triggers WHERE is_disabled=0 AND is_instead_of_trigger=0 AND ("
            "(parent_id=OBJECT_ID(N'review.incidents') AND name='itops_publisher_incident_boundary') OR "
            "(parent_id=OBJECT_ID(N'review.revisions') AND name IN ('itops_publisher_revision_boundary','revisions_immutable')) OR "
            "(parent_id=OBJECT_ID(N'review.publications') AND name IN ('itops_publisher_publication_boundary','publications_immutable')) OR "
            "(parent_id=OBJECT_ID(N'review.receipts') AND name='receipts_immutable') OR "
            "(parent_id=OBJECT_ID(N'review.publisher_projections') AND name='publisher_projection_immutable') OR "
            "(parent_id=OBJECT_ID(N'review.outbox') AND name='itops_outbox_authority_boundary') OR "
            "(parent_id=OBJECT_ID(N'dbo.consumer_application_runs') AND name='consumer_application_runs_immutable')))<>9 "
            "THROW 51022, 'Runtime authority triggers are absent or disabled', 1;"
        )
        for statement in statements:
            cursor.execute(statement)
            _drain(cursor)
        # Any unexpected database role membership is an approval blocker.
        cursor.execute(
            "SELECT COUNT(*) FROM sys.database_role_members m JOIN sys.database_principals p "
            "ON p.principal_id=m.member_principal_id "
            "WHERE p.name IN ('itops_review','itops_worker','itops_tools','itops_publisher')"
        )
        if cursor.fetchone()[0]:
            raise MigrationError("runtime identity has database role membership; refuse broad inherited grants")
        cursor.execute(
            "SELECT p.name, dp.class, dp.state, dp.permission_name, "
            "OBJECT_SCHEMA_NAME(dp.major_id), OBJECT_NAME(dp.major_id), dp.minor_id "
            "FROM sys.database_permissions dp JOIN sys.database_principals p "
            "ON p.principal_id=dp.grantee_principal_id "
            "WHERE p.name IN ('itops_review','itops_worker','itops_tools','itops_publisher')"
        )
        for user, permission_class, state, permission, schema, obj, column in cursor.fetchall():
            if permission_class == 0 and permission == "CONNECT" and state == "G":
                continue
            if (permission_class != 1 or column != 0 or state != "G"
                    or (user, schema, obj, permission) not in expected):
                raise MigrationError("runtime identity has unexpected explicit permissions; reconciliation required")
        cursor.execute(
            "SELECT COUNT(*) FROM sys.schemas s JOIN sys.database_principals p ON p.principal_id=s.principal_id "
            "WHERE p.name IN ('itops_review','itops_worker','itops_tools','itops_publisher')"
        )
        if cursor.fetchone()[0]:
            raise MigrationError("runtime identity owns a schema; refuse implicit control authority")
        cursor.execute(
            "SELECT COUNT(*) FROM sys.objects o JOIN sys.database_principals p ON p.principal_id=o.principal_id "
            "WHERE p.name IN ('itops_review','itops_worker','itops_tools','itops_publisher')"
        )
        if cursor.fetchone()[0]:
            raise MigrationError("runtime identity owns an object; refuse implicit control authority")
        cursor.execute("COMMIT TRANSACTION")
    except BaseException:
        cursor.execute("IF @@TRANCOUNT>0 ROLLBACK TRANSACTION")
        raise
    finally:
        cursor.close()
