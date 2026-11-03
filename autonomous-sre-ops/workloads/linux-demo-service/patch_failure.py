"""One owned, reversible APT installation failure; assessment remains available."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from demo_common import canonical_id

DIRECTORY = Path("/etc/apt/apt.conf.d")
PREFIX = "99-asre-demo-install-failure-"
CONTENT = b'DPkg::Pre-Invoke { "/bin/false"; };\n'


def path_for(scenario_id: str) -> Path:
    canonical_id(scenario_id)
    return DIRECTORY / (PREFIX + scenario_id)


def require_directory() -> None:
    info = DIRECTORY.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError("APT configuration requires its existing root-owned, non-writable directory.")


def enable(scenario_id: str) -> None:
    target = path_for(scenario_id)
    require_directory()
    if any(DIRECTORY.glob(PREFIX + "*")):
        raise ValueError("An existing package-failure file must not be replaced or overlapped.")
    # Publish a complete file atomically; APT ignores the hidden staging file.
    with tempfile.NamedTemporaryFile(dir=DIRECTORY, prefix=".asre-demo-", delete=False) as stream:
        staging = Path(stream.name)
        try:
            stream.write(CONTENT)
            stream.flush()
            os.fsync(stream.fileno())
            os.link(staging, target, follow_symlinks=False)
        finally:
            stream.close()
            staging.unlink()


def disable(scenario_id: str) -> bool:
    target = path_for(scenario_id)
    require_directory()
    try:
        info = target.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or target.read_bytes() != CONTENT:
        raise PermissionError("Only the original regular package-failure file may be removed.")
    current = target.lstat()
    if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
        raise PermissionError("The owned package-failure file changed during cleanup.")
    target.unlink()
    return True
