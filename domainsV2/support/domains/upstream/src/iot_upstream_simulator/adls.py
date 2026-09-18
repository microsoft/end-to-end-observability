"""ADLS monitoring-log sink for fictional upstream simulator outputs.

The sink is monitoring-only. The private `ground_truth` family, continuous
`telemetry`, and the structural catalog can never be published through it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from iot_grid_simulator.adls import AdlsError, AdlsSink as _BaseAdlsSink

from .catalog import (
    CATALOG_RECORD_TYPES,
    LOCAL_ONLY_RECORD_TYPES,
    PRIVATE_RECORD_TYPES,
    PUBLISHED_RECORD_TYPES,
    TELEMETRY_RECORD_TYPES,
    WORKFLOW_RECORD_TYPES,
)

DEFAULT_PATH_PREFIX = "upstream-production/logs"

#: Namespace the dashboard publishes and enumerates its workflow-only runs
#: into. Full-run publication must never share it: the dashboard sanitizes to
#: ``PUBLIC_RECORD_TYPES`` and refuses local-only families, so mixing the two
#: policies in one namespace would put inventory artifacts in a location the
#: public surface lists.
DASHBOARD_PUBLIC_RUNS_PREFIX = "upstream-production/runs"

#: Namespace for full-run publication, including the approved inventory
#: families. Deliberately a sibling of both the monitoring namespace and the
#: dashboard's run namespace rather than a child or a reuse of either, so a
#: reader cannot mistake published run artifacts for the sparse monitoring log
#: stream or for the dashboard's public runs.
DEFAULT_RUN_PATH_PREFIX = "upstream-production/inventory"


class AdlsSink(_BaseAdlsSink):
    """ADLS sink with the upstream monitoring namespace as its safe default."""

    def __init__(
        self,
        account_url: str,
        file_system: str,
        path_prefix: str = DEFAULT_PATH_PREFIX,
        mode: Literal["stream", "upload"] = "upload",
        auth_mode: Literal["default", "managed-identity"] = "default",
        managed_identity_client_id: str | None = None,
        overwrite: bool = False,
        chunk_size: int = 4 * 1024 * 1024,
        service_client: Any | None = None,
    ) -> None:
        super().__init__(
            account_url=account_url,
            file_system=file_system,
            path_prefix=path_prefix,
            mode=mode,
            auth_mode=auth_mode,
            managed_identity_client_id=managed_identity_client_id,
            overwrite=overwrite,
            chunk_size=chunk_size,
            service_client=service_client,
        )


class AdlsMonitoringLogSink:
    """Publish only allowlisted sparse public upstream log and workflow families."""

    LOG_TYPES = frozenset(WORKFLOW_RECORD_TYPES)
    LOCAL_ONLY_TYPES = frozenset(
        (
            *CATALOG_RECORD_TYPES,
            *TELEMETRY_RECORD_TYPES,
            *LOCAL_ONLY_RECORD_TYPES,
            *PRIVATE_RECORD_TYPES,
        )
    )
    PRIVATE_TYPES = frozenset(PRIVATE_RECORD_TYPES)

    def __init__(self, sink: AdlsSink) -> None:
        self.sink = sink

    def artifact_ready(
        self,
        path: Path,
        relative_path: str,
        run_id: str,
        metadata: dict[str, Any],
    ) -> None:
        record_type = metadata.get("record_type")
        if not isinstance(record_type, str) or not record_type:
            raise AdlsError("artifact metadata must include a non-empty record_type")
        if record_type in self.PRIVATE_TYPES:
            raise AdlsError(
                f"private record_type must never be published to a monitoring sink: {record_type}"
            )
        if record_type in self.LOG_TYPES:
            expected_path = f"{record_type}.jsonl"
            if relative_path != expected_path or path.name != expected_path:
                raise AdlsError(
                    f"monitoring artifact path must be {expected_path} "
                    f"for record_type {record_type}"
                )
            self.sink.artifact_ready(path, relative_path, run_id, metadata)
            return
        if record_type in self.LOCAL_ONLY_TYPES:
            return
        raise AdlsError(f"unsupported record_type for monitoring sink: {record_type}")

    def finalize(
        self,
        output_directory: Path,
        logs_manifest_path: Path,
        logs_manifest: dict[str, Any],
    ) -> None:
        self._validate_logs_manifest(logs_manifest_path, logs_manifest)
        self.sink.finalize(output_directory, logs_manifest_path, logs_manifest)

    @classmethod
    def _validate_logs_manifest(
        cls,
        logs_manifest_path: Path,
        logs_manifest: dict[str, Any],
    ) -> None:
        if not isinstance(logs_manifest, dict):
            raise AdlsError("logs manifest must be a JSON object")
        if logs_manifest_path.name != "logs-manifest.json":
            raise AdlsError("logs manifest path must be logs-manifest.json")
        if logs_manifest.get("private_truth_included") is not False:
            raise AdlsError("logs manifest must set private_truth_included to false")

        counts = logs_manifest.get("record_counts")
        if not isinstance(counts, dict):
            raise AdlsError("logs manifest must include a record_counts object")
        for record_type in sorted(counts):
            if record_type not in cls.LOG_TYPES:
                raise AdlsError(
                    f"logs manifest contains unsupported record_type: {record_type}"
                )

        files = logs_manifest.get("files")
        if not isinstance(files, list):
            raise AdlsError("logs manifest must include a files list")

        for index, entry in enumerate(files):
            if not isinstance(entry, dict):
                raise AdlsError(f"logs manifest file entry {index} must be an object")
            path = entry.get("path")
            if not isinstance(path, str) or not path:
                raise AdlsError(
                    f"logs manifest file entry {index} must include a non-empty path"
                )
            record_type = entry.get("record_type")
            if not isinstance(record_type, str) or not record_type:
                raise AdlsError(
                    f"logs manifest file entry {index} must include a non-empty record_type"
                )
            if record_type not in cls.LOG_TYPES:
                raise AdlsError(
                    f"logs manifest contains unsupported record_type: {record_type}"
                )
            expected_path = f"{record_type}.jsonl"
            if path != expected_path:
                raise AdlsError(
                    f"logs manifest path must be {expected_path} "
                    f"for record_type {record_type}"
                )


class AdlsRunPublicationSink:
    """Publish a full run: the public families plus the approved inventory ones.

    The monitoring sink above answers "what is happening now" and carries only
    the sparse workflow families. This sink answers "what does this run contain"
    and is the path the owner approved for inventory publication in design
    phase 9.

    It refuses the private family outright and refuses anything the allowlist in
    ``catalog.py`` has not named, so widening this sink requires editing the
    allowlist where the invariant assertions live -- not here.
    """

    PUBLISHABLE_TYPES = frozenset(PUBLISHED_RECORD_TYPES)
    PRIVATE_TYPES = frozenset(PRIVATE_RECORD_TYPES)

    def __init__(self, sink: AdlsSink) -> None:
        self.sink = sink

    def artifact_ready(
        self,
        path: Path,
        relative_path: str,
        run_id: str,
        metadata: dict[str, Any],
    ) -> None:
        record_type = metadata.get("record_type")
        if not isinstance(record_type, str) or not record_type:
            raise AdlsError("artifact metadata must include a non-empty record_type")
        if record_type in self.PRIVATE_TYPES:
            raise AdlsError(
                f"private record_type must never be published: {record_type}"
            )
        if record_type not in self.PUBLISHABLE_TYPES:
            # Silently skipping would make an accidental narrowing of the
            # allowlist look like a successful publication of everything.
            raise AdlsError(
                f"record_type is not in the publication allowlist: {record_type}"
            )
        expected_path = f"{record_type}.jsonl"
        if relative_path != expected_path or path.name != expected_path:
            raise AdlsError(
                f"published artifact path must be {expected_path} "
                f"for record_type {record_type}"
            )
        self.sink.artifact_ready(path, relative_path, run_id, metadata)

    def finalize(
        self,
        output_directory: Path,
        manifest_path: Path,
        manifest: dict[str, Any],
    ) -> None:
        self._validate_public_manifest(manifest_path, manifest)
        self.sink.finalize(output_directory, manifest_path, manifest)

    @classmethod
    def _validate_public_manifest(
        cls,
        manifest_path: Path,
        manifest: dict[str, Any],
    ) -> None:
        """Check the manifest at the door, not just the artifacts.

        The manifest names families. If the sanitizer ever regressed, a private
        family could be named in a manifest even though no private artifact was
        ever handed to ``artifact_ready``.
        """
        if not isinstance(manifest, dict):
            raise AdlsError("public manifest must be a JSON object")
        if manifest_path.name != "manifest.json":
            raise AdlsError("public manifest path must be manifest.json")

        counts = manifest.get("record_counts")
        if not isinstance(counts, dict):
            raise AdlsError("public manifest must include a record_counts object")

        named: set[str] = set(counts)
        files = manifest.get("files")
        if not isinstance(files, list):
            raise AdlsError("public manifest must include a files list")
        for index, entry in enumerate(files):
            if not isinstance(entry, dict):
                raise AdlsError(f"public manifest file entry {index} must be an object")
            record_type = entry.get("record_type")
            if not isinstance(record_type, str) or not record_type:
                raise AdlsError(
                    f"public manifest file entry {index} must include a non-empty record_type"
                )
            named.add(record_type)

        forbidden = sorted(named & cls.PRIVATE_TYPES)
        if forbidden:
            raise AdlsError(
                f"public manifest must never name a private record_type: {forbidden}"
            )
        unexpected = sorted(named - cls.PUBLISHABLE_TYPES)
        if unexpected:
            raise AdlsError(
                f"public manifest names record types outside the allowlist: {unexpected}"
            )


__all__ = [
    "AdlsError",
    "AdlsMonitoringLogSink",
    "AdlsRunPublicationSink",
    "AdlsSink",
    "DEFAULT_PATH_PREFIX",
    "DEFAULT_RUN_PATH_PREFIX",
    "DASHBOARD_PUBLIC_RUNS_PREFIX",
]
