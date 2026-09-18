from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import ContractError, Observation, canonical, digest, strict_json, utc


@dataclass(frozen=True)
class IncidentCandidate:
    document: bytes

    @property
    def value(self) -> dict[str, Any]:
        return strict_json(self.document)

    @property
    def sha256(self) -> str:
        return digest(self.value)


def derive_candidates(
    analysis: dict[str, Any], observations: list[Observation],
) -> tuple[IncidentCandidate, ...]:
    if (analysis.get("IsSynthetic") is not True or analysis.get("OperationalUse") != "prohibited"
            or analysis.get("execution_allowed") is not False):
        raise ContractError("detector analysis lacks its immutable non-operation overlay")
    if analysis.get("condition") == "unsupported":
        return ()
    if analysis.get("episodes") and analysis.get("analysis_sha256") != digest(
        {key: value for key, value in analysis.items() if key != "analysis_sha256"}
    ):
        raise ContractError("candidate detector scope/content digest does not match")
    records: dict[str, Observation] = {}
    for observation in observations:
        row = observation.row
        key = row["EventId"]
        if key in records and (
            records[key].producer.key != observation.producer.key
            or records[key].source_sha256 != observation.source_sha256
        ):
            raise ContractError("ambiguous candidate evidence EventId")
        records[key] = observation
    candidates = []
    for episode in analysis.get("episodes", []):
        transitions = [item for item in analysis["transitions"]
                       if item["episode_id"] == episode["episode_id"]]
        ids = sorted(set(analysis["baseline"]["evidence_event_ids"] + episode["evidence_event_ids"]
                         + [event_id for item in transitions for event_id in item["evidence_event_ids"]]))
        if not ids or any(event_id not in records for event_id in ids):
            raise ContractError("candidate evidence is missing")
        selected = [records[event_id] for event_id in ids]
        first = selected[0].row
        for observation in selected:
            row = observation.row
            if (observation.producer.key != analysis["producer_key"]
                    or row["RunId"] != analysis["run_id"] or row["Domain"] != analysis["domain"]
                    or row["Surface"] != analysis["surface"]
                    or observation.signal_identity_as_list() != analysis["signal_identity"]):
                raise ContractError("candidate evidence does not match detector identity/scope")
        source = {
            "producer_key": analysis["producer_key"], "run_id": analysis["run_id"],
            "domain": analysis["domain"], "surfaces": [analysis["surface"]],
        }
        target = {
            "kind": "synthetic_entity", "domain": first["Domain"], "surface": first["Surface"],
            "entity_id": first["EntityId"], "location_id": first["LocationId"],
            "producer_key": analysis["producer_key"],
        }
        target_id = "urn:agentic-itops:synthetic-entity:" + digest(target)
        ordered = sorted(selected, key=lambda item: (utc(item.row["ObservedAt"]), item.row["EventId"]))
        value = {
            "schema_version": "1", "incident_id": episode["episode_id"],
            "incident_fingerprint": episode["episode_id"], "source": source,
            "finding_membership": [episode["episode_id"]], "correlation_basis": "single_supported_episode",
            "observed_lifecycle": episode["observed_state"], "onset_at": episode["onset_at"],
            "confirmed_at": episode["confirmed_at"], "recovered_at": episode["recovered_at"],
            "observed_transitions": transitions,
            "severity": None, "severity_basis": "awaiting_triage",
            "target": {**target, "id": target_id, "component": first["Payload"].get("measurement", "inventory_position")},
            "detector": {
                "policy_id": analysis["policy_id"], "policy_version": analysis["policy_version"],
                "policy_sha256": analysis["policy_sha256"], "analysis_sha256": analysis["analysis_sha256"],
                "baseline": analysis["baseline"], "unit": analysis["unit"],
            },
            "evidence_refs": [{"event_id": item.row["EventId"], "event_key": item.event_key,
                               "source_sha256": item.source_sha256} for item in ordered],
            "observation_projection": "source fields excluding service-assigned TimeGenerated",
            "observations": [{key: value for key, value in item.row.items() if key != "TimeGenerated"}
                             for item in ordered],
            "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
        }
        candidates.append(IncidentCandidate(canonical(value)))
    return tuple(candidates)
