from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import re
from typing import Callable
from uuid import UUID

from .contracts import ContractError, digest, iso, utc
from .storage_contract import IntegrityConflict
from ..foundry_contracts import AgentVersionReference


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class BudgetLimits:
    """Versioned accounting policy; observe_only keeps thresholds as metadata."""
    policy_version: str
    pricing_reference: str
    model_context_tokens: int
    max_output_tokens: int
    input_nanodollars_per_token: int
    output_nanodollars_per_token: int
    monthly_nanodollars: int
    workflow_tokens: int
    api_calls: int = 32
    tool_calls: int = 32
    deadline_seconds: int = 600
    usage_mode: str = "enforced"

    def __post_init__(self):
        if any(not isinstance(value, str) or not value.strip()
               for value in (self.policy_version, self.pricing_reference)):
            raise ContractError("explicit versioned model pricing is required")
        if self.usage_mode not in ("enforced", "observe_only"):
            raise ContractError("usage mode must be enforced or observe_only")
        for key, value in asdict(self).items():
            if key not in ("policy_version", "pricing_reference", "usage_mode"):
                if type(value) is not int or value < 1 or value > 10 ** 15:
                    raise ContractError(f"invalid model budget limit: {key}")
        if self.max_output_tokens >= self.model_context_tokens:
            raise ContractError("output budget must leave room for model input")
        if self.reserved_nanodollars > 2 ** 62:
            raise ContractError("model reservation exceeds ledger integer precision")

    @property
    def reserved_tokens(self):
        return self.model_context_tokens + self.max_output_tokens

    @property
    def reserved_nanodollars(self):
        return (self.model_context_tokens * self.input_nanodollars_per_token
                + self.max_output_tokens * self.output_nanodollars_per_token)

    @property
    def sha256(self):
        value = asdict(self)
        # Legacy enforced policies must retain their exact durable workflow hash.
        if self.usage_mode == "enforced":
            value.pop("usage_mode")
        return digest(value)

    @property
    def caps_enforced(self):
        return self.usage_mode == "enforced"

    @classmethod
    def parse(cls, value):
        required = {field.name for field in fields(cls)} - {"usage_mode"}
        if (not isinstance(value, dict) or not required <= set(value)
                or set(value) - required - {"usage_mode"}):
            raise ContractError("every model budget field must be explicitly configured")
        return cls(**value)


@dataclass(frozen=True)
class UsageAuthorization:
    approval_id: str
    actor_oid: str
    expires_at_utc: str
    policy_hash: str

    def __post_init__(self):
        if (not isinstance(self.approval_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}", self.approval_id)):
            raise ContractError("usage authorization requires an explicit approval identity")
        try:
            actor = UUID(self.actor_oid)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ContractError("usage authorization requires an operator identity") from exc
        if not actor.int or str(actor) != self.actor_oid:
            raise ContractError("usage authorization requires a canonical operator identity")
        if not isinstance(self.policy_hash, str) or not re.fullmatch("[a-f0-9]{64}", self.policy_hash):
            raise ContractError("usage authorization requires an exact policy hash")
        if not isinstance(self.expires_at_utc, str):
            raise ContractError("usage authorization requires an explicit UTC expiry")
        utc(self.expires_at_utc)


def _key(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ContractError("invalid runtime ledger identifier")
    return value


@dataclass(frozen=True)
class MonthlyLimitAmendment:
    approval_id: str
    workflow_id: str
    scope_key: str
    month_key: str
    policy_hash: str
    expected_limit_nanodollars: int
    new_limit_nanodollars: int
    expected_accounted_nanodollars: int
    expires_at_utc: str

    def __post_init__(self):
        for value in (self.approval_id, self.workflow_id, self.scope_key):
            _key(value)
        if (not isinstance(self.month_key, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", self.month_key)
                or not isinstance(self.policy_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", self.policy_hash)):
            raise ContractError("monthly amendment requires an exact month and policy hash")
        amounts = (self.expected_limit_nanodollars, self.new_limit_nanodollars,
                   self.expected_accounted_nanodollars)
        if (any(type(value) is not int or not 0 <= value <= 10 ** 15 for value in amounts)
                or not 0 < self.expected_limit_nanodollars < self.new_limit_nanodollars
                or self.expected_accounted_nanodollars > self.expected_limit_nanodollars):
            raise ContractError("invalid approved monthly limit amendment")
        if not isinstance(self.expires_at_utc, str):
            raise ContractError("monthly amendment requires an explicit UTC expiry")
        utc(self.expires_at_utc)


class BudgetLedger:
    """Durable conservative reservations; not an Azure subscription spending cap."""

    def __init__(self, connection_factory: Callable, limits: BudgetLimits, scope_key: str,
                 *, dialect: str = "sqlserver", clock: Callable | None = None,
                 monthly_limit_amendment: MonthlyLimitAmendment | None = None,
                 usage_authorization: UsageAuthorization | None = None):
        if dialect not in ("sqlserver", "sqlite-local-test"):
            raise ContractError("an explicit supported budget database is required")
        if clock is not None and dialect != "sqlite-local-test":
            raise ContractError("production budget time must come from SQL Server")
        self.factory, self.limits, self.scope = connection_factory, limits, _key(scope_key)
        self.dialect = dialect
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        if usage_authorization is not None and (
                not isinstance(usage_authorization, UsageAuthorization)
                or limits.caps_enforced or usage_authorization.policy_hash != limits.sha256):
            raise ContractError("usage authorization differs from the configured observe-only policy")
        self.usage_authorization = usage_authorization
        if monthly_limit_amendment is not None and not limits.caps_enforced:
            raise ContractError("observe-only accounting cannot amend stored monthly limits")
        if monthly_limit_amendment is not None and (
                not isinstance(monthly_limit_amendment, MonthlyLimitAmendment)
                or monthly_limit_amendment.scope_key != self.scope
                or monthly_limit_amendment.policy_hash != limits.sha256
                or monthly_limit_amendment.approval_id != limits.policy_version
                or monthly_limit_amendment.new_limit_nanodollars != limits.monthly_nanodollars):
            raise ContractError("monthly amendment differs from the configured scope or approved policy")
        self.monthly_limit_amendment = monthly_limit_amendment

    def _authorize_usage(self, now):
        if not self.limits.caps_enforced and (
                self.usage_authorization is None
                or self.usage_authorization.policy_hash != self.limits.sha256
                or now >= utc(self.usage_authorization.expires_at_utc)):
            raise ContractError("observe-only usage requires current exact operator authorization")

    @contextmanager
    def _transaction(self):
        connection = self.factory()
        try:
            cursor = connection.cursor()
            cursor.execute("BEGIN IMMEDIATE" if self.dialect == "sqlite-local-test" else
                           "SET XACT_ABORT ON; SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            yield cursor
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _now(self, cursor) -> datetime:
        if self.dialect == "sqlite-local-test":
            return utc(self.clock())
        cursor.execute("SELECT CONCAT(CONVERT(varchar(33), SYSUTCDATETIME(), 127), 'Z')")
        return utc(cursor.fetchone()[0])

    def _locked(self, table: str) -> str:
        return table + (" WITH (UPDLOCK,HOLDLOCK)" if self.dialect == "sqlserver" else "")

    def migrate(self):
        filename = "budget.sql" if self.dialect == "sqlserver" else "budget.local-test.sql"
        script = Path(__file__).with_name("runtime_sql").joinpath(filename).read_text(encoding="utf-8")
        with self._transaction() as cursor:
            for statement in script.split("-- batch"):
                if statement.strip():
                    cursor.execute(statement)

    def open_workflow(self, workflow_id: str) -> None:
        _key(workflow_id)
        amended = False
        with self._transaction() as cursor:
            now = self._now(cursor)
            self._authorize_usage(now)
            cursor.execute(f"SELECT policy_hash,deadline_at,scope_key FROM {self._locked('itops_runtime_workflow')} "
                           "WHERE workflow_id=?", (workflow_id,))
            existing = cursor.fetchone()
            if existing:
                if existing[0] != self.limits.sha256 or existing[2] != self.scope:
                    raise IntegrityConflict("workflow budget policy cannot change")
                if now >= utc(existing[1]):
                    raise BudgetExceeded("workflow deadline expired; reopening cannot reset it")
                return
            amendment = self.monthly_limit_amendment
            if amendment is not None:
                if (workflow_id != amendment.workflow_id or now >= utc(amendment.expires_at_utc)
                        or now.strftime("%Y-%m") != amendment.month_key):
                    raise IntegrityConflict("monthly amendment is expired or bound to another workflow/month")
                cursor.execute(
                    f"SELECT limit_nanos,accounted_nanos FROM {self._locked('itops_runtime_month')} "
                    "WHERE scope_key=? AND month_key=?", (self.scope, amendment.month_key))
                period = cursor.fetchone()
                if period is None or tuple(period) != (
                        amendment.expected_limit_nanodollars, amendment.expected_accounted_nanodollars):
                    raise IntegrityConflict("monthly amendment baseline differs from durable accounting")
                cursor.execute(
                    "SELECT COUNT(*) FROM itops_runtime_call WHERE scope_key=? AND state IN ('reserved','overrun')",
                    (self.scope,))
                if cursor.fetchone()[0]:
                    raise IntegrityConflict("monthly amendment requires reconciled provider attempts")
                cursor.execute(
                    "UPDATE itops_runtime_month SET limit_nanos=? WHERE scope_key=? AND month_key=?",
                    (amendment.new_limit_nanodollars, self.scope, amendment.month_key))
                amended = True
            cursor.execute(
                "INSERT INTO itops_runtime_workflow "
                "(workflow_id,policy_hash,scope_key,deadline_at,api_calls,tool_calls,tokens_accounted) "
                "VALUES (?,?,?,?,0,0,0)",
                (workflow_id, self.limits.sha256, self.scope,
                 iso(now + timedelta(seconds=self.limits.deadline_seconds))),
            )
        if amended:
            assert amendment is not None
            logging.getLogger(__name__).info(
                "Approved monthly limit amendment committed: approval=%s workflow=%s policy=%s amendment=%s",
                amendment.approval_id, workflow_id, self.limits.sha256, digest(asdict(amendment)))

    def inspect_month(self) -> dict:
        with self._transaction() as cursor:
            month = self._now(cursor).strftime("%Y-%m")
            cursor.execute("SELECT limit_nanos,accounted_nanos FROM itops_runtime_month "
                           "WHERE scope_key=? AND month_key=?", (self.scope, month))
            row = cursor.fetchone()
        if row is not None and (any(type(value) is not int or value < 0 for value in row) or row[0] == 0):
            raise IntegrityConflict("invalid durable monthly accounting")
        return {
            "month_key": month,
            "usage_mode": self.limits.usage_mode,
            "caps_enforced": self.limits.caps_enforced,
            "policy_hash": self.limits.sha256,
            "configured_limit_nanodollars": self.limits.monthly_nanodollars,
            "stored_limit_nanodollars": row[0] if row is not None else None,
            "effective_limit_nanodollars": (
                min(row[0], self.limits.monthly_nanodollars) if row is not None
                else self.limits.monthly_nanodollars) if self.limits.caps_enforced else None,
            "accounted_nanodollars": row[1] if row is not None else 0,
        }

    def inspect_workflow(self, workflow_id: str, *, include_dispatches: bool = False) -> dict:
        """Read historical accounting without admitting work or releasing reservations."""
        with self._transaction() as cursor:
            cursor.execute(
                "SELECT policy_hash,deadline_at,api_calls,tool_calls,tokens_accounted,stop_reason "
                "FROM itops_runtime_workflow WHERE workflow_id=? AND scope_key=?",
                (_key(workflow_id), self.scope))
            row = cursor.fetchone()
            if row is None:
                return {"status": "not_admitted"}
            cursor.execute(
                "SELECT state,COUNT(*),SUM(charged_nanos) FROM itops_runtime_call "
                "WHERE workflow_id=? AND scope_key=? GROUP BY state",
                (workflow_id, self.scope))
            states = cursor.fetchall()
            if include_dispatches:
                cursor.execute(
                    "SELECT agent_name,state,COUNT(*) FROM itops_runtime_call "
                    "WHERE workflow_id=? AND scope_key=? GROUP BY agent_name,state", (workflow_id, self.scope))
                agent_rows = cursor.fetchall()
                cursor.execute(
                    "SELECT tool_name,COUNT(*) FROM itops_runtime_tool WHERE workflow_id=? GROUP BY tool_name",
                    (workflow_id,))
                tool_rows = cursor.fetchall()
        summary = self._call_summary(states)
        result = {
            "status": "admitted", "policy_hash": row[0], "deadline_at_utc": iso(utc(row[1])),
            "api_calls": row[2], "tool_calls": row[3], "tokens_accounted": row[4],
            "stop_reason": row[5], **summary,
        }
        if include_dispatches:
            by_agent = {}
            for name, state, count in agent_rows:
                if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", name)
                        or state not in summary["call_states"] or type(count) is not int or count < 1):
                    raise IntegrityConflict("invalid per-agent accounting")
                by_agent.setdefault(name, {})[state] = count
            by_tool = {}
            for name, count in tool_rows:
                _key(name)
                if type(count) is not int or count < 1:
                    raise IntegrityConflict("invalid per-tool accounting")
                by_tool[name] = count
            if sum(count for _, _, count in agent_rows) != row[2] or sum(by_tool.values()) != row[3]:
                raise IntegrityConflict("dispatch diagnostics differ from durable workflow counters")
            result["dispatch_diagnostic"] = {
                "provider_calls_by_agent": by_agent, "recorded_dispatches_by_tool": by_tool,
                "meaning": "Provider accounting and dispatch attempts, not proof of successful stage outputs.",
            }
        return result

    @staticmethod
    def _call_summary(states):
        if any(state not in {"reserved", "settled", "unknown", "overrun"}
               or type(count) is not int or count < 1
               or type(charge) is not int or charge < 0 for state, count, charge in states):
            raise IntegrityConflict("invalid durable model accounting summary")
        return {
            "call_states": {state: count for state, count, _ in states},
            "accounted_nanodollars": sum(charge for _, _, charge in states),
            "accounting_basis": "conservative_reservations_not_a_billing_invoice",
        }

    def inspect_scope(self) -> dict:
        """Read all scope history, including earlier policies, months and unknown charges."""
        with self._transaction() as cursor:
            cursor.execute(
                "SELECT COUNT(*),COALESCE(SUM(api_calls),0),COALESCE(SUM(tool_calls),0) "
                "FROM itops_runtime_workflow WHERE scope_key=?", (self.scope,))
            row = cursor.fetchone()
            cursor.execute(
                "SELECT state,COUNT(*),SUM(charged_nanos) FROM itops_runtime_call "
                "WHERE scope_key=? GROUP BY state", (self.scope,))
            states = cursor.fetchall()
        summary = self._call_summary(states)
        if (any(type(value) is not int or value < 0 for value in row)
                or row[1] != sum(summary["call_states"].values())):
            raise IntegrityConflict("scope counters differ from durable provider reservations")
        return {"scope_basis": "all_history_for_configured_scope", "workflow_count": row[0],
                "api_calls": row[1], "tool_calls": row[2], **summary}

    def _workflow(self, cursor, workflow_id: str, now: datetime):
        self._authorize_usage(now)
        cursor.execute(
            f"SELECT policy_hash,scope_key,deadline_at,api_calls,tool_calls,tokens_accounted,stop_reason "
            f"FROM {self._locked('itops_runtime_workflow')} WHERE workflow_id=?", (_key(workflow_id),))
        row = cursor.fetchone()
        if row is None or row[0] != self.limits.sha256 or row[1] != self.scope:
            raise IntegrityConflict("workflow budget is absent or bound to another policy/scope")
        if now >= utc(row[2]):
            raise BudgetExceeded("workflow deadline expired")
        if row[6] is not None:
            raise BudgetExceeded(f"workflow blocked: {row[6]}")
        return row

    def remaining_seconds(self, workflow_id: str) -> float:
        with self._transaction() as cursor:
            now = self._now(cursor)
            row = self._workflow(cursor, workflow_id, now)
            return (utc(row[2]) - now).total_seconds()

    def reserve(self, workflow_id: str, invocation_id: str,
                agent: AgentVersionReference, request_sha256: str) -> None:
        _key(invocation_id)
        if not re.fullmatch(r"[0-9a-f]{64}", request_sha256):
            raise ContractError("request fingerprint is required before reserving model spend")
        with self._transaction() as cursor:
            now = self._now(cursor)
            month = now.strftime("%Y-%m")
            row = self._workflow(cursor, workflow_id, now)
            cursor.execute(
                f"SELECT limit_nanos,accounted_nanos FROM {self._locked('itops_runtime_month')} "
                "WHERE scope_key=? AND month_key=?", (self.scope, month))
            period = cursor.fetchone()
            if period is None:
                cursor.execute("INSERT INTO itops_runtime_month VALUES (?,?,?,0)",
                               (self.scope, month, self.limits.monthly_nanodollars))
                period = (self.limits.monthly_nanodollars, 0)
            if self.limits.caps_enforced and (row[3] >= self.limits.api_calls
                    or row[5] + self.limits.reserved_tokens > self.limits.workflow_tokens
                    or period[1] + self.limits.reserved_nanodollars
                    > min(period[0], self.limits.monthly_nanodollars)):
                raise BudgetExceeded("model invocation would exceed its durable budget")
            cursor.execute(f"SELECT invocation_id FROM {self._locked('itops_runtime_call')} "
                           "WHERE invocation_id=?", (invocation_id,))
            if cursor.fetchone():
                raise IntegrityConflict("an invocation reservation cannot authorize another provider attempt")
            cursor.execute(
                "INSERT INTO itops_runtime_call "
                "(invocation_id,workflow_id,scope_key,month_key,agent_name,agent_version,request_hash,"
                "state,reserved_tokens,reserved_nanos,input_tokens,output_tokens,charged_nanos) "
                "VALUES (?,?,?,?,?,?,?,'reserved',?,?,NULL,NULL,?)",
                (invocation_id, workflow_id, self.scope, month, agent.name, agent.version, request_sha256,
                 self.limits.reserved_tokens, self.limits.reserved_nanodollars, self.limits.reserved_nanodollars))
            cursor.execute("UPDATE itops_runtime_month SET accounted_nanos=accounted_nanos+? "
                           "WHERE scope_key=? AND month_key=?",
                           (self.limits.reserved_nanodollars, self.scope, month))
            cursor.execute("UPDATE itops_runtime_workflow SET api_calls=api_calls+1,"
                           "tokens_accounted=tokens_accounted+? WHERE workflow_id=?",
                           (self.limits.reserved_tokens, workflow_id))

    def settle(self, invocation_id: str, input_tokens: int | None = None,
               output_tokens: int | None = None) -> str:
        if (input_tokens is None) != (output_tokens is None):
            raise ContractError("partial usage cannot release a model reservation")
        if input_tokens is not None and any(type(n) is not int or n < 0 or n > 10 ** 9
                                           for n in (input_tokens, output_tokens)):
            raise ContractError("invalid provider usage cannot release a reservation")
        with self._transaction() as cursor:
            hint = " WITH (READUNCOMMITTED)" if self.dialect == "sqlserver" else ""
            cursor.execute(f"SELECT workflow_id,scope_key,month_key FROM itops_runtime_call{hint} "
                           "WHERE invocation_id=?", (_key(invocation_id),))
            keys = cursor.fetchone()
            if keys is None or keys[1] != self.scope:
                raise IntegrityConflict("model reservation is absent or outside this budget scope")
            # Follow reservation lock order; recheck the immutable locator under the final lock.
            cursor.execute(f"SELECT policy_hash,scope_key FROM {self._locked('itops_runtime_workflow')} "
                           "WHERE workflow_id=?", (keys[0],))
            policy = cursor.fetchone()
            if policy is None or policy[0] != self.limits.sha256 or policy[1] != self.scope:
                raise IntegrityConflict("reservation pricing policy cannot be changed at settlement")
            cursor.execute(f"SELECT scope_key FROM {self._locked('itops_runtime_month')} "
                           "WHERE scope_key=? AND month_key=?", (keys[1], keys[2]))
            if cursor.fetchone() is None:
                raise IntegrityConflict("reservation accounting period is missing")
            cursor.execute(
                f"SELECT workflow_id,scope_key,month_key,state,reserved_tokens,reserved_nanos,"
                f"input_tokens,output_tokens FROM {self._locked('itops_runtime_call')} WHERE invocation_id=?",
                (_key(invocation_id),))
            row = cursor.fetchone()
            if row is None or tuple(row[:3]) != tuple(keys):
                raise IntegrityConflict("model reservation is absent or outside this budget scope")
            if row[3] != "reserved":
                if (row[6], row[7]) != (input_tokens, output_tokens):
                    raise IntegrityConflict("settled usage cannot be rewritten")
                return row[3]
            if input_tokens is None:
                cursor.execute("UPDATE itops_runtime_call SET state='unknown' WHERE invocation_id=?",
                               (invocation_id,))
                return "unknown"
            total = input_tokens + output_tokens
            charged = (input_tokens * self.limits.input_nanodollars_per_token
                       + output_tokens * self.limits.output_nanodollars_per_token)
            state = "overrun" if (input_tokens > self.limits.model_context_tokens
                                  or output_tokens > self.limits.max_output_tokens) else "settled"
            cursor.execute("UPDATE itops_runtime_call SET state=?,input_tokens=?,output_tokens=?,"
                           "charged_nanos=? WHERE invocation_id=?", (state, input_tokens, output_tokens, charged,
                                                                   invocation_id))
            cursor.execute("UPDATE itops_runtime_month SET accounted_nanos=accounted_nanos+? "
                           "WHERE scope_key=? AND month_key=?", (charged - row[5], row[1], row[2]))
            cursor.execute("UPDATE itops_runtime_workflow SET tokens_accounted=tokens_accounted+? "
                           "WHERE workflow_id=?", (total - row[4], row[0]))
            if state == "overrun":
                cursor.execute("UPDATE itops_runtime_workflow SET stop_reason=? WHERE workflow_id=?",
                               ("provider_usage_exceeds_proven_bound", row[0]))
            return state

    def record_tool(self, workflow_id: str, call_id: str, name: str) -> None:
        _key(call_id), _key(name)
        with self._transaction() as cursor:
            row = self._workflow(cursor, workflow_id, self._now(cursor))
            if self.limits.caps_enforced and row[4] >= self.limits.tool_calls:
                raise BudgetExceeded("workflow tool-call budget exhausted")
            cursor.execute(f"SELECT call_id FROM {self._locked('itops_runtime_tool')} "
                           "WHERE workflow_id=? AND call_id=?", (workflow_id, call_id))
            if cursor.fetchone():
                raise IntegrityConflict("a tool call cannot be dispatched twice")
            cursor.execute("INSERT INTO itops_runtime_tool VALUES (?,?,?)", (workflow_id, call_id, name))
            cursor.execute("UPDATE itops_runtime_workflow SET tool_calls=tool_calls+1 WHERE workflow_id=?",
                           (workflow_id,))
