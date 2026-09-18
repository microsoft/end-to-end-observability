"""Optional Azure queue control and Azure Monitor publishing adapters."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
from typing import Any, Protocol
from urllib.parse import urlparse


class MonitorError(RuntimeError):
    """Raised when an operational Azure integration cannot proceed."""


class CommandSourceError(MonitorError):
    """Raised when the Azure Storage Queue command source fails."""


class MonitorPublisherError(MonitorError):
    """Raised when Azure Monitor Logs Ingestion fails."""


@dataclass(frozen=True)
class ReceivedQueueMessage:
    """A queue message body plus the provider receipt needed for deletion."""

    body: str
    receipt: Any


class CommandSource(Protocol):
    """Receive operational commands without acknowledging them."""

    def receive(self) -> list[ReceivedQueueMessage]:
        """Return currently available messages."""

    def delete(self, message: ReceivedQueueMessage) -> None:
        """Acknowledge a message after durable local persistence."""


class MonitorPublisher(Protocol):
    """Publish operational events to an external monitor."""

    def publish(self, events: list[dict[str, Any]]) -> None:
        """Publish a non-empty event collection."""


def _credential(auth_mode: str, managed_identity_client_id: str | None) -> Any:
    if auth_mode not in {"default", "managed-identity"}:
        raise MonitorError("Azure auth mode must be default or managed-identity")
    if managed_identity_client_id and auth_mode != "managed-identity":
        raise MonitorError(
            "managed identity client ID requires managed-identity auth mode"
        )
    try:
        from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
    except ImportError as exc:
        raise MonitorError(
            "Azure queue and monitor support requires azure-identity"
        ) from exc
    return (
        DefaultAzureCredential()
        if auth_mode == "default"
        else ManagedIdentityCredential(client_id=managed_identity_client_id)
    )


def _https_endpoint(value: str, label: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise MonitorError(f"{label} must be an absolute HTTPS endpoint")
    return f"{parsed.scheme}://{parsed.netloc}"


class AzureStorageQueueCommandSource:
    """Receive storm commands from Azure Storage Queue."""

    def __init__(
        self,
        account_url: str,
        queue_name: str,
        auth_mode: str = "default",
        managed_identity_client_id: str | None = None,
        visibility_timeout: int = 60,
        max_messages: int = 32,
        queue_client: Any | None = None,
    ) -> None:
        self.account_url = _https_endpoint(account_url, "queue account URL")
        if not queue_name or "/" in queue_name or "\\" in queue_name:
            raise CommandSourceError(
                "queue name must be a non-empty Azure Storage queue name"
            )
        if visibility_timeout < 1:
            raise CommandSourceError("queue visibility timeout must be positive")
        if not 1 <= max_messages <= 32:
            raise CommandSourceError("queue max messages must be between 1 and 32")
        self.queue_name = queue_name
        self.visibility_timeout = visibility_timeout
        self.max_messages = max_messages
        if queue_client is None:
            try:
                from azure.storage.queue import QueueClient
            except ImportError as exc:
                raise CommandSourceError(
                    "Azure queue support requires azure-storage-queue"
                ) from exc
            try:
                queue_client = QueueClient(
                    account_url=self.account_url,
                    queue_name=queue_name,
                    credential=_credential(
                        auth_mode,
                        managed_identity_client_id,
                    ),
                )
            except MonitorError:
                raise
            except Exception as exc:
                raise CommandSourceError(
                    f"unable to create Azure queue client: {exc}"
                ) from exc
        self._queue_client = queue_client

    def receive(self) -> list[ReceivedQueueMessage]:
        try:
            messages = self._queue_client.receive_messages(
                messages_per_page=self.max_messages,
                visibility_timeout=self.visibility_timeout,
            )
            return [
                ReceivedQueueMessage(str(message.content), message)
                for message in islice(messages, self.max_messages)
            ]
        except Exception as exc:
            raise CommandSourceError(
                f"unable to receive Azure queue commands: {exc}"
            ) from exc

    def delete(self, message: ReceivedQueueMessage) -> None:
        try:
            self._queue_client.delete_message(message.receipt)
        except Exception as exc:
            raise CommandSourceError(
                f"unable to delete persisted Azure queue command: {exc}"
            ) from exc


class AzureLogsIngestionPublisher:
    """Publish durable outbox events through Azure Monitor Logs Ingestion."""

    def __init__(
        self,
        endpoint: str,
        rule_id: str,
        stream_name: str,
        auth_mode: str = "default",
        managed_identity_client_id: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.endpoint = _https_endpoint(endpoint, "monitor DCR endpoint")
        if not rule_id:
            raise MonitorPublisherError(
                "monitor DCR immutable rule ID must not be empty"
            )
        if not stream_name:
            raise MonitorPublisherError("monitor stream name must not be empty")
        self.rule_id = rule_id
        self.stream_name = stream_name
        if client is None:
            try:
                from azure.monitor.ingestion import LogsIngestionClient
            except ImportError as exc:
                raise MonitorPublisherError(
                    "Azure Monitor support requires azure-monitor-ingestion"
                ) from exc
            try:
                client = LogsIngestionClient(
                    endpoint=self.endpoint,
                    credential=_credential(
                        auth_mode,
                        managed_identity_client_id,
                    ),
                )
            except MonitorError:
                raise
            except Exception as exc:
                raise MonitorPublisherError(
                    f"unable to create Logs Ingestion client: {exc}"
                ) from exc
        self._client = client

    def publish(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        try:
            self._client.upload(
                rule_id=self.rule_id,
                stream_name=self.stream_name,
                logs=events,
            )
        except Exception as exc:
            raise MonitorPublisherError(
                f"unable to upload Azure Monitor events: {exc}"
            ) from exc
