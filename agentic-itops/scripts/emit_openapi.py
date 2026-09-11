"""Emit OpenAPI 3.0 specs for the code-hosted skills.

Foundry binds OpenAPI-described endpoints directly as agent tools, so
these files are the contract between the prompt-hosted agents and the
deterministic code. Generating them keeps the descriptions honest --
the `description` fields are what the model actually reads when
deciding whether and how to call a tool, so they are written for that
audience rather than for a human API consumer.

Run:  python scripts/emit_openapi.py
"""

from __future__ import annotations

from pathlib import Path

import yaml

OUT = Path(__file__).resolve().parents[1] / "tools" / "openapi"
SERVER = {"url": "${AGENTIC_ITOPS_SKILL_BASE_URL}", "description": "Azure Function host for the code skills"}


def base(title: str, description: str) -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": title, "version": "1.0.0", "description": description},
        "servers": [SERVER],
        "paths": {},
        "components": {"schemas": {}},
    }


def signal_normalizer() -> dict:
    spec = base(
        "Signal Normalizer",
        "Normalizes heterogeneous alert payloads into canonical incident "
        "envelopes and correlates them by fingerprint. Deterministic.",
    )
    spec["paths"]["/normalize"] = {
        "post": {
            "operationId": "normalizeSignals",
            "summary": "Normalize and correlate a batch of raw alert payloads",
            "description": (
                "Accepts raw payloads from Azure Monitor, Datadog, Splunk, "
                "Prometheus Alertmanager or ServiceNow and returns canonical "
                "incidents. Signals describing the same symptom on the same "
                "resource collapse into ONE incident even when they came from "
                "different tools. Treat each returned incident as a single "
                "problem regardless of how many signals it contains."
            ),
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["payloads"],
                            "properties": {
                                "payloads": {
                                    "type": "array",
                                    "items": {"type": "object"},
                                    "description": "Raw alert payloads, any supported source.",
                                }
                            },
                        }
                    }
                },
            },
            "responses": {
                "200": {
                    "description": "Canonical incidents plus a normalization report",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "incidents": {"type": "array", "items": {"type": "object"}},
                                    "report": {"type": "object"},
                                    "failures": {"type": "array", "items": {"type": "object"}},
                                },
                            }
                        }
                    },
                }
            },
        }
    }
    return spec


def change_safety_gate() -> dict:
    spec = base(
        "Change Safety Gate",
        "Authorization boundary for agent-initiated change. Deterministic "
        "policy evaluation. This endpoint decides; callers do not.",
    )
    spec["paths"]["/evaluate"] = {
        "post": {
            "operationId": "evaluateChangeRequest",
            "summary": "Decide whether a proposed action is permitted",
            "description": (
                "Evaluates a proposed remediation against policy and returns "
                "allow, deny or escalate with an audit record. THIS DECISION IS "
                "FINAL. Do not attempt to work around a denial, do not re-submit "
                "a denied request with altered wording, and do not present a "
                "denial to a user as a caveat or a warning -- report it as the "
                "refusal it is. A deny result means the action must not be taken."
            ),
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["action", "resource_id"],
                            "properties": {
                                "action": {
                                    "type": "string",
                                    "description": "One verb from the allowed action list.",
                                },
                                "resource_id": {"type": "string", "description": "ARM resource id."},
                                "environment": {
                                    "type": "string",
                                    "enum": ["prod", "staging", "test", "dev", "unknown"],
                                },
                                "service": {"type": "string"},
                                "estimated_affected": {
                                    "type": "integer",
                                    "description": "How many resources the action would touch.",
                                    "default": 1,
                                },
                                "incident_fingerprint": {"type": "string"},
                                "justification": {"type": "string"},
                            },
                        }
                    }
                },
            },
            "responses": {
                "200": {
                    "description": "The decision, with reasons and an audit id",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["effect", "audit_id"],
                                "properties": {
                                    "effect": {
                                        "type": "string",
                                        "enum": ["allow", "deny", "escalate"],
                                    },
                                    "approval_tier": {"type": "string"},
                                    "reasons": {"type": "array", "items": {"type": "string"}},
                                    "matched_rules": {"type": "array", "items": {"type": "string"}},
                                    "audit_id": {"type": "string"},
                                    "policy_version": {"type": "string"},
                                },
                            }
                        }
                    },
                }
            },
        }
    }
    return spec


def log_analytics() -> dict:
    spec = base(
        "Log Analytics Query",
        "Read-only KQL execution against the customer's Log Analytics workspace.",
    )
    spec["paths"]["/query"] = {
        "post": {
            "operationId": "runKqlQuery",
            "summary": "Run a KQL query",
            "description": (
                "Executes a read-only KQL query. Returns columns and rows. "
                "An empty result set is a real answer and should be reported as "
                "such -- do not invent rows, and do not assume an empty result "
                "means the query was wrong."
            ),
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["query"],
                            "properties": {
                                "query": {"type": "string", "description": "KQL text."},
                                "timespan": {
                                    "type": "string",
                                    "description": "ISO-8601 duration, e.g. PT1H or P1D.",
                                    "default": "PT1H",
                                },
                            },
                        }
                    }
                },
            },
            "responses": {
                "200": {
                    "description": "Query result",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "columns": {"type": "array", "items": {"type": "string"}},
                                    "rows": {"type": "array", "items": {"type": "array"}},
                                    "row_count": {"type": "integer"},
                                },
                            }
                        }
                    },
                }
            },
        }
    }
    return spec


def resource_graph() -> dict:
    spec = base(
        "Resource Context",
        "Resource metadata, dependencies, criticality and ownership.",
    )
    spec["paths"]["/resource"] = {
        "get": {
            "operationId": "getResourceContext",
            "summary": "Get context for a resource",
            "description": (
                "Returns metadata, tags, upstream and downstream dependencies, "
                "ownership and criticality. Check `criticality` before proposing "
                "any action: a resource marked safety-critical or sitting in an "
                "OT zone is outside agent authority regardless of severity."
            ),
            "parameters": [
                {
                    "name": "resource_id",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string"},
                    "description": "ARM resource id, or bare resource name.",
                }
            ],
            "responses": {
                "200": {
                    "description": "Resource context; `found` is false when unknown",
                    "content": {"application/json": {"schema": {"type": "object"}}},
                }
            },
        }
    }
    return spec


def change_history() -> dict:
    spec = base(
        "Change History",
        "Recent changes touching a resource or service. The highest-yield "
        "question in incident response.",
    )
    spec["paths"]["/changes"] = {
        "get": {
            "operationId": "getRecentChanges",
            "summary": "List recent changes for a resource or service",
            "description": (
                "Returns deployments, configuration writes and infrastructure "
                "changes, most recent first, each with a correlation hint. Call "
                "this BEFORE querying telemetry. A change landing after symptom "
                "onset is not the cause, however close the timestamps look."
            ),
            "parameters": [
                {"name": "resource_id", "in": "query", "schema": {"type": "string"}},
                {"name": "service", "in": "query", "schema": {"type": "string"}},
                {
                    "name": "hours",
                    "in": "query",
                    "schema": {"type": "integer", "default": 24},
                    "description": "Lookback window.",
                },
            ],
            "responses": {
                "200": {
                    "description": "Changes, most recent first",
                    "content": {"application/json": {"schema": {"type": "object"}}},
                }
            },
        }
    }
    return spec


def alert_hygiene() -> dict:
    spec = base(
        "Alert Hygiene Analysis",
        "Noise scoring over alert history. Produces a tuning backlog.",
    )
    spec["paths"]["/analyze"] = {
        "post": {
            "operationId": "analyzeAlertHygiene",
            "summary": "Score alert rules for noise and produce a tuning backlog",
            "description": (
                "Computes firing volume, self-resolve rate, flap ratio and a "
                "weighted noise score per rule over the supplied alert history. "
                "Runs entirely on exported history -- no live incident and no "
                "production access required. Note the limitation: this shows what "
                "fired and what resolved, not what an engineer did in response."
            ),
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["payloads"],
                            "properties": {
                                "payloads": {"type": "array", "items": {"type": "object"}}
                            },
                        }
                    }
                },
            },
            "responses": {
                "200": {
                    "description": "Ranked findings with rationale",
                    "content": {"application/json": {"schema": {"type": "object"}}},
                }
            },
        }
    }
    return spec


SPECS = {
    "signal-normalizer.yaml": signal_normalizer,
    "change-safety-gate.yaml": change_safety_gate,
    "log-analytics.yaml": log_analytics,
    "resource-graph.yaml": resource_graph,
    "change-history.yaml": change_history,
    "alert-hygiene.yaml": alert_hygiene,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for filename, builder in SPECS.items():
        path = OUT / filename
        path.write_text(
            yaml.safe_dump(builder(), sort_keys=False, width=92, default_flow_style=False),
            encoding="utf-8",
        )
        print(f"  wrote {path.relative_to(OUT.parents[1])}")


if __name__ == "__main__":
    main()
