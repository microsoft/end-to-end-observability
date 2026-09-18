"""Version-bound request metadata, independent of Azure SDK imports."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class AgentVersionReference:
    name: str
    version: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", self.name):
            raise ValueError("agent name must be an explicit valid name")
        if not isinstance(self.version, str) or not re.fullmatch(r"[1-9][0-9]*", self.version):
            raise ValueError("agent version must be an explicit positive numeric version, not latest")

    def extra_body(self) -> dict[str, dict[str, str]]:
        return {
            "agent_reference": {
                "type": "agent_reference",
                "name": self.name,
                "version": self.version,
            }
        }
