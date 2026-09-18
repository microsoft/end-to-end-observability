"""Deterministic fictional compact refining-portfolio simulator."""

from .catalog import (
    CADENCE_ALLOCATION,
    MODULE_DEFINITIONS,
    PRODUCT_FAMILIES,
    SITE_DEFINITIONS,
    project_scheduled_telemetry_count,
)
from .simulator import generate_dataset

__all__ = [
    "CADENCE_ALLOCATION",
    "MODULE_DEFINITIONS",
    "PRODUCT_FAMILIES",
    "SITE_DEFINITIONS",
    "generate_dataset",
    "project_scheduled_telemetry_count",
]
__version__ = "0.1.0"
