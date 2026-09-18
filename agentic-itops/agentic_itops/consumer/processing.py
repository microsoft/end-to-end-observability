from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .contracts import ContractError, Observation, ProducerScope, SURFACES, canonical, iso, utc
from .detectors import DetectorPolicy, analyze_window
from .incidents import derive_candidates
from .logs import CompleteBatch
from .storage_contract import EvidenceRef, EvidenceStore


@dataclass(frozen=True)
class AnalysisScope:
    producer: ProducerScope
    run_id: str
    surfaces: tuple[str, ...]
    baseline_start: datetime
    reference_time: datetime
    ingestion_start: datetime
    ingestion_end: datetime

    def __post_init__(self):
        if not isinstance(self.run_id, str) or not self.run_id or len(self.run_id) > 256:
            raise ContractError("an explicit bounded analysis RunId is required")
        if (not isinstance(self.surfaces, tuple) or not self.surfaces
                or len(set(self.surfaces)) != len(self.surfaces)
                or any(surface not in SURFACES for surface in self.surfaces)):
            raise ContractError("explicit analysis surfaces are required")
        for field in ("baseline_start", "reference_time", "ingestion_start", "ingestion_end"):
            object.__setattr__(self, field, utc(getattr(self, field)))
        if not timedelta(0) < self.reference_time - self.baseline_start <= timedelta(hours=2):
            raise ContractError("analysis observation bounds must be positive and at most two hours")
        if not timedelta(0) < self.ingestion_end - self.ingestion_start <= timedelta(days=31):
            raise ContractError("analysis ingestion bounds must be positive and at most 31 days")


def process_snapshot(
    scope: AnalysisScope, references: tuple[EvidenceRef, ...],
    evidence: EvidenceStore, policies: list[DetectorPolicy],
) -> dict:
    batches = [CompleteBatch.from_document(evidence.read(reference)) for reference in references]
    for batch in batches:
        if batch.producer.key != scope.producer.key or batch.window.run_id != scope.run_id:
            raise ContractError("analysis evidence crosses its producer/RunId boundary")
    selected = [(reference, batch) for reference, batch in zip(references, batches)
                if batch.window.observed_start <= scope.baseline_start
                and batch.window.observed_end >= scope.reference_time
                and set(scope.surfaces).issubset(batch.window.surfaces)
                and batch.window.ingestion_start < scope.ingestion_end
                and batch.window.ingestion_end > scope.ingestion_start]
    intervals = sorted((max(batch.window.ingestion_start, scope.ingestion_start),
                        min(batch.window.ingestion_end, scope.ingestion_end)) for _, batch in selected)
    cursor = scope.ingestion_start
    missing = []
    for start, end in intervals:
        if start > cursor:
            missing.append({"start": iso(cursor), "end": iso(start)})
        cursor = max(cursor, end)
    if cursor < scope.ingestion_end:
        missing.append({"start": iso(cursor), "end": iso(scope.ingestion_end)})
    report = {
        "schema_version": "1", "producer_key": scope.producer.key, "run_id": scope.run_id,
        "surfaces": list(scope.surfaces), "baseline_start": iso(scope.baseline_start),
        "reference_time": iso(scope.reference_time),
        "ingestion_start": iso(scope.ingestion_start), "ingestion_end": iso(scope.ingestion_end),
        "coverage": "incomplete" if missing else "complete_as_queried",
        "missing_ingestion_intervals": missing,
        "source_evidence": [{"key": ref.key, "sha256": ref.sha256, "byte_length": ref.byte_length}
                            for ref, _ in selected],
        "analyses": [], "candidates": [], "surface_coverage": [],
        "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
    }
    if missing:
        report["condition"] = "insufficient_evidence"
        report["reason"] = "ingestion_window_not_fully_covered"
        return report
    unique: dict[str, Observation] = {}
    for _, batch in selected:
        for record in batch.records:
            observation, row = record.observation, record.observation.row
            if (row["Surface"] not in scope.surfaces
                    or not scope.baseline_start <= utc(row["ObservedAt"]) < scope.reference_time
                    or not scope.ingestion_start <= record.ingested_at < scope.ingestion_end):
                continue
            key = observation.event_key
            if key in unique and unique[key].source_sha256 != observation.source_sha256:
                raise ContractError("complete revisions disagree about a source event")
            if key not in unique or utc(row["TimeGenerated"]) < utc(unique[key].row["TimeGenerated"]):
                unique[key] = observation
    observations = sorted(unique.values(), key=lambda item: (utc(item.row["ObservedAt"]), item.event_key))
    for surface in scope.surfaces:
        members = [item for item in observations if item.row["Surface"] == surface]
        report["surface_coverage"].append({
            "surface": surface, "observation_count": len(members),
            "condition": "observations_present" if members else "insufficient_evidence",
            "reason": "observed_records_do_not_prove_all_expected_assets_reported" if members else "no_observations",
        })
    report["analyses"] = analyze_window(observations, policies, baseline_start=scope.baseline_start,
                                        reference_time=scope.reference_time)
    for analysis in report["analyses"]:
        for candidate in derive_candidates(analysis, observations):
            reference = evidence.put(candidate.document)
            if evidence.read(reference) != candidate.document:
                raise ContractError("candidate evidence failed its read-after-write verification")
            report["candidates"].append({
                "incident_id": candidate.value["incident_id"],
                "evidence": {"key": reference.key, "sha256": reference.sha256,
                             "byte_length": reference.byte_length},
            })
    report["condition"] = "analyzed" if observations else "insufficient_evidence"
    report["reason"] = "explicit_policy_evaluation" if observations else "no_observations"
    return report


def persist_snapshot(report: dict, evidence: EvidenceStore) -> EvidenceRef:
    content = canonical(report)
    reference = evidence.put(content)
    if evidence.read(reference) != content:
        raise ContractError("analysis evidence failed its read-after-write verification")
    return reference
