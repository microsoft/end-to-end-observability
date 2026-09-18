"""Authenticated command publication for the synthetic exercise dashboard."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


class DashboardControlError(RuntimeError):
    """Raised when an operator command cannot be published safely."""


class StormCommandSender(Protocol):
    def send_storm_start(self) -> dict[str, Any]:
        """Publish one validated synthetic storm-start command."""

    def send_storm_clear(self) -> dict[str, Any]:
        """Publish one synthetic storm-clear command."""


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_storm_template(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DashboardControlError(
            f"unable to load the synthetic storm template: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise DashboardControlError("synthetic storm template must be an object")
    if value.get("status") != "Exercise":
        raise DashboardControlError("storm template status must be Exercise")
    if value.get("is_synthetic") is not True:
        raise DashboardControlError("storm template must be synthetic")
    if value.get("public_safety") is not False:
        raise DashboardControlError("storm template public_safety must be false")
    if not isinstance(value.get("storm_id"), str):
        raise DashboardControlError("storm template must contain a storm_id")
    return value


class AzureQueueStormCommandSender:
    """Send fixed, versioned storm commands to Azure Queue Storage."""

    def __init__(
        self,
        account_url: str,
        queue_name: str,
        template_path: str | Path,
        credential: Any,
        queue_client: Any | None = None,
    ) -> None:
        if queue_client is None:
            try:
                from azure.storage.queue import QueueClient
            except ImportError as exc:
                raise DashboardControlError(
                    "Azure Queue support requires the dashboard Azure dependencies"
                ) from exc
            queue_client = QueueClient(
                account_url=account_url,
                queue_name=queue_name,
                credential=credential,
            )
        self.queue_client = queue_client
        self.template_path = Path(template_path)

    def send_storm_start(self) -> dict[str, Any]:
        return self._send_command(
            "storm_upsert",
            _load_storm_template(self.template_path),
        )

    def send_storm_clear(self) -> dict[str, Any]:
        return self._send_command("storm_clear", {})

    def _send_command(
        self,
        command_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        command = {
            "schema_version": "1.0",
            "command_id": f"command-{uuid.uuid4().hex}",
            "command_type": command_type,
            "requested_at_utc": _timestamp(datetime.now(timezone.utc)),
            "payload": payload,
        }
        try:
            self.queue_client.send_message(
                json.dumps(
                    command,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        except Exception as exc:
            raise DashboardControlError(
                f"unable to queue the storm command: {exc}"
            ) from exc
        return command
