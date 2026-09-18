"""Output sink contract for optional external destinations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class DatasetSink(Protocol):
    """Receives deterministic artifacts without changing their local content."""

    def artifact_ready(
        self,
        path: Path,
        relative_path: str,
        run_id: str,
        metadata: dict[str, Any],
    ) -> None:
        """Handle an artifact immediately after local serialization."""

    def finalize(
        self,
        output_directory: Path,
        manifest_path: Path,
        manifest: dict[str, Any],
    ) -> None:
        """Finalize the external run after the deterministic manifest exists."""

