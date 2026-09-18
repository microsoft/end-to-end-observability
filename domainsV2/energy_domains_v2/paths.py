"""Locations within the movable source kit, independent of its folder name."""

import os
from pathlib import Path


KIT_ROOT = Path(__file__).resolve().parents[1]


def source_root() -> Path:
    """An explicit override is supported; never discover a parent repository."""
    return Path(os.getenv("V2_REPOSITORY_ROOT", str(KIT_ROOT / "support"))).resolve()
