"""Shared, bounded workload configuration and disposable-filesystem operations."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import tempfile
from dataclasses import asdict, dataclass
from itertools import islice
from pathlib import Path
from uuid import UUID

OWNER = "asre-demo"
DATA_ROOT = Path("/srv/asre-demo")
CONFIG_PATH = Path("/var/lib/asre-demo/config.json")
CHUNK_BYTES = 4 * 1024 * 1024
MAX_DISK_BYTES = 3584 * 1024 * 1024
MAX_MEMORY_BYTES = 160 * 1024 * 1024
MAX_TTL_SECONDS = 1800
FILE_NAME = re.compile(r"^[0-9]{8}\.bin$")


def canonical_id(value: str) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("A canonical, lowercase UUID is required.")
    return value


def atomic_json(path: Path, value: dict, mode: int = 0o640) -> None:
    previous = path.stat() if path.exists() else None
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.chmod(temporary, stat.S_IMODE(previous.st_mode) if previous else mode)
            if previous and hasattr(os, "fchown"):
                os.fchown(stream.fileno(), previous.st_uid, previous.st_gid)
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class Config:
    mode: str = "normal"
    scenario_id: str | None = None
    expires_at: float = 0
    byte_limit: int = 0
    interval_seconds: float = 2

    @classmethod
    def parse(cls, value: dict) -> Config:
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("Unexpected or missing workload configuration fields.")
        result = cls(**value)
        if result.mode not in {"normal", "disk", "memory", "memory-oom"}:
            raise ValueError("Unknown workload mode.")
        if type(result.byte_limit) is not int or type(result.expires_at) not in {int, float}:
            raise ValueError("Invalid numeric limits.")
        if type(result.interval_seconds) not in {int, float}:
            raise ValueError("Invalid growth interval.")
        if not math.isfinite(result.expires_at) or not math.isfinite(result.interval_seconds):
            raise ValueError("Limits must be finite.")
        if not 0.25 <= result.interval_seconds <= 30:
            raise ValueError("Growth interval is outside the bounded range.")
        if result.mode == "normal":
            if result.scenario_id is not None or result.byte_limit != 0 or result.expires_at != 0:
                raise ValueError("Normal mode cannot retain an active fault.")
        else:
            canonical_id(result.scenario_id)
            maximum = MAX_DISK_BYTES if result.mode == "disk" else MAX_MEMORY_BYTES
            if not CHUNK_BYTES <= result.byte_limit <= maximum or result.expires_at <= 0:
                raise ValueError("Fault budget is outside the bounded range.")
            if result.mode == "memory" and result.byte_limit > 64 * 1024 * 1024:
                raise ValueError("The proactive memory case must remain below the workload OOM limit.")
        return result

    def write(self, path: Path = CONFIG_PATH) -> None:
        atomic_json(path, asdict(self))

    @classmethod
    def read(cls, path: Path = CONFIG_PATH) -> Config:
        with path.open(encoding="utf-8") as stream:
            return cls.parse(json.load(stream))


def validate_data_root(root: Path = DATA_ROOT) -> None:
    if root != root.resolve() or not os.path.ismount(root):
        raise ValueError("The disposable root must be a real mount without symlink components.")
    if root.stat().st_dev == Path("/").stat().st_dev:
        raise ValueError("Refusing to write or clean files on the OS filesystem.")
    marker = root / ".asre-demo-owner"
    marker_stat = marker.lstat()
    if not stat.S_ISREG(marker_stat.st_mode) or marker_stat.st_uid != 0:
        raise ValueError("The ownership marker must be a root-owned regular file.")
    if marker_stat.st_mode & 0o022:
        raise ValueError("The ownership marker must not be writable by the workload.")
    if marker.read_text(encoding="utf-8").strip() != OWNER:
        raise ValueError("The disposable volume belongs to a different owner.")


def clean_scenario(scenario_id: str, root: Path = DATA_ROOT) -> int:
    canonical_id(scenario_id)
    validate_data_root(root)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(root, flags)
    try:
        data_fd = os.open("data", flags, dir_fd=root_fd)
        try:
            try:
                scenario_fd = os.open(scenario_id, flags, dir_fd=data_fd)
            except FileNotFoundError:
                return 0
            try:
                manifest_fd = os.open("manifest.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=scenario_fd)
                with os.fdopen(manifest_fd, encoding="utf-8") as stream:
                    raw = stream.read(65537)
                if len(raw) > 65536:
                    raise ValueError("The disposable manifest exceeds its bounded size.")
                manifest = json.loads(raw)
                if (
                    not isinstance(manifest, dict)
                    or set(manifest) != {"owner", "scenario_id", "files"}
                    or manifest["owner"] != OWNER
                    or manifest["scenario_id"] != scenario_id
                    or not isinstance(manifest["files"], list)
                    or len(manifest["files"]) > MAX_DISK_BYTES // CHUNK_BYTES + 1
                    or any(not isinstance(name, str) or not FILE_NAME.fullmatch(name) for name in manifest["files"])
                    or len(set(manifest["files"])) != len(manifest["files"])
                ):
                    raise ValueError("Invalid disposable file manifest.")
                with os.scandir(scenario_fd) as iterator:
                    entries = list(islice(iterator, MAX_DISK_BYTES // CHUNK_BYTES + 2))
                if len(entries) > len(manifest["files"]) + 1:
                    raise ValueError("The scenario contains more entries than its exact manifest.")
                allowed = set(manifest["files"]) | {"manifest.json"}
                for entry in entries:
                    metadata = entry.stat(follow_symlinks=False)
                    if (
                        entry.name not in allowed or not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_nlink != 1 or metadata.st_uid != os.fstat(scenario_fd).st_uid
                    ):
                        raise ValueError("Unowned, linked or unexpected entry; cleanup refused.")
                count = 0
                for entry in entries:
                    if entry.name != "manifest.json":
                        os.unlink(entry.name, dir_fd=scenario_fd)
                        count += 1
                os.unlink("manifest.json", dir_fd=scenario_fd)
            finally:
                os.close(scenario_fd)
            os.rmdir(scenario_id, dir_fd=data_fd)
            return count
        finally:
            os.close(data_fd)
    finally:
        os.close(root_fd)
