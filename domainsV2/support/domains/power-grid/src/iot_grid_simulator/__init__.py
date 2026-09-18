"""Deterministic synthetic electric-distribution IoT data generation."""

from .config import ConfigError, load_config, validate_config
from .simulator import generate_dataset

__all__ = ["ConfigError", "generate_dataset", "load_config", "validate_config"]
__version__ = "0.6.0"
