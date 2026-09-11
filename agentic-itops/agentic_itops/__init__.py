"""Agentic ITOps -- Foundry agent and skill portfolio.

Reference implementation of the agents and skills described in
``docs/architecture.md``. Code-hosted skills (normalizer, safety gate,
hygiene analyst) are real and tested. Prompt-hosted agents ship as
instruction sets plus declarative definitions under ``agents/``.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .envelope import (
    IncidentEnvelope,
    MonitorCondition,
    Severity,
    SignalType,
    SymptomClass,
    compute_fingerprint,
)
from .hygiene import analyze as analyze_hygiene
from .normalizer import (
    Incident,
    correlate,
    normalize_and_correlate,
    normalize_many,
    normalize_one,
)
from .safety_gate import ChangeRequest, ChangeSafetyGate, Decision, Effect

__all__ = [
    "__version__",
    "IncidentEnvelope",
    "Severity",
    "SymptomClass",
    "SignalType",
    "MonitorCondition",
    "compute_fingerprint",
    "Incident",
    "normalize_one",
    "normalize_many",
    "correlate",
    "normalize_and_correlate",
    "ChangeSafetyGate",
    "ChangeRequest",
    "Decision",
    "Effect",
    "analyze_hygiene",
]
