"""Optional Azure Data Lake Storage Gen2 output sink."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Literal
from urllib.parse import urlparse

from .io import canonical_json, sha256_file

AdlsMode = Literal["stream", "upload"]
AuthMode = Literal["default", "managed-identity"]


class AdlsError(RuntimeError):
    """Raised when ADLS configuration or transfer fails."""


class AdlsSink:
    """Append deterministic artifacts to an ADLS Gen2 run directory."""

    def __init__(
        self,
        account_url: str,
        file_system: str,
        path_prefix: str = "iot-grid-simulator",
        mode: AdlsMode = "upload",
        auth_mode: AuthMode = "default",
        managed_identity_client_id: str | None = None,
        overwrite: bool = False,
        chunk_size: int = 4 * 1024 * 1024,
        service_client: Any | None = None,
    ) -> None:
        parsed = urlparse(account_url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise AdlsError("ADLS account URL must be an absolute HTTPS URL")
        if not file_system or "/" in file_system or "\\" in file_system:
            raise AdlsError("ADLS file system must be a non-empty container name")
        if mode not in {"stream", "upload"}:
            raise AdlsError("ADLS mode must be stream or upload")
        if auth_mode not in {"default", "managed-identity"}:
            raise AdlsError("Azure auth mode must be default or managed-identity")
        if managed_identity_client_id and auth_mode != "managed-identity":
            raise AdlsError(
                "managed identity client ID requires managed-identity auth mode"
            )
        if chunk_size <= 0:
            raise AdlsError("ADLS chunk size must be positive")

        normalized_prefix = path_prefix.strip("/")
        if "\\" in normalized_prefix or ".." in PurePosixPath(normalized_prefix).parts:
            raise AdlsError("ADLS path prefix must be a safe POSIX path")

        self.account_url = f"{parsed.scheme}://{parsed.netloc}"
        self.file_system_name = file_system
        self.path_prefix = normalized_prefix
        self.mode = mode
        self.auth_mode = auth_mode
        self.managed_identity_client_id = managed_identity_client_id
        self.overwrite = overwrite
        self.chunk_size = chunk_size
        self._uploaded: set[str] = set()
        self._run_id: str | None = None
        self._active_run_id: str | None = None

        if service_client is None:
            service_client = self._build_service_client()
        else:
            self._not_found_error = FileNotFoundError
            self._exists_error = FileExistsError
            self._collision_errors = (FileExistsError,)
            self._if_missing = "if_missing"
            self._azure_error: type[BaseException] | tuple[type[BaseException], ...] = ()
        self._file_system_client = service_client.get_file_system_client(file_system)

    def _build_service_client(self) -> Any:
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import (
                AzureError,
                ResourceExistsError,
                ResourceModifiedError,
                ResourceNotFoundError,
            )
            from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
            from azure.storage.filedatalake import DataLakeServiceClient
        except ImportError as exc:
            raise AdlsError(
                "ADLS support requires `python -m pip install -e .[azure]`"
            ) from exc

        self._not_found_error = ResourceNotFoundError
        self._exists_error = ResourceExistsError
        self._collision_errors = (ResourceExistsError, ResourceModifiedError)
        self._if_missing = MatchConditions.IfMissing
        self._azure_error = AzureError
        credential = (
            DefaultAzureCredential()
            if self.auth_mode == "default"
            else ManagedIdentityCredential(client_id=self.managed_identity_client_id)
        )
        return DataLakeServiceClient(
            account_url=self.account_url,
            credential=credential,
        )

    def remote_run_path(self, run_id: str | None = None) -> str:
        resolved_run_id = run_id or self._run_id
        if resolved_run_id is None:
            raise AdlsError("ADLS run path is unavailable before generation starts")
        run_component = f"run_id={resolved_run_id}"
        return (
            f"{self.path_prefix}/{run_component}"
            if self.path_prefix
            else run_component
        )

    def _remote_path(self, run_id: str, relative_path: str) -> str:
        if (
            not relative_path
            or "\\" in relative_path
            or ".." in PurePosixPath(relative_path).parts
        ):
            raise AdlsError("artifact path must be a safe relative POSIX path")
        return f"{self.remote_run_path(run_id)}/{relative_path}"

    def _ensure_directories(self, remote_file_path: str) -> None:
        current: list[str] = []
        for part in PurePosixPath(remote_file_path).parent.parts:
            current.append(part)
            directory = self._file_system_client.get_directory_client(
                "/".join(current)
            )
            try:
                directory.create_directory()
            except self._exists_error:
                continue

    def _prepare_file(self, remote_path: str) -> Any:
        file_client = self._file_system_client.get_file_client(remote_path)
        if self.overwrite:
            file_client.create_file()
            return file_client
        try:
            file_client.create_file(
                match_condition=self._if_missing,
            )
        except self._collision_errors as exc:
            raise FileExistsError(
                f"ADLS destination already exists: {remote_path}"
            ) from exc
        return file_client

    def _delete_if_exists(self, remote_path: str) -> None:
        file_client = self._file_system_client.get_file_client(remote_path)
        try:
            file_client.get_file_properties()
        except self._not_found_error:
            return
        except self._azure_error as exc:
            raise AdlsError(
                f"ADLS completion marker could not be inspected: {exc}"
            ) from exc
        try:
            file_client.delete_file()
        except self._not_found_error:
            return
        except self._azure_error as exc:
            raise AdlsError(
                f"ADLS completion marker could not be invalidated: {exc}"
            ) from exc

    def _begin_run(self, run_id: str) -> None:
        if self._active_run_id is not None and self._active_run_id != run_id:
            raise AdlsError(
                "one ADLS sink cannot interleave multiple dataset runs"
            )
        if self._active_run_id == run_id:
            return
        if self.overwrite:
            marker_path = self._remote_path(run_id, "_SUCCESS.json")
            try:
                self._ensure_directories(marker_path)
            except self._azure_error as exc:
                raise AdlsError(
                    f"ADLS completion marker path could not be prepared: {exc}"
                ) from exc
            self._delete_if_exists(marker_path)
        self._active_run_id = run_id
        self._run_id = run_id
        self._uploaded = set()

    def _append_stream(self, file_client: Any, stream: BinaryIO) -> None:
        offset = 0
        while chunk := stream.read(self.chunk_size):
            file_client.append_data(
                data=chunk,
                offset=offset,
                length=len(chunk),
            )
            offset += len(chunk)
        file_client.flush_data(offset)

    def _upload_file(self, local_path: Path, remote_path: str) -> None:
        try:
            self._ensure_directories(remote_path)
            file_client = self._prepare_file(remote_path)
            with local_path.open("rb") as stream:
                self._append_stream(file_client, stream)
        except self._azure_error as exc:
            raise AdlsError(f"ADLS upload failed for {remote_path}: {exc}") from exc

    def _upload_bytes(self, payload: bytes, remote_path: str) -> None:
        try:
            self._ensure_directories(remote_path)
            file_client = self._prepare_file(remote_path)
            offset = 0
            for start in range(0, len(payload), self.chunk_size):
                chunk = payload[start : start + self.chunk_size]
                file_client.append_data(
                    data=chunk,
                    offset=offset,
                    length=len(chunk),
                )
                offset += len(chunk)
            file_client.flush_data(offset)
        except self._azure_error as exc:
            raise AdlsError(f"ADLS upload failed for {remote_path}: {exc}") from exc

    def artifact_ready(
        self,
        path: Path,
        relative_path: str,
        run_id: str,
        metadata: dict[str, Any],
    ) -> None:
        """In stream mode, append each JSONL artifact as soon as it is ready."""
        self._begin_run(run_id)
        if self.mode == "stream" and metadata["format"] == "jsonl":
            remote_path = self._remote_path(run_id, relative_path)
            self._upload_file(path, remote_path)
            self._uploaded.add(relative_path)

    def finalize(
        self,
        output_directory: Path,
        manifest_path: Path,
        manifest: dict[str, Any],
    ) -> None:
        """Upload pending artifacts, then manifest and deterministic success marker."""
        run_id = manifest["run_id"]
        self._begin_run(run_id)
        for entry in manifest["files"]:
            relative_path = entry["path"]
            if relative_path not in self._uploaded:
                self._upload_file(
                    output_directory / relative_path,
                    self._remote_path(run_id, relative_path),
                )
                self._uploaded.add(relative_path)

        manifest_remote_path = self._remote_path(run_id, manifest_path.name)
        self._upload_file(manifest_path, manifest_remote_path)
        marker = {
            "manifest_path": manifest_path.name,
            "manifest_sha256": sha256_file(manifest_path),
            "run_id": run_id,
            "status": "complete",
        }
        marker_payload = (canonical_json(marker) + "\n").encode("utf-8")
        self._upload_bytes(
            marker_payload,
            self._remote_path(run_id, "_SUCCESS.json"),
        )
        self._active_run_id = None
        self._uploaded = set()
