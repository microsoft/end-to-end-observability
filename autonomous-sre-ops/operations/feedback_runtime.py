"""Private one-minute worker; no HTTP listener or browser write surface."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from operations import agent_case as cases
from operations import context as deployment
from operations.feedback import (
    ACTIVE, Bridge, Conflict, FeedbackError, RejectedDelivery, UnknownDelivery, uuid,
)
from operations.servicenow import CaseClient, ClientCredentials, ServiceNowError

LOG = logging.getLogger("asre-feedback")
CTX = deployment.load()
AGENT_ID = CTX.agent.resource_id
# The agent endpoint is resolved through ARM and checked against the deployment's own agent name and region.
_ENDPOINT_HOST = re.compile(
    re.escape(CTX.agent.name) + r"--[a-z0-9]+\.[a-z0-9]+\." + re.escape(CTX.location) + r"\.azuresre\.ai"
)
SCOPE = "https://azuresre.dev/.default"
LIMIT = 4_000_000


class Native:
    def __init__(self, credential, opener=None):
        self.credential = credential
        self.opener = opener or build_opener(cases.NoRedirect())
        self.endpoint = ""

    def _get(self, url, scope):
        request = Request(url, headers={
            "Authorization": "Bearer " + self.credential.get_token(scope).token,
            "Accept": "application/json",
        })
        try:
            with self.opener.open(request, timeout=20) as response:
                raw = response.read(LIMIT + 1)
                if response.status != 200 or len(raw) > LIMIT:
                    raise FeedbackError("The fixed native read returned an invalid bounded response.")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise FeedbackError("The fixed native response must be an object.")
                return value
        except HTTPError as error:
            status = error.code
            error.close()
            raise FeedbackError("The fixed native read failed (HTTP " + str(status) + ").") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise FeedbackError("The fixed native read did not complete or validate.") from None

    def resolve(self):
        agent = self._get("https://management.azure.com" + AGENT_ID + "?api-version=2026-01-01",
                          "https://management.azure.com/.default")
        properties = agent.get("properties")
        if not isinstance(properties, dict) or not isinstance(properties.get("actionConfiguration"), dict):
            raise FeedbackError("The native agent action configuration is incomplete.")
        action = properties["actionConfiguration"]
        endpoint = properties.get("agentEndpoint")
        if (
            str(agent.get("id", "")).lower() != AGENT_ID.lower()
            or properties.get("powerState") != "Running" or properties.get("provisioningState") != "Succeeded"
            or action.get("mode") != "Review" or action.get("accessLevel") != "Low"
            or not isinstance(endpoint, str)
        ):
            raise FeedbackError("The fixed native agent is not ready in Review/Low mode.")
        try:
            parsed = urlsplit(endpoint)
        except ValueError:
            raise FeedbackError("The native agent endpoint is invalid.") from None
        if (
            parsed.scheme != "https" or not parsed.hostname
            or not _ENDPOINT_HOST.fullmatch(parsed.hostname)
            or parsed.netloc != parsed.hostname or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment
            or any(ord(char) < 33 for char in endpoint)
        ):
            raise FeedbackError("The ARM-resolved native endpoint is outside the approved agent/region.")
        self.endpoint = endpoint.rstrip("/")

    def _url(self, thread, suffix=""):
        if not self.endpoint:
            raise FeedbackError("Resolve the approved native agent before using its data plane.")
        return self.endpoint + "/api/v1/threads/" + uuid(thread) + suffix

    def thread(self, thread):
        value = self._get(self._url(thread), SCOPE)
        if not isinstance(value, dict) or value.get("id") != thread:
            raise FeedbackError("The native thread readback does not match its original ID.")
        return value

    def messages(self, thread):
        value = self._get(self._url(thread, "/messages"), SCOPE)
        if (
            not isinstance(value, dict) or not isinstance(value.get("value"), list)
            or not isinstance(value.get("state"), str)
            or any(not isinstance(message, dict) for message in value["value"])
            or value.get("nextLink") or value.get("nextCursor") or value.get("hasMore") is True
            or value.get("error")
        ):
            raise FeedbackError("The bounded native message readback is incomplete.")
        return value

    def prepare(self, thread, text):
        # Text-only POST was qualified against the actual native API on 2026-10-22.
        body = json.dumps({"text": text}, ensure_ascii=False, allow_nan=False).encode()
        if not isinstance(text, str) or len(body) > 64000:
            raise FeedbackError("The native continuation exceeds its bounded text contract.")
        request = Request(self._url(thread, "/messages"), data=body, method="POST", headers={
            "Authorization": "Bearer " + self.credential.get_token(SCOPE).token,
            "Content-Type": "application/json", "Accept": "application/json",
        })

        def submit():
            try:
                with self.opener.open(request, timeout=20) as response:
                    if response.status not in {200, 201, 202}:
                        raise UnknownDelivery("The native submission needs readback; do not resend.")
            except HTTPError as error:
                status = error.code
                error.close()
                if status in {400, 401, 403, 404, 405, 413, 415, 422}:
                    raise RejectedDelivery("Native continuation rejected (HTTP " + str(status) + ").") from None
                raise UnknownDelivery("The native submission needs readback; do not resend.") from None
            except (URLError, TimeoutError, OSError):
                raise UnknownDelivery("The native submission needs readback; do not resend.") from None

        return submit


class TableStore:
    def __init__(self, table):
        self.table = table

    @staticmethod
    def partition(episode):
        return "servicenow-feedback-v1:" + (episode if episode == "runtime" else uuid(episode))

    @staticmethod
    def row(entity):
        return {**dict(entity), "_etag": entity.metadata["etag"]}

    def get(self, episode, key):
        from azure.core.exceptions import ResourceNotFoundError
        try:
            return self.row(self.table.get_entity(self.partition(episode), key))
        except ResourceNotFoundError:
            return None

    def create(self, episode, key, value):
        from azure.core.exceptions import ResourceExistsError
        try:
            self.table.create_entity({"PartitionKey": self.partition(episode), "RowKey": key, **value})
        except ResourceExistsError:
            pass
        return self.get(episode, key)

    def change(self, episode, key, previous, values):
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceModifiedError
        from azure.data.tables import UpdateMode
        try:
            self.table.update_entity(
                {"PartitionKey": self.partition(episode), "RowKey": key, **values},
                mode=UpdateMode.MERGE, etag=previous["_etag"], match_condition=MatchConditions.IfNotModified,
            )
        except ResourceModifiedError:
            raise Conflict("Another feedback worker advanced this durable event.") from None
        return self.get(episode, key)

    def pending(self, episode):
        rows = self.table.query_entities(
            "PartitionKey eq @partition and (status eq 'prepared' or status eq 'submitting' or status eq 'native_received')",
            parameters={"partition": self.partition(episode)})
        result = []
        for row in rows:
            if row.get("status") in ACTIVE:
                result.append(self.row(row))
            if len(result) > 2000:
                raise FeedbackError("The pending feedback ledger exceeds its bounded scan.")
        return result

    def transition(self, episode, previous, current, event):
        from azure.core import MatchConditions
        from azure.data.tables import TableTransactionError, UpdateMode
        partition = self.partition(episode)
        try:
            self.table.submit_transaction([
                ("update", {"PartitionKey": partition, "RowKey": "_state", **current},
                 {"mode": UpdateMode.MERGE, "etag": previous["_etag"],
                  "match_condition": MatchConditions.IfNotModified}),
                ("create", {"PartitionKey": partition, "RowKey": event["event_id"], **event}),
            ])
        except TableTransactionError as error:
            if error.status_code not in {409, 412}:
                raise
            raise Conflict("Another worker committed the case snapshot/feedback event.") from None


def run():
    from azure.core.exceptions import AzureError
    from azure.data.tables import TableClient
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient

    if os.environ.get("ASRE_EXECUTION_MODE") != "Review" or os.environ.get("ASRE_SERVICENOW_FEEDBACK_ENABLED") != "true":
        raise PermissionError("The dedicated feedback worker requires explicit enabled Review configuration.")
    credential = ManagedIdentityCredential(
        client_id=uuid(os.environ["ASRE_RUNNER_CLIENT_ID"]), retry_total=0,
        connection_timeout=5, read_timeout=10,
    )
    native = Native(credential)
    native.resolve()
    table = TableStore(TableClient(
        CTX.storage.table_endpoint, "Episodes",
        credential=credential, retry_total=0,
    ))
    client = CaseClient(ClientCredentials(
        lambda: cases.exact_secret(credential), client_id=cases.FACADE_CLIENT,
    ).token)
    client.health()
    blobs = BlobServiceClient(cases.STORAGE, credential=credential, retry_total=0).get_container_client("evidence")
    names, scanned = [], 0
    for blob in blobs.list_blobs(name_starts_with=cases.PREFIX):
        scanned += 1
        if re.fullmatch(re.escape(cases.PREFIX) + r"[0-9a-f-]{36}/initial\.json", blob.name):
            names.append(blob.name)
        if len(names) > 2000 or scanned > 10000:
            raise FeedbackError("The owned-case inventory exceeds its bounded scan.")
    names.sort()
    cursor = table.get("runtime", "_cursor")
    if cursor:
        names = [name for name in names if name > cursor["last_intent"]] + [
            name for name in names if name <= cursor["last_intent"]]
    bridge, intents = Bridge(client, native, table), cases.BlobIntents(credential)
    deadline, checked, failed = time.monotonic() + 40, 0, 0
    for name in names:
        if time.monotonic() >= deadline:
            break
        checked += 1
        try:
            binding = bridge.bind(cases.decode(intents.read(name)))
            counts = bridge.collect(binding)
            status = bridge.deliver(binding)
            LOG.info("episode=%s thread=%s status=%s events=%s blocked=%s",
                     binding.episode_id, binding.thread_id, status, counts["source_events"],
                     counts["blocked_or_ignored"])
            if counts["blocked"]:
                failed += 1
                LOG.error("Sensitive or oversized feedback withheld; inspect the owned ticket. episode=%s",
                          binding.episode_id)
            if status in {"delivery_unknown_no_resend", "native_received_ack_unknown"}:
                failed += 1
                LOG.error("Feedback needs reconciliation; no blind resend. episode=%s status=%s",
                          binding.episode_id, status)
        except Conflict:
            LOG.info("Feedback deferred because another worker advanced the same durable row.")
        except (FeedbackError, cases.CaseToolError, ServiceNowError, AzureError, ValueError, KeyError, TypeError) as error:
            failed += 1
            LOG.error("Feedback case failed closed. intent=%s error_type=%s", name, type(error).__name__)
        cursor = table.get("runtime", "_cursor")
        if cursor:
            try:
                table.change("runtime", "_cursor", cursor, {"last_intent": name})
            except Conflict:
                LOG.info("Another worker advanced the round-robin cursor.")
        else:
            table.create("runtime", "_cursor", {"last_intent": name})
    print(json.dumps({"mode": "Review", "feedback": "failed" if failed else "polled",
                      "checked": checked, "inventory": len(names), "failed": failed,
                      "remaining": len(names) - checked, "agent_answer_verified": False}))
    if failed:
        raise FeedbackError("One or more feedback deliveries require attention; inspect the private durable ledger.")
