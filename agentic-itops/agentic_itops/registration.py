"""Explicitly approved, version-journaled registration of the complete portfolio."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Protocol
from uuid import uuid4

from .foundry_contracts import AgentVersionReference
from .portfolio import AGENT_FOLDERS, CompiledPortfolio, build_sdk_definitions, canonical_json


class RegistrationError(RuntimeError):
    pass


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def project_endpoint(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"https://[a-z0-9][a-z0-9-]*\.services\.ai\.azure\.com/api/projects/"
        r"[A-Za-z0-9][A-Za-z0-9_-]*", value
    ):
        raise RegistrationError("explicit commercial Foundry project endpoint required")
    return value


@dataclass(frozen=True)
class RegistrationPlan:
    document_json: str

    @property
    def document(self) -> dict:
        return json.loads(self.document_json)

    @property
    def sha256(self) -> str:
        return _digest(self.document)


def prepare_registration(
    portfolio: CompiledPortfolio, endpoint: str, model_deployment: str, *, sampling: str,
) -> RegistrationPlan:
    """Offline only. Model defaults must be selected explicitly, never inferred."""
    endpoint = project_endpoint(endpoint)
    if (not portfolio.bindings_complete or portfolio.mode != "trusted_dispatch"
            or {agent.name for agent in portfolio.agents} != set(AGENT_FOLDERS.values())
            or len(portfolio.agents) != 9):
        raise RegistrationError("registration requires all nine completely bound trusted-dispatch agents")
    if not isinstance(model_deployment, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", model_deployment):
        raise RegistrationError("explicit model deployment required")
    if any(agent.model_deployment != model_deployment for agent in portfolio.agents):
        raise RegistrationError("every agent must bind the explicitly selected model deployment")
    if sampling not in ("source", "model_defaults"):
        raise RegistrationError("sampling must explicitly select source or model_defaults")
    definitions = build_sdk_definitions(portfolio)
    entries = []
    for agent in portfolio.agents:
        definition = definitions[agent.name].as_dict()
        if any(tool.get("type") != "function" for tool in definition["tools"]):
            raise RegistrationError("this registration path requires mediated, function-only tools")
        if sampling == "model_defaults":
            del definition["temperature"]
            del definition["top_p"]
        entries.append({
            "name": agent.name, "definition": definition,
            "definition_sha256": _digest(definition),
            "source_content_sha256": agent.content_sha256,
            "source_sampling": {"temperature": agent.temperature, "top_p": agent.top_p},
        })
    return RegistrationPlan(canonical_json({
        "schema_version": 1, "project_endpoint": endpoint,
        "portfolio_sha256": portfolio.content_sha256,
        "model_deployment": model_deployment, "sampling": sampling, "agents": entries,
        "live_acceptance": False, "execution_allowed": False,
    }))


@dataclass(frozen=True)
class RegisteredVersion:
    name: str
    version: str
    definition: dict
    metadata: dict[str, str]
    status: str | None = None
    draft: bool = False


class VersionService(Protocol):
    project_endpoint: str

    def create(self, name: str, definition: dict, metadata: dict[str, str]) -> RegisteredVersion: ...
    def get(self, name: str, version: str) -> RegisteredVersion: ...
    def matching(self, name: str, metadata: dict[str, str]) -> list[RegisteredVersion]: ...


class RegistrationJournal:
    """One immutable plan per local journal. No transaction spans a network call."""

    def __init__(self, path: Path, plan: RegistrationPlan):
        self.path, self.plan = Path(path), plan
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("xb"):
                pass
            new = True
        except FileExistsError:
            new = False
        self.connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA synchronous=FULL")
            with self.transaction() as connection:
                if new:
                    connection.execute("CREATE TABLE registration (plan_sha256 TEXT PRIMARY KEY, document TEXT NOT NULL)")
                    connection.execute(
                        "CREATE TABLE versions (name TEXT PRIMARY KEY, state TEXT NOT NULL, "
                        "attempt TEXT, version TEXT, error_type TEXT)"
                    )
                    connection.execute("INSERT INTO registration VALUES (?,?)", (plan.sha256, plan.document_json))
                    connection.executemany(
                        "INSERT INTO versions(name,state) VALUES (?, 'pending')",
                        [(entry["name"],) for entry in plan.document["agents"]],
                    )
                stored = connection.execute("SELECT plan_sha256, document FROM registration").fetchall()
                if len(stored) != 1 or tuple(stored[0]) != (plan.sha256, plan.document_json):
                    raise RegistrationError("journal belongs to a different project, source or settings plan")
                rows = connection.execute("SELECT name,state,attempt,version FROM versions").fetchall()
                if {row["name"] for row in rows} != {entry["name"] for entry in plan.document["agents"]}:
                    raise RegistrationError("journal does not contain the exact nine-agent inventory")
                for row in rows:
                    if row["state"] not in ("pending", "attempting", "created", "verified"):
                        raise RegistrationError("journal contains an unknown registration state")
                    if row["state"] != "pending" and not row["attempt"]:
                        raise RegistrationError("journal is missing its durable attempt identity")
                    if row["state"] in ("created", "verified"):
                        AgentVersionReference(row["name"], row["version"])
        except BaseException:
            self.connection.close()
            raise

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self.connection.close()

    def row(self, name: str) -> dict:
        row = self.connection.execute("SELECT * FROM versions WHERE name=?", (name,)).fetchone()
        if row is None:
            raise RegistrationError("agent is outside the registration journal")
        return dict(row)

    def begin(self, name: str) -> str:
        attempt = str(uuid4())
        with self.transaction() as connection:
            updated = connection.execute(
                "UPDATE versions SET state='attempting',attempt=? WHERE name=? AND state='pending'",
                (attempt, name),
            )
            if updated.rowcount != 1:
                raise RegistrationError("another registration attempt already owns this agent")
        return attempt

    def created(self, name: str, attempt: str, version: str) -> None:
        AgentVersionReference(name, version)
        with self.transaction() as connection:
            row = self.row(name)
            if row["attempt"] != attempt or row["state"] not in ("attempting", "created", "verified"):
                raise RegistrationError("registration attempt lost ownership")
            if row["version"] is not None and row["version"] != version:
                raise RegistrationError("one attempt cannot own multiple remote versions")
            if row["state"] == "attempting":
                connection.execute("UPDATE versions SET state='created',version=? WHERE name=?", (version, name))

    def verified(self, name: str, attempt: str, version: str) -> None:
        with self.transaction() as connection:
            updated = connection.execute(
                "UPDATE versions SET state='verified',error_type=NULL "
                "WHERE name=? AND attempt=? AND version=? AND state IN ('created','verified')",
                (name, attempt, version),
            )
            if updated.rowcount != 1:
                raise RegistrationError("registration verification lost ownership")

    def failed(self, name: str, error: BaseException) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE versions SET error_type=? WHERE name=?", (type(error).__name__, name),
            )


def _metadata(plan: RegistrationPlan, entry: dict, attempt: str) -> dict[str, str]:
    return {
        "itops_plan": plan.sha256, "itops_attempt": attempt,
        "itops_definition": entry["definition_sha256"],
    }


def _check_version(value: RegisteredVersion, entry: dict, metadata: dict[str, str]) -> None:
    if not isinstance(value, RegisteredVersion):
        raise RegistrationError("version service returned an untyped receipt")
    try:
        AgentVersionReference(value.name, value.version)
    except ValueError as exc:
        raise RegistrationError("version receipt lacks an exact supported version") from exc
    if value.name != entry["name"] or any(value.metadata.get(key) != item for key, item in metadata.items()):
        raise RegistrationError("remote version ownership does not match this exact attempt")
    if value.draft or value.status not in (None, "active"):
        raise RegistrationError("remote version is draft, pending or unavailable; resume verification later")
    if canonical_json(value.definition) != canonical_json(entry["definition"]):
        raise RegistrationError("remote definition differs from the approved schema, tools or model settings")


def register_portfolio(
    plan: RegistrationPlan, journal: RegistrationJournal, service: VersionService,
    *, approved_sha256: str,
) -> dict:
    if approved_sha256 != plan.sha256 or journal.plan != plan:
        raise RegistrationError("explicit approval of the exact registration plan digest is required")
    if service.project_endpoint != plan.document["project_endpoint"]:
        raise RegistrationError("version service targets a different project than the approved plan")
    versions = {}
    for entry in plan.document["agents"]:
        name = entry["name"]
        try:
            row = journal.row(name)
            if row["state"] == "pending":
                attempt = journal.begin(name)
                metadata = _metadata(plan, entry, attempt)
                created = service.create(name, entry["definition"], metadata)
                _check_version(created, entry, metadata)
                journal.created(name, attempt, created.version)
            else:
                attempt = row["attempt"]
                metadata = _metadata(plan, entry, attempt)
                if row["state"] == "attempting":
                    matches = service.matching(name, metadata)
                    if not isinstance(matches, list) or len(matches) != 1:
                        raise RegistrationError(
                            "uncertain create outcome: require exactly one matching remote version; "
                            "no automatic create retry or deletion"
                        )
                    _check_version(matches[0], entry, metadata)
                    journal.created(name, attempt, matches[0].version)
            version = journal.row(name)["version"]
            remote = service.get(name, version)
            _check_version(remote, entry, metadata)
            if remote.version != version:
                raise RegistrationError("read-back returned a different version")
            journal.verified(name, attempt, version)
            versions[name] = version
        except Exception as exc:
            journal.failed(name, exc)
            raise
    return {
        "schema_version": 1, "status": "registered_and_read_back",
        "project_endpoint": plan.document["project_endpoint"], "plan_sha256": plan.sha256,
        "portfolio_sha256": plan.document["portfolio_sha256"], "versions": versions,
        "live_acceptance": False, "execution_allowed": False,
    }


def bind_registered_runtime_agents(portfolio: CompiledPortfolio, plan: RegistrationPlan, receipt: dict):
    """Bind trusted deployment artifacts, not user-uploaded or model-authored receipts."""
    from .consumer.responses import RuntimeAgent
    from .runtime import bind_runtime_agents

    expected_fields = {
        "schema_version", "status", "project_endpoint", "plan_sha256",
        "portfolio_sha256", "versions", "live_acceptance", "execution_allowed",
    }
    document = plan.document
    if plan != prepare_registration(
        portfolio, document["project_endpoint"], document["model_deployment"], sampling=document["sampling"],
    ):
        raise RegistrationError("registration plan no longer matches the compiled source portfolio")
    if (not isinstance(receipt, dict) or set(receipt) != expected_fields
            or type(receipt["schema_version"]) is not int or receipt["schema_version"] != 1
            or receipt["status"] != "registered_and_read_back"
            or receipt["project_endpoint"] != document["project_endpoint"]
            or receipt["plan_sha256"] != plan.sha256
            or receipt["portfolio_sha256"] != portfolio.content_sha256
            or receipt["live_acceptance"] is not False or receipt["execution_allowed"] is not False
            or not isinstance(receipt["versions"], dict)):
        raise RegistrationError("runtime requires a complete matching registration receipt")
    agents = bind_runtime_agents(portfolio, receipt["versions"])
    result = {}
    for name, agent in agents.items():
        definition = agent.definition
        result[name] = RuntimeAgent.create(
            name, agent.reference.version, definition["output_schema"], definition["functions"],
            {**definition["provenance"], "registration_plan_sha256": plan.sha256,
             "project_endpoint": document["project_endpoint"]},
            function_outputs=definition["function_outputs"],
        )
    return result


class FoundryVersionService:
    """Adapter for an explicitly credentialed, retry-disabled AIProjectClient."""

    def __init__(self, client, *, max_versions: int = 100):
        if type(max_versions) is not int or not 1 <= max_versions <= 1000:
            raise RegistrationError("reconciliation requires a bounded version scan")
        # The pinned SDK exposes its endpoint through configuration, not a public property.
        self.project_endpoint = project_endpoint(getattr(getattr(client, "_config", None), "endpoint", None))
        self.client, self.max_versions = client, max_versions

    @staticmethod
    def _receipt(value) -> RegisteredVersion:
        return RegisteredVersion(
            name=value.name, version=value.version, definition=value.definition.as_dict(),
            metadata=dict(value.metadata), status=value.status, draft=value.draft is True,
        )

    def create(self, name: str, definition: dict, metadata: dict[str, str]) -> RegisteredVersion:
        from azure.ai.projects.models import PromptAgentDefinition

        model = PromptAgentDefinition(definition)
        if model.as_dict() != definition:
            raise RegistrationError("SDK reconstruction changed the approved definition")
        return self._receipt(self.client.agents.create_version(
            agent_name=name, definition=model, metadata=metadata, draft=False, retry_total=0,
        ))

    def get(self, name: str, version: str) -> RegisteredVersion:
        return self._receipt(self.client.agents.get_version(
            agent_name=name, agent_version=version, retry_total=0,
        ))

    def matching(self, name: str, metadata: dict[str, str]) -> list[RegisteredVersion]:
        matches = []
        for index, value in enumerate(self.client.agents.list_versions(
            agent_name=name, limit=min(self.max_versions, 100), retry_total=0,
        )):
            if index >= self.max_versions:
                raise RegistrationError("reconciliation scan bound exceeded; manual inspection required")
            if all(value.metadata.get(key) == item for key, item in metadata.items()):
                matches.append(self._receipt(value))
        return matches
