"""Durable conditional state and explicitly selected log delivery transports.

Acceptance by Logs Ingestion is not proof of queryability. Delivery retries and
outbox acknowledgement belong to the runtime, not these transports.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import uuid

from azure.core import MatchConditions
from azure.core.exceptions import (
    ClientAuthenticationError, DecodeError, HttpResponseError,
    ResourceExistsError, ResourceModifiedError, ResourceNotFoundError,
    ServiceRequestError, ServiceResponseError,
)
from azure.core.pipeline.policies import RetryPolicy
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.monitor.ingestion import LogsIngestionClient
from azure.storage.blob import BlobServiceClient, LinearRetry


MAX_REQUEST_BYTES = 200_000
MAX_FIELD_BYTES = 60_000
MAX_STATE_BYTES = 32 * 1024 * 1024
MAX_LISTED_KEYS = 1024
LOCK_TIMEOUT_SECONDS = 5.0
TABLE_STREAMS = {
    "EnergyObservations_CL": "Custom-EnergyObservations_CL",
    "EnergyInjectionAudit_CL": "Custom-EnergyInjectionAudit_CL",
}
_KEY = re.compile(r"[a-z][a-z0-9-]{0,79}\Z")
_IO_ERRORS = (ServiceRequestError, ServiceResponseError)


class StoreConflict(Exception):
    """The supplied version no longer owns the document."""


class StoreUnavailable(Exception):
    """Durable storage failed; no successful commit may be inferred."""


class DeliveryError(Exception):
    """Sanitized transport failure suitable for persistent retry metadata."""

    def __init__(self, code: str, retryable: bool, retry_after: float | None = None):
        self.code = code if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", code or "") else "DeliveryFailed"
        self.retryable = bool(retryable)
        self.retry_after = (
            float(retry_after) if isinstance(retry_after, (int, float))
            and math.isfinite(retry_after) and retry_after >= 0 else None
        )
        super().__init__(self.code)


def _hosted():
    return any(os.getenv(name) for name in (
        "WEBSITE_SITE_NAME", "WEBSITE_INSTANCE_ID", "IDENTITY_ENDPOINT", "MSI_ENDPOINT",
    ))


def _credential(credential):
    if credential is not None:
        if not callable(getattr(credential, "get_token", None)):
            raise ValueError("A token credential is required")
        if _hosted() and not isinstance(credential, ManagedIdentityCredential):
            raise ValueError("Hosted delivery requires ManagedIdentityCredential")
        return credential
    return ManagedIdentityCredential() if _hosted() else DefaultAzureCredential()


def _url(value, host_pattern):
    if not isinstance(value, str):
        raise ValueError("An approved Azure HTTPS endpoint is required")
    try:
        parts = urlsplit(value)
        valid = (
            parts.scheme == "https" and re.fullmatch(host_pattern, parts.netloc)
            and parts.path in ("", "/") and not parts.query and not parts.fragment
            and value == value.strip() and not any(c.isspace() for c in value)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("An approved Azure HTTPS endpoint without path or credentials is required")
    return value.rstrip("/")


def _key(key):
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise ValueError("State keys must be safe lowercase names of at most 80 characters")
    # These are device names even with an extension on Windows.
    if key in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)),
               *(f"lpt{i}" for i in range(10))}:
        raise ValueError("Reserved state key")
    return key


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"), sort_keys=True).encode("utf-8")


def _json_types(value, depth=0):
    if depth > 64:
        raise ValueError("JSON nesting is too deep")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            _json_types(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _json_types(item, depth + 1)
    elif value is not None and type(value) not in (str, bool, int, float):
        raise ValueError("Only JSON types are supported")


def _document_bytes(document, max_bytes=MAX_STATE_BYTES):
    if not isinstance(document, dict):
        raise ValueError("State document must be an object")
    _json_types(document)
    data = _json_bytes(document)
    if len(data) > max_bytes:
        raise ValueError("State document exceeds the durable storage bound")
    return data


def _version(version):
    if version is not None and (not isinstance(version, str) or not version or len(version) > 256):
        raise ValueError("Invalid state version")


def _load_json(data, max_bytes=MAX_STATE_BYTES):
    def invalid_constant(_value):
        raise ValueError("Nonfinite stored JSON")
    if len(data) > max_bytes:
        raise ValueError("Stored JSON exceeds its size bound")
    value = json.loads(data, parse_constant=invalid_constant)
    _document_bytes(value, max_bytes)
    return value


def _sync_directory(directory):
    if os.name != "nt":
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _atomic_write(path, data):
    staging = path.with_name(f".transport-{uuid.uuid4().hex}.pending")
    try:
        with staging.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
        _sync_directory(path.parent)
    finally:
        staging.unlink(missing_ok=True)


class LocalStateStore:
    """Process-safe CAS using a persistent OS-locked file and atomic replacement.

    The lock file is never deleted or stolen. The operating system releases
    ownership on process exit/crash, so a dead owner cannot strand the store.
    """

    def __init__(self, directory):
        self.directory = Path(directory)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise StoreUnavailable("LocalStateUnavailable") from None

    @contextmanager
    def _locked(self):
        try:
            with (self.directory / ".transport.lock").open("a+b") as handle:
                # Windows byte-range locking permits a region beyond EOF.
                deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
                while True:
                    try:
                        if os.name == "nt":
                            import msvcrt
                            handle.seek(0)
                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as error:
                        import errno
                        if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                            raise
                        if time.monotonic() >= deadline:
                            raise StoreUnavailable("LocalStateBusy") from None
                        time.sleep(0.01)
                try:
                    yield
                finally:
                    if os.name == "nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            raise StoreUnavailable("LocalStateUnavailable") from None

    def _read(self, key):
        path = self.directory / f"{key}.json"
        if path.is_symlink():
            raise StoreUnavailable("UnsafeStateFile")
        try:
            with path.open("rb") as handle:
                data = handle.read(MAX_STATE_BYTES + 1025)
        except FileNotFoundError:
            return None, None
        try:
            envelope = _load_json(data, MAX_STATE_BYTES + 1024)
            if set(envelope) != {"document", "version"}:
                raise ValueError("Invalid envelope")
            _document_bytes(envelope["document"])
            version = envelope["version"]
            if not isinstance(version, str) or not re.fullmatch(r"[0-9a-f]{32}", version):
                raise ValueError("Invalid version")
            return envelope["document"], version
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise StoreUnavailable("CorruptLocalState") from None

    def read(self, key) -> tuple[dict | None, str | None]:
        _key(key)
        with self._locked():
            return self._read(key)

    def write(self, key, document: dict, version: str | None) -> str:
        _key(key)
        _version(version)
        _document_bytes(document)
        with self._locked():
            _, current = self._read(key)
            if current != version:
                raise StoreConflict("StateVersionConflict")
            new_version = uuid.uuid4().hex
            _atomic_write(self.directory / f"{key}.json",
                          _json_bytes({"document": document, "version": new_version}))
            return new_version

    def keys(self) -> list[str]:
        with self._locked():
            result = []
            for path in self.directory.glob("*.json"):
                try:
                    name = _key(path.stem)
                except ValueError:
                    raise StoreUnavailable("UnsafeStateFile") from None
                if path.is_symlink():
                    raise StoreUnavailable("UnsafeStateFile")
                result.append(name)
                if len(result) > MAX_LISTED_KEYS:
                    raise StoreUnavailable("StateKeyLimitExceeded")
            return sorted(result)


class BlobStateStore:
    def __init__(self, account_url, container, credential=None):
        account_url = _url(account_url, r"[a-z0-9]{3,24}\.blob\.core\.windows\.net")
        if not isinstance(container, str) or not re.fullmatch(
                r"[a-z0-9](?:[a-z0-9]|-(?!-)){1,61}[a-z0-9]", container):
            raise ValueError("Invalid Azure blob container")
        self._service = BlobServiceClient(
            account_url, credential=_credential(credential),
            retry_policy=LinearRetry(backoff=1, retry_total=2, random_jitter_range=0),
            connection_timeout=5, read_timeout=15,
        )
        self._container = self._service.get_container_client(container)

    def read(self, key) -> tuple[dict | None, str | None]:
        key = _key(key)
        try:
            download = self._container.get_blob_client(f"{key}.json").download_blob(
                max_concurrency=1, timeout=20)
            if download.size > MAX_STATE_BYTES:
                raise StoreUnavailable("StateDocumentTooLarge")
            data = download.readall()
            # The version comes from the same GET, never a separate properties read.
            version = download.properties.etag
            if not isinstance(version, str) or not version or len(version) > 256:
                raise StoreUnavailable("InvalidBlobVersion")
            return _load_json(data), version
        except ResourceNotFoundError as error:
            if error.error_code == "BlobNotFound":
                return None, None
            raise StoreUnavailable("BlobStateUnavailable") from None
        except (HttpResponseError, *_IO_ERRORS, ClientAuthenticationError, DecodeError):
            raise StoreUnavailable("BlobStateUnavailable") from None
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise StoreUnavailable("CorruptBlobState") from None

    def write(self, key, document: dict, version: str | None) -> str:
        key = _key(key)
        _version(version)
        data = _document_bytes(document)
        options = {"overwrite": False} if version is None else {
            "overwrite": True, "etag": version, "match_condition": MatchConditions.IfNotModified,
        }
        try:
            result = self._container.get_blob_client(f"{key}.json").upload_blob(
                data, **options, timeout=20)
            new_version = result.get("etag")
            if not isinstance(new_version, str) or not new_version or len(new_version) > 256:
                raise StoreUnavailable("InvalidBlobVersion")
            return new_version
        except (ResourceExistsError, ResourceModifiedError):
            raise StoreConflict("StateVersionConflict") from None
        except HttpResponseError as error:
            if error.status_code in (409, 412) or (
                    version is not None and error.error_code == "BlobNotFound"):
                raise StoreConflict("StateVersionConflict") from None
            raise StoreUnavailable("BlobStateUnavailable") from None
        except (*_IO_ERRORS, ClientAuthenticationError, DecodeError):
            raise StoreUnavailable("BlobStateUnavailable") from None

    def keys(self) -> list[str]:
        result = []
        try:
            for blob in self._container.list_blobs(results_per_page=100, timeout=20):
                name = blob.name
                if not name.endswith(".json"):
                    raise StoreUnavailable("UnsafeStateBlob")
                result.append(_key(name[:-5]))
                if len(result) > MAX_LISTED_KEYS:
                    raise StoreUnavailable("StateKeyLimitExceeded")
        except (HttpResponseError, *_IO_ERRORS, ClientAuthenticationError, DecodeError):
            raise StoreUnavailable("BlobStateUnavailable") from None
        except ValueError:
            raise StoreUnavailable("UnsafeStateBlob") from None
        return sorted(result)


def _retry_after(headers):
    normalized = {str(key).lower(): value for key, value in (headers or {}).items()}
    value = normalized.get("retry-after")
    if value is None:
        value = normalized.get("x-ms-retry-after-ms", normalized.get("retry-after-ms"))
        try:
            seconds = float(value) / 1000
            return seconds if math.isfinite(seconds) and seconds >= 0 else None
        except (TypeError, ValueError, OverflowError):
            return None
    try:
        seconds = float(value)
        if seconds < 0:
            return None
    except (TypeError, ValueError, OverflowError):
        try:
            date = parsedate_to_datetime(str(value))
            if date.tzinfo is None:
                return None
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


class _BoundedRetryPolicy(RetryPolicy):
    """Keep long server-directed waits in the durable outbox, not the worker."""

    def get_retry_after(self, response):
        return _retry_after(response.http_response.headers)

    def is_retry(self, settings, response):
        delay = self.get_retry_after(response)
        if delay is not None and delay > 5:
            return False
        return super().is_retry(settings, response)


def _payload(table, records):
    if not isinstance(table, str) or table not in TABLE_STREAMS:
        raise DeliveryError("InvalidTable", False)
    if not isinstance(records, list) or not records or not all(isinstance(row, dict) for row in records):
        raise DeliveryError("InvalidRecords", False)
    try:
        _json_types(records)
        data = _json_bytes(records)
        if len(data) > MAX_REQUEST_BYTES:
            raise DeliveryError("RequestTooLarge", False)
        for record in records:
            for key, value in record.items():
                # Dynamic fields are bounded as whole serialized values too.
                if max(len(_json_bytes(key)), len(_json_bytes(value)),
                       len(json.dumps(key, allow_nan=False).encode("utf-8")),
                       len(json.dumps(value, allow_nan=False).encode("utf-8"))) >= MAX_FIELD_BYTES:
                    raise DeliveryError("FieldTooLarge", False)
            if len(_json_bytes(record)) > MAX_REQUEST_BYTES:
                raise DeliveryError("RecordTooLarge", False)
        # The SDK uses ASCII-escaped JSON with spaces, unlike runtime accounting.
        # Check its actual representation too, preventing hidden multi-chunk sends.
        if len(json.dumps(records, allow_nan=False).encode("utf-8")) > MAX_REQUEST_BYTES:
            raise DeliveryError("RequestTooLarge", False)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise DeliveryError("InvalidJson", False) from None
    return data


class IngestionSender:
    delivery_mode = "azure"

    def __init__(self, endpoint, rule_id, credential=None, client=None):
        endpoint = _url(endpoint, r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.ingest\.monitor\.azure\.com")
        if not isinstance(rule_id, str) or not re.fullmatch(r"dcr-[0-9a-fA-F]{32}", rule_id):
            raise ValueError("A DCR immutable ID is required")
        self.rule_id = rule_id
        self.client = client if client is not None else LogsIngestionClient(
            endpoint, credential=_credential(credential),
            retry_policy=_BoundedRetryPolicy(
                retry_total=2, retry_connect=2, retry_read=2, retry_status=2,
                retry_backoff_factor=0.5, retry_backoff_max=2, timeout=45,
            ),
            connection_timeout=5, read_timeout=15,
        )

    def send(self, table: str, records: list[dict]) -> None:
        _payload(table, records)
        try:
            # No on_error callback: any failed SDK chunk must raise, not return success.
            result = self.client.upload(
                rule_id=self.rule_id, stream_name=TABLE_STREAMS[table], logs=records,
                connection_timeout=5, read_timeout=15,
            )
            if result is not None:
                raise DeliveryError("UnexpectedIngestionResponse", False)
        except ClientAuthenticationError:
            raise DeliveryError("AuthenticationFailed", False) from None
        except HttpResponseError as error:
            status = error.status_code
            retryable = status == 429 or isinstance(status, int) and 500 <= status <= 599
            response = getattr(error, "response", None)
            delay = _retry_after(getattr(response, "headers", None))
            code = f"Http{status}" if isinstance(status, int) and 100 <= status <= 599 else "HttpFailure"
            raise DeliveryError(code, retryable, delay) from None
        except _IO_ERRORS:
            raise DeliveryError("TransportFailure", True) from None
        except DecodeError:
            raise DeliveryError("InvalidIngestionResponse", False) from None
        except (ValueError, TypeError):
            raise DeliveryError("InvalidIngestionRequest", False) from None


class LocalCaptureSender:
    """Explicit durable local capture; never represents Azure acceptance."""

    delivery_mode = "local_capture"

    def __init__(self, directory):
        if _hosted():
            raise ValueError("Local capture is prohibited in hosted environments")
        self.directory = Path(directory)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise DeliveryError("LocalCaptureUnavailable", True) from None

    def send(self, table: str, records: list[dict]) -> None:
        data = _payload(table, records)
        envelope = _json_bytes({
            "delivery_mode": self.delivery_mode, "table": table,
            "records": json.loads(data),
        })
        try:
            _atomic_write(self.directory / f"{uuid.uuid4().hex}.json", envelope)
        except OSError:
            raise DeliveryError("LocalCaptureUnavailable", True) from None
