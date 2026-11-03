"""Pinned, schema-validated ServiceNow facade transport; no native Table API."""

from __future__ import annotations

import calendar
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

ORIGIN = "https://example-instance.service-now.com"
BASE = "/api/x_0000000_asre/case_adapter/v1"
CLIENT_ID = "0000000000000000000000000000003a"
SCOPE = "asre_case_adapter_v1"
GROUP = "0000000000000000000000000000007a"
CIS = {
    "linux": "0000000000000000000000000000014a",
    "patch": "0000000000000000000000000000001a",
    "backup": "0000000000000000000000000000012a",
}
# Keep the transport independently packageable; do not import the repair ledger
# merely to validate its three fixed facade resources.
TARGETS = {
    f"/subscriptions/00000001-000a-4000-a000-000000000001/resourcegroups/rg-asre-{role}-cus/providers/microsoft.compute/virtualmachines/vm-asre-{role}-cus": role
    for role in CIS
}
SCHEMAS = (
    "servicenow-case", "servicenow-case-write", "servicenow-note-write",
    "servicenow-feedback-ack", "servicenow-action-audit", "servicenow-adapter-response",
)
SENSITIVE = re.compile(
    r"[?&](?:sig|access_token|token|client_secret|password|api_key)="
    r"|authorization\s*:\s*(?:basic|bearer)\s+\S+"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----", re.I,
)


# BEGIN Python 3 adaptation of rfc3339-validator 0.1.4 by Nicolas Aimetti.
# Upstream: https://github.com/naimetti/rfc3339-validator, rfc3339_validator.py
# Source: installed .venv/Lib/site-packages/rfc3339_validator.py
# SHA256: c220cb9495da5215a53ab173f695060294df2d1042a9cbaaf0cc622bcba09d6e
# License: rfc3339_validator-0.1.4.dist-info/LICENSE (full notice below).
# Changes: re.ASCII replaces six.PY3; the regex is reformatted and fullmatch
# replaces ^/$ matching to reject trailing newlines. Uppercasing and non-string
# pass-through retain jsonschema 4.25.1's date-time format semantics.
# Calendar/leap-year checks and rejection of leap seconds are retained;
# this is not an unrestricted RFC3339 implementation.
#
# MIT License
#
# Copyright (c) 2019, Nicolas Aimetti
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
_DATE_TIME = re.compile(
    r"(\d{4})-(0[1-9]|1[0-2])-(\d{2})T"
    r"(?:[01]\d|2[0123]):[0-5]\d:[0-5]\d(?:\.\d+)?"
    r"(?:Z|[+-](?:[01]\d|2[0123]):[0-5]\d)", re.ASCII,
)


def _is_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return True
    match = _DATE_TIME.fullmatch(value.upper())
    if match is None:
        return False
    year, month, day = map(int, match.groups())
    return bool(year) and 1 <= day <= calendar.monthrange(year, month)[1]
# END rfc3339-validator adaptation.


class ServiceNowError(RuntimeError):
    pass


def canonical_uuid(value: str) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("A canonical UUID is required.")
    return value


def normalized_target(value: str) -> str:
    if not isinstance(value, str) or value.lower() not in TARGETS:
        raise PermissionError("The resource is outside the exact demonstration target allowlist.")
    return value.lower()


class AmbiguousWriteError(ServiceNowError):
    def __init__(self, episode_id: str, write_request_id: str):
        self.episode_id = episode_id
        self.write_request_id = write_request_id
        super().__init__("ServiceNow write outcome is unknown; reconcile the original episode/write ID.")


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


class Transport(Protocol):
    def __call__(self, method: str, path: str, body: bytes | None, headers: dict[str, str], *, timeout: float = 45) -> HttpResponse: ...


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


MIN_SUBMISSION_TIMEOUT = 0.001  # urllib accepts fractional seconds; never round up.


def https_transport(method: str, path: str, body: bytes | None, headers: dict[str, str], *, timeout: float = 45,
                    before_submit: Callable[[], float] | None = None) -> HttpResponse:
    if not (path == "/oauth_token.do" or path.startswith(BASE + "/")):
        raise PermissionError("Only the pinned OAuth endpoint and owned-case facade are admitted.")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not MIN_SUBMISSION_TIMEOUT <= timeout <= 45:
        raise ServiceNowError("The pinned HTTPS timeout is outside its supported bound.")
    request = Request(ORIGIN + path, data=body, headers=headers, method=method)
    opener = build_opener(NoRedirect())
    if before_submit is not None:
        remaining = before_submit()
        if type(remaining) not in (int, float) or not math.isfinite(remaining) or remaining < MIN_SUBMISSION_TIMEOUT:
            raise PermissionError("Finite submission authority expired before HTTPS dispatch.")
        timeout = min(timeout, remaining)
    try:
        response = opener.open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    except (URLError, OSError, TimeoutError) as error:
        raise ServiceNowError("The pinned ServiceNow HTTPS request did not complete.") from error
    with response:
        content = response.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ServiceNowError("The ServiceNow response exceeded its bounded transport limit.")
        return HttpResponse(response.status, content)


def parse_json(response: HttpResponse) -> dict:
    try:
        value = json.loads(response.body)
    except (ValueError, UnicodeDecodeError):
        raise ServiceNowError("ServiceNow returned an invalid JSON response.") from None
    if not isinstance(value, dict):
        raise ServiceNowError("ServiceNow returned an unexpected response envelope.")
    return value


class Contracts:
    def __init__(self):
        formats = FormatChecker()
        formats.checks("date-time")(_is_datetime)
        contents = [
            json.loads((Path(__file__).parent / "contracts" / (name + ".schema.json")).read_text(encoding="utf-8"))
            for name in SCHEMAS
        ]
        registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in contents)
        self.validators = {
            s["$id"]: Draft202012Validator(s, registry=registry, format_checker=formats)
            for s in contents
        }

    def validate(self, name: str, value: dict, *, definition: str | None = None) -> None:
        try:
            identifier = "urn:asre-demo:" + name + ":1"
            validator = self.validators[identifier]
            if definition is not None:
                validator = validator.evolve(schema={"$ref": identifier + "#/$defs/" + definition})
            validator.validate(value)
        except ValidationError:
            raise ServiceNowError("ServiceNow transport schema violation: " + name + ".") from None


class ClientCredentials:
    def __init__(self, secret: Callable[[], str], transport: Transport = https_transport, clock: Callable[[], float] = time.monotonic, *, client_id: str = CLIENT_ID):
        if not isinstance(client_id, str) or not re.fullmatch(r"[0-9a-f]{32}", client_id):
            raise ServiceNowError("The dedicated ServiceNow client ID must be 32 lowercase hexadecimal characters.")
        self._client_id = client_id
        self._secret = secret
        self._transport = transport
        self._clock = clock
        self._token = ""
        self._expires = 0.0

    def token(self) -> str:
        if self._clock() < self._expires:
            return self._token
        secret = self._secret()
        if not isinstance(secret, str) or not secret:
            raise ServiceNowError("The dedicated ServiceNow credential reference is empty or unbound.")
        started = self._clock()
        response = self._transport("POST", "/oauth_token.do", urlencode({
            "grant_type": "client_credentials", "client_id": self._client_id,
            "client_secret": secret, "scope": SCOPE,
        }).encode(), {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        value = parse_json(response)
        expiry = value.get("expires_in")
        token = value.get("access_token")
        if (
            response.status != 200 or not isinstance(value.get("token_type"), str) or value["token_type"].lower() != "bearer"
            or not isinstance(token, str) or not token or any(c.isspace() for c in token)
            or not isinstance(value.get("scope"), str) or SCOPE not in value["scope"].split()
            or isinstance(expiry, bool) or not isinstance(expiry, int) or not 60 < expiry <= 1800
        ):
            raise ServiceNowError("The dedicated ServiceNow OAuth grant or assigned scope was not accepted.")
        self._token, self._expires = token, started + expiry - 60
        return token


@dataclass(frozen=True)
class OwnedCase:
    episode_id: str
    resource_id: str
    reference: dict
    state: str


class CaseClient:
    def __init__(self, token: Callable[[], str], transport: Transport = https_transport):
        self._token = token
        self._transport = transport
        self.contracts = Contracts()

    def _request(self, method: str, suffix: str, payload: dict | None = None, *, episode: str = "", write_id: str = "",
                 before_submit: Callable[[], float] | None = None) -> dict:
        token = self._token()
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token):
            raise ServiceNowError("The pinned facade requires an opaque valid bearer token.")
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        body = None
        if payload is not None:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            if len(encoded) > 48000 or SENSITIVE.search(encoded):
                raise ServiceNowError("Case content exceeds its bound or contains suspected credentials.")
            body = encoded.encode()
            headers["Content-Type"] = "application/json"
        # Outside finite commissioning retain four-argument transports unchanged.
        # The finite callback runs after authentication and serialization, outside
        # ambiguous-outcome handling: rejection here has submitted nothing.
        options = {}
        if before_submit is not None:
            if method != "POST" or payload is None:
                raise ServiceNowError("A submission boundary requires a business write.")
            remaining = before_submit()
            if type(remaining) not in (int, float) or not math.isfinite(remaining) or remaining < MIN_SUBMISSION_TIMEOUT:
                raise ServiceNowError("Finite submission authority has insufficient remaining time.")
            options["timeout"] = min(45.0, remaining)
            if self._transport is https_transport:
                options["before_submit"] = before_submit
        try:
            response = self._transport(method, BASE + suffix, body, headers, **options)
            if response.status in {401, 403}:
                raise PermissionError("ServiceNow rejected the authenticated facade caller (HTTP " + str(response.status) + ").")
            value = parse_json(response)
            self.contracts.validate("servicenow-adapter-response", value)
            result = value["result"]
            if (
                method == "GET" and response.status == 404 and episode and write_id
                and suffix == "/cases/" + episode + "/writes/" + write_id
                and result.get("error", {}).get("code") == "write_not_found"
                and result["error"]["write_request_id"] is None
            ):
                # AsreAdapter._reconcile returns this exact authenticated error
                # before any episode lookup when no original write is reserved.
                # Normalize only that server verdict, never an arbitrary 404,
                # into the existing write contract. A retry still uses the same
                # immutable ID/payload and the server's fingerprint reservation.
                missing = {
                    "outcome": "not_applied", "episode_id": episode,
                    "write_request_id": write_id, "error_code": "write_not_found",
                }
                self.contracts.validate("servicenow-adapter-response", missing, definition="write")
                return missing
            if 200 <= response.status < 300 and "error" not in result:
                kind = (
                    "write" if method == "POST" or "/writes/" in suffix else
                    "health" if suffix == "/health" else
                    "updates" if "/updates" in suffix else "context"
                )
                self.contracts.validate("servicenow-adapter-response", result, definition=kind)
                if kind == "write" and (
                    result["episode_id"] != episode
                    or result["write_request_id"] != (write_id or payload["write_request_id"])
                ):
                    raise ServiceNowError("The write receipt does not match its original episode/write ID.")
                if kind == "write" and result["outcome"] in {"applied", "already_applied"} and response.status != 200:
                    raise ServiceNowError("An applied write requires a successful terminal response.")
        except ServiceNowError as error:
            if method == "POST" and payload is not None:
                raise AmbiguousWriteError(episode, payload["write_request_id"]) from error
            raise
        if response.status >= 500 and method == "POST":
            raise AmbiguousWriteError(episode, payload["write_request_id"])
        if not 200 <= response.status < 300 or "error" in result:
            raise ServiceNowError("ServiceNow rejected the facade operation (HTTP " + str(response.status) + ").")
        return result

    def health(self) -> dict:
        result = self._request("GET", "/health")
        if result.get("version") != "0.1.0" or result.get("execution_mode") != "Review":
            raise ServiceNowError("The actual adapter version or execution mode differs from the accepted binding.")
        return result

    def _context(self, value: dict, episode: str, resource: str, reference: dict | None) -> OwnedCase:
        target = normalized_target(resource)
        evidence = value.get("evidence", {})
        if (
            value.get("episode_id") != episode or normalized_target(value.get("resource_id")) != target
            or value.get("assignment_group", {}).get("sys_id") != GROUP
            or value.get("ci", {}).get("sys_id") != CIS[TARGETS[target]]
            or reference is not None and value.get("case_reference") != reference
            or value.get("case_reference", {}).get("record_type") != "incident"
            or not re.fullmatch(r"INC[0-9]+", value.get("case_reference", {}).get("number", ""))
            or value.get("instance_url") != ORIGIN + "/incident.do?sys_id=" + value.get("case_reference", {}).get("sys_id", "")
            or evidence.get("episode_id") != episode or evidence.get("resource_id", "").lower() != target
            or evidence.get("mode") != value.get("mode") or evidence.get("signal_family") != value.get("signal_family")
        ):
            raise ServiceNowError("The response does not match the immutable owned case/group/CI/resource.")
        return OwnedCase(episode, target, dict(value["case_reference"]), value["state"]["value"])

    def read_case(self, episode: str, resource: str, reference: dict | None = None) -> OwnedCase:
        canonical_uuid(episode)
        normalized_target(resource)
        result = self._request("GET", "/cases/" + episode)
        return self._context(result, episode, resource, reference)

    def create_case(self, payload: dict, *, before_submit: Callable[[], float] | None = None) -> dict:
        self.contracts.validate("servicenow-case-write", payload)
        canonical_uuid(payload["write_request_id"])
        episode = canonical_uuid(payload["evidence"]["episode_id"])
        result = self._request("POST", "/cases", payload, episode=episode, before_submit=before_submit)
        if result["outcome"] in {"applied", "already_applied"}:
            try:
                self.read_case(episode, payload["evidence"]["resource_id"], result["case_reference"])
            except (ServiceNowError, PermissionError):
                raise AmbiguousWriteError(episode, payload["write_request_id"]) from None
        return result

    def append_note(self, case: OwnedCase, payload: dict, *, before_submit: Callable[[], float] | None = None) -> dict:
        self.contracts.validate("servicenow-note-write", payload)
        canonical_uuid(payload["write_request_id"])
        result = self._request("POST", "/cases/" + canonical_uuid(case.episode_id) + "/notes", payload,
                               episode=case.episode_id, before_submit=before_submit)
        if result["outcome"] in {"applied", "already_applied"}:
            if result.get("case_reference") != case.reference or not re.fullmatch(r"[0-9a-f]{32}", result.get("journal_sys_id", "")):
                raise AmbiguousWriteError(case.episode_id, payload["write_request_id"])
        return result

    def reconcile(self, episode: str, write_id: str) -> dict:
        canonical_uuid(episode)
        canonical_uuid(write_id)
        return self._request("GET", "/cases/" + episode + "/writes/" + write_id, episode=episode, write_id=write_id)

    def acknowledge(self, case: OwnedCase, payload: dict) -> dict:
        self.contracts.validate("servicenow-feedback-ack", payload)
        canonical_uuid(payload["write_request_id"])
        result = self._request("POST", "/cases/" + canonical_uuid(case.episode_id) + "/updates/ack", payload, episode=case.episode_id)
        if result["outcome"] in {"applied", "already_applied"} and (
            result.get("case_reference") != case.reference or result.get("event_id") != payload["event_id"]
            or result.get("delivery") != "received_not_answered"
        ):
            raise AmbiguousWriteError(case.episode_id, payload["write_request_id"])
        return result

    def audit(self, case: OwnedCase, payload: dict) -> dict:
        self.contracts.validate("servicenow-action-audit", payload)
        canonical_uuid(payload["write_request_id"])
        canonical_uuid(payload["action_request_id"])
        result = self._request("POST", "/cases/" + canonical_uuid(case.episode_id) + "/audit", payload, episode=case.episode_id)
        if result["outcome"] in {"applied", "already_applied"}:
            audit = result.get("audit_receipt", {})
            if (
                result.get("case_reference") != case.reference or audit.get("case_reference") != case.reference
                or audit.get("resource_id", "").lower() != case.resource_id
                or audit.get("episode_id") != case.episode_id or audit.get("action_request_id") != payload["action_request_id"]
                or audit.get("action_name") != payload["action_name"]
                or audit.get("proposal_fingerprint") != payload["proposal_fingerprint"]
                or audit.get("journal_sys_id") != result.get("journal_sys_id")
                or audit.get("execution_authorized") is not False
            ):
                raise AmbiguousWriteError(case.episode_id, payload["write_request_id"])
        return result

    def feedback_pages(self, case: OwnedCase):
        cursor = None
        seen = set()
        for _ in range(100):
            suffix = "/cases/" + canonical_uuid(case.episode_id) + "/updates"
            if cursor:
                suffix += "?" + urlencode({"cursor": cursor})
            value = self._request("GET", suffix)
            self._context(value["context"], case.episode_id, case.resource_id, case.reference)
            if any(event["actor"] == "copilot.integration" for event in value["events"]):
                raise ServiceNowError("The adapter returned an integration echo as external feedback.")
            yield value
            if not value["has_more"]:
                return
            cursor = value["next_cursor"]
            if not isinstance(cursor, str) or not re.fullmatch(r"[0-9a-f]{32}", cursor) or cursor in seen:
                raise ServiceNowError("Feedback pagination cannot advance safely.")
            seen.add(cursor)
        raise ServiceNowError("Feedback pagination exceeded its bounded cycle; persist continuation before retrying.")
