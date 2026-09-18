"""Shared deterministic primitives for the midstream parts layer.

These helpers carry the repository determinism contract into the midstream
package: content-derived identifiers, stable ordering, and finite floats
rounded to six decimal places. They are deliberately identical in behaviour to
the upstream helpers of the same names, because the parts layer is a port of
the upstream one and any drift in the hashing would silently change every
derived value.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def round6(value: float) -> float:
    rounded = round(float(value), 6)
    if not math.isfinite(rounded):
        raise ValueError("parts layer produced a non-finite value")
    return 0.0 if rounded == -0.0 else rounded


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def hash_float(seed: int, *parts: str, low: float = -1.0, high: float = 1.0) -> float:
    """Deterministic float in ``[low, high)`` derived from the seed and parts.

    The default range is signed. Callers that need a non-negative draw must
    pass ``low`` and ``high`` explicitly; relying on the default and then
    scaling produces negative values.
    """
    digest = hashlib.sha256(f"{seed}|{'|'.join(parts)}".encode("utf-8")).digest()
    return low + (high - low) * (int.from_bytes(digest[:8], "big") / 2**64)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


__all__ = ["clamp", "hash_float", "round6", "stable_id", "stamp"]
