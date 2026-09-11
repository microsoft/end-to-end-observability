"""Symptom classification and severity mapping.

Shared by every adapter so that classification is consistent no matter
which tool the signal came from. Rule-based and deterministic by design:
a probabilistic classifier here would make the fingerprint unstable, and
an unstable fingerprint breaks deduplication.
"""

from __future__ import annotations

import re

from .envelope import Severity, SymptomClass

# Ordered. First match wins, so put the specific patterns above the
# general ones -- "disk queue" must classify as saturation, not capacity.
_SYMPTOM_PATTERNS: tuple[tuple[SymptomClass, tuple[str, ...]], ...] = (
    (
        SymptomClass.SECURITY,
        (r"unauthori[sz]ed", r"\bauth\w* fail", r"privilege", r"intrusion", r"malware",
         r"suspicious", r"policy violation", r"certificate expir", r"\bcve\b"),
    ),
    (
        SymptomClass.AVAILABILITY,
        (r"\bdown\b", r"unavailab", r"unreachab", r"heartbeat", r"health ?check",
         r"\bup\b ?=", r"not responding", r"offline", r"outage", r"instance.*terminat"),
    ),
    (
        SymptomClass.CONNECTIVITY,
        (r"connection", r"timeout", r"\bdns\b", r"packet loss", r"network in",
         r"network out", r"tls handshake", r"socket", r"refused"),
    ),
    (
        SymptomClass.ERROR_RATE,
        (r"5xx", r"4xx", r"error rate", r"failed request", r"exception",
         r"fault", r"\berrors?\b", r"failure rate"),
    ),
    (
        SymptomClass.LATENCY,
        (r"latency", r"response time", r"slow", r"duration", r"\bp9[59]\b", r"time to first byte"),
    ),
    (
        SymptomClass.SATURATION,
        (r"\bcpu\b", r"memory", r"queue (depth|length)", r"thread pool", r"connection pool",
         r"throttl", r"backlog", r"saturation", r"utilization", r"utilisation"),
    ),
    (
        SymptomClass.CAPACITY,
        (r"disk (space|full|free)", r"storage", r"quota", r"capacity", r"free space", r"volume full"),
    ),
    (
        SymptomClass.DATA_QUALITY,
        (r"stale", r"no data", r"missing data", r"ingestion", r"schema", r"data drift",
         r"telemetry gap", r"late arriv"),
    ),
    (
        SymptomClass.CONFIGURATION,
        (r"config", r"drift", r"non[- ]?compliant", r"baseline", r"policy state",
         r"deployment fail", r"misconfigur"),
    ),
)


def classify_symptom(*texts: str) -> SymptomClass:
    """Classify from any combination of title, rule name, metric name.

    Pass the most specific text first; earlier arguments win ties.
    """
    for text in texts:
        if not text:
            continue
        haystack = text.strip().lower()
        for symptom, patterns in _SYMPTOM_PATTERNS:
            for pattern in patterns:
                if re.search(pattern, haystack):
                    return symptom
    return SymptomClass.UNKNOWN


# --- severity mapping -------------------------------------------------

_AZURE_SEVERITY = {
    "sev0": Severity.CRITICAL,
    "sev1": Severity.HIGH,
    "sev2": Severity.MEDIUM,
    "sev3": Severity.LOW,
    "sev4": Severity.INFO,
}

_WORD_SEVERITY = {
    "critical": Severity.CRITICAL,
    "crit": Severity.CRITICAL,
    "fatal": Severity.CRITICAL,
    "emergency": Severity.CRITICAL,
    "page": Severity.CRITICAL,
    "error": Severity.HIGH,
    "high": Severity.HIGH,
    "major": Severity.HIGH,
    "warn": Severity.MEDIUM,
    "warning": Severity.MEDIUM,
    "medium": Severity.MEDIUM,
    "minor": Severity.LOW,
    "low": Severity.LOW,
    "info": Severity.INFO,
    "information": Severity.INFO,
    "informational": Severity.INFO,
    "ok": Severity.INFO,
    "success": Severity.INFO,
    "debug": Severity.INFO,
    "none": Severity.INFO,
}

# Splunk / ServiceNow numeric scales, where 1 is most urgent.
_NUMERIC_SEVERITY = {
    1: Severity.CRITICAL,
    2: Severity.HIGH,
    3: Severity.MEDIUM,
    4: Severity.LOW,
    5: Severity.INFO,
}


def map_severity(value: object, default: Severity = Severity.MEDIUM) -> Severity:
    """Map any vendor severity representation onto the canonical scale."""
    if value is None:
        return default
    if isinstance(value, Severity):
        return value
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return _NUMERIC_SEVERITY.get(int(value), default)

    text = str(value).strip().lower()
    if not text:
        return default
    if text in _AZURE_SEVERITY:
        return _AZURE_SEVERITY[text]
    if text in _WORD_SEVERITY:
        return _WORD_SEVERITY[text]
    if text.isdigit():
        return _NUMERIC_SEVERITY.get(int(text), default)
    # Tolerate "Sev 2", "severity_1", "P1"
    match = re.search(r"(?:sev|severity|p)[^0-9]*([0-9])", text)
    if match:
        digit = int(match.group(1))
        if f"sev{digit}" in _AZURE_SEVERITY and "sev" in text:
            return _AZURE_SEVERITY[f"sev{digit}"]
        return _NUMERIC_SEVERITY.get(digit if digit > 0 else 1, default)
    return default


# --- environment inference -------------------------------------------

_ENV_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("prod", (r"\bprod\b", r"\bproduction\b", r"\bprd\b", r"-p\b")),
    ("staging", (r"\bstag", r"\bstg\b", r"\bpreprod\b", r"\buat\b")),
    ("test", (r"\btest\b", r"\bqa\b", r"\btst\b")),
    ("dev", (r"\bdev\b", r"\bdevelopment\b", r"\bsandbox\b")),
)


def infer_environment(*texts: str, explicit: str = "") -> str:
    """Infer environment from tags, resource group, or resource name.

    An explicit tag always wins. Inference is a fallback and is flagged
    in normalization warnings by the caller when it matters, because
    guessing "prod" wrong in either direction has real consequences at
    the Change-Safety Gate.
    """
    if explicit:
        normalized = explicit.strip().lower()
        for env, _ in _ENV_PATTERNS:
            if normalized.startswith(env[:3]):
                return env
        return normalized
    for text in texts:
        if not text:
            continue
        haystack = text.strip().lower()
        for env, patterns in _ENV_PATTERNS:
            for pattern in patterns:
                if re.search(pattern, haystack):
                    return env
    return "unknown"
