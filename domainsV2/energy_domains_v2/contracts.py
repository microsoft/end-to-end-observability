"""Shared adapter types; simulation engines remain owned by their V1 domains."""

from typing import Any, TypedDict


class Observation(TypedDict):
    record_type: str
    entity_id: str
    location_id: str
    simulated_at: str
    payload: dict[str, Any]
    context: dict[str, Any]


class Frame(TypedDict):
    model: dict[str, Any]
    snapshot: dict[str, Any]
    observations: list[Observation]


class Scenario(TypedDict):
    id: str
    label: str
    description: str


MONITORING_SURFACES = (
    "power-grid", "upstream", "midstream", "downstream", "downstream-portfolio",
)
INVENTORY_SURFACES = (
    "power-grid-inventory", "upstream-inventory",
    "midstream-inventory", "downstream-inventory",
)
SURFACES = MONITORING_SURFACES + INVENTORY_SURFACES


def domain_for(surface: str) -> str:
    if surface not in SURFACES:
        raise ValueError("Unknown surface")
    if surface == "downstream-portfolio":
        return "downstream"
    return surface.removesuffix("-inventory")
