"""Adapter base class and registry."""

from __future__ import annotations

from typing import Any, Iterator

from ..envelope import IncidentEnvelope


class Adapter:
    """Translate one vendor payload shape into an IncidentEnvelope."""

    #: Value written to `envelope.source`.
    source: str = "unknown"
    #: Bumped when the mapping changes, so normalized output is traceable.
    version: str = "1.0"
    #: Lower numbers are offered the payload first.
    priority: int = 100

    def matches(self, payload: dict[str, Any]) -> bool:
        raise NotImplementedError

    def normalize(self, payload: dict[str, Any]) -> IncidentEnvelope:
        raise NotImplementedError

    # -- helpers available to all adapters ----------------------------

    @staticmethod
    def _get(payload: dict[str, Any], path: str, default: Any = None) -> Any:
        """Read a dotted path, tolerating missing intermediate keys."""
        node: Any = payload
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node if node is not None else default


class AdapterRegistry:
    """Ordered collection of adapters."""

    def __init__(self) -> None:
        self._adapters: list[Adapter] = []

    def register(self, adapter: Adapter) -> Adapter:
        self._adapters.append(adapter)
        self._adapters.sort(key=lambda a: a.priority)
        return adapter

    def resolve(self, payload: dict[str, Any]) -> Adapter | None:
        for adapter in self._adapters:
            try:
                if adapter.matches(payload):
                    return adapter
            except Exception:  # a broken matcher must not block the others
                continue
        return None

    def __iter__(self) -> Iterator[Adapter]:
        return iter(self._adapters)

    def __len__(self) -> int:
        return len(self._adapters)


registry = AdapterRegistry()
