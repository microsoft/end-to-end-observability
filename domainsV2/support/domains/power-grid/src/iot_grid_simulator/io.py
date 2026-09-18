"""Deterministic JSONL, CSV, and manifest serialization."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> str:
    """Serialize JSON with deterministic keys, separators, and finite numbers."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(canonical_json(record))
            stream.write("\n")


def write_csv(
    path: Path, records: list[dict[str, Any]], fields: list[str] | None = None
) -> None:
    fields = fields or sorted({field for record in records for field in record})
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(fields)
        for record in records:
            writer.writerow([canonical_json(record.get(field)) for field in fields])


def read_csv_logical(path: Path) -> list[dict[str, Any]]:
    """Read simulator CSV cells back to their JSON logical values."""
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        return [
            {field: json.loads(value) for field, value in row.items()}
            for row in reader
        ]
