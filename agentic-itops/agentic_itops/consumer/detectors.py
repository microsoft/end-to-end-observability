from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import math
from statistics import median
from typing import Any

from .contracts import ContractError, Observation, SURFACES, digest, iso, utc


@dataclass(frozen=True)
class DetectorPolicy:
    policy_id: str
    version: str
    surface: str
    record_type: str
    measurement: str | None
    unit: str
    minimum_absolute_change: float
    relative_change: float
    approval_reference: str
    enabled: bool = False
    baseline_seconds: int = 600
    minimum_baseline_samples: int = 10
    firing_samples: int = 3
    recovery_samples: int = 3
    maximum_gap_seconds: int = 90
    maximum_source_age_seconds: int = 180
    maximum_future_skew_seconds: int = 5
    mad_multiplier: float = 6.0
    allowed_quality: tuple[str, ...] = ("good",)
    entity_id: str | None = None
    location_id: str | None = None
    tag_id: str | None = None
    phase: str | None = None

    def __post_init__(self):
        if any(not isinstance(value, str) or not value.strip() for value in
               (self.policy_id, self.version, self.unit)):
            raise ContractError("detector policy identity, version and unit are required")
        if self.surface not in SURFACES or self.record_type not in ("telemetry", "inventory_position"):
            raise ContractError("detector scope is unsupported")
        inventory = self.surface.endswith("-inventory")
        if inventory != (self.record_type == "inventory_position"):
            raise ContractError("detector surface and record family disagree")
        if (inventory and self.measurement is not None) or (
            not inventory and (not isinstance(self.measurement, str) or not self.measurement.strip())
        ):
            raise ContractError("detector measurement must match its record family")
        if type(self.enabled) is not bool or not isinstance(self.approval_reference, str):
            raise ContractError("detector approval configuration is invalid")
        if self.enabled and not self.approval_reference.strip():
            raise ContractError("enabled detector requires an external approval reference")
        for name in ("baseline_seconds", "minimum_baseline_samples", "firing_samples",
                     "recovery_samples", "maximum_gap_seconds", "maximum_source_age_seconds"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ContractError(f"{name} must be a positive integer")
        if self.baseline_seconds >= 7200:
            raise ContractError("baseline cannot consume the entire bounded producer run")
        if type(self.maximum_future_skew_seconds) is not int or self.maximum_future_skew_seconds < 0:
            raise ContractError("future skew allowance must be a nonnegative integer")
        for name in ("minimum_absolute_change", "relative_change", "mad_multiplier"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ContractError(f"{name} must be finite and nonnegative")
        if self.minimum_absolute_change <= 0:
            raise ContractError("an explicitly calibrated positive absolute-change floor is required")
        if (not isinstance(self.allowed_quality, tuple) or not self.allowed_quality
                or any(not isinstance(value, str) or not value for value in self.allowed_quality)):
            raise ContractError("allowed source quality values must be explicit")
        selector = (self.entity_id, self.location_id, self.tag_id, self.phase)
        if any(value is not None for value in selector):
            if (any(not isinstance(value, str) or not value.strip()
                    for value in selector[:2])
                    or any(value is not None and (not isinstance(value, str) or not value.strip())
                           for value in selector[2:])):
                raise ContractError("signal selector requires exact entity/location and optional tag/phase")

    @property
    def sha256(self) -> str:
        values = asdict(self)
        values["allowed_quality"] = list(self.allowed_quality)
        if self.entity_id is None:
            # Preserve hashes of already approved policies without a selector.
            for name in ("entity_id", "location_id", "tag_id", "phase"):
                del values[name]
        return digest(values)

    def matches(self, observation: Observation) -> bool:
        row, payload = observation.row, observation.row["Payload"]
        return (row["Surface"] == self.surface and row["RecordType"] == self.record_type
                and payload.get("measurement") == self.measurement
                and payload.get("unit" if self.record_type == "telemetry" else "uom") == self.unit
                and (self.entity_id is None or (
                    row["EntityId"] == self.entity_id and row["LocationId"] == self.location_id
                    and payload.get("tag_id") == self.tag_id and payload.get("phase") == self.phase)))


def series_key(observation: Observation) -> str:
    row = observation.row
    return digest([observation.signal_key, row["Payload"].get("unit", row["Payload"].get("uom")),
                   row["Context"].get("details_profile")])


def _quality(observation: Observation, policy: DetectorPolicy) -> str | None:
    row = observation.row
    payload = row["Payload"]
    if payload["quality"] not in policy.allowed_quality:
        return "source_quality_not_accepted"
    value = payload["value" if row["RecordType"] == "telemetry" else "quantity"]
    if value is None:
        return "quantity_unavailable"
    if row["RecordType"] == "telemetry":
        age = observation.source_age_seconds
        if age is None:
            return "source_time_unknown"
        if age > policy.maximum_source_age_seconds:
            return "source_stale"
        if age < -policy.maximum_future_skew_seconds:
            return "source_clock_ahead"
    return None


def analyze_series(
    observations: list[Observation], policy: DetectorPolicy,
    *, baseline_start: datetime, reference_time: datetime,
) -> dict[str, Any]:
    start, reference = utc(baseline_start), utc(reference_time)
    baseline_end = start + timedelta(seconds=policy.baseline_seconds)
    if reference <= start or reference - start > timedelta(hours=2):
        raise ContractError("analysis requires a positive absolute window of at most two hours")
    if not observations:
        return {
            "schema_version": "1", "condition": "insufficient_evidence", "reason": "no_observations",
            "policy_sha256": policy.sha256, "episodes": [], "transitions": [],
            "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
        }
    unique: dict[str, Observation] = {}
    for observation in observations:
        if not policy.matches(observation):
            raise ContractError("observation does not match the explicitly selected detector scope/units")
        if (observation.event_key in unique
                and unique[observation.event_key].source_sha256 != observation.source_sha256):
            raise ContractError("conflicting detector evidence for one EventId")
        unique[observation.event_key] = observation
    ordered = sorted(unique.values(), key=lambda item: (utc(item.row["ObservedAt"]), item.row["EventId"]))
    key = series_key(ordered[0])
    if any(series_key(item) != key for item in ordered):
        raise ContractError("a baseline cannot mix identities, producers, profiles or units")
    if any(not start <= utc(item.row["ObservedAt"]) <= reference for item in ordered):
        raise ContractError("detector evidence falls outside the declared analysis window")
    evidence_ids = [item.row["EventId"] for item in ordered]
    points: dict[datetime, Observation] = {}
    for item in ordered:
        at = utc(item.row["ObservedAt"])
        if at in points:
            def sample(observation):
                return {key: value for key, value in observation.row.items()
                        if key not in ("EventId", "TimeGenerated")}
            if digest(sample(points[at])) != digest(sample(item)):
                raise ContractError("conflicting observations at one signal timestamp")
        else:
            points[at] = item
    ordered = list(points.values())
    first = ordered[0].row
    result: dict[str, Any] = {
        "schema_version": "1", "series_key": key,
        "producer_key": ordered[0].producer.key, "run_id": first["RunId"],
        "domain": first["Domain"], "surface": first["Surface"],
        "signal_identity": ordered[0].signal_identity_as_list(),
        "profile": first["Context"].get("details_profile"), "unit": policy.unit,
        "policy_id": policy.policy_id, "policy_version": policy.version, "policy_sha256": policy.sha256,
        "baseline_start": iso(start), "baseline_end": iso(baseline_end), "reference_time": iso(reference),
        "condition": "insufficient_evidence", "reason": "baseline_not_ready",
        "evidence_event_ids": evidence_ids,
        "episodes": [], "transitions": [],
        "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
    }
    if not policy.enabled:
        result.update(condition="unsupported", reason="detector_policy_disabled")
        return result
    baseline = [item for item in ordered if utc(item.row["ObservedAt"]) < baseline_end]
    valid = [item for item in baseline if _quality(item, policy) is None]
    gaps = [start] + [utc(item.row["ObservedAt"]) for item in valid] + [baseline_end]
    if (reference < baseline_end or len(valid) < policy.minimum_baseline_samples
            or len(valid) != len(baseline)
            or any((right - left).total_seconds() > policy.maximum_gap_seconds
                   for left, right in zip(gaps, gaps[1:]))):
        return result
    field = "value" if policy.record_type == "telemetry" else "quantity"
    values = [item.row["Payload"][field] for item in valid]
    if any(abs(value) > 2 ** 53 for value in values):
        raise ContractError("quantity exceeds the detector's supported numeric precision")
    center = median(values)
    mad = median(abs(value - center) for value in values)
    threshold = max(policy.minimum_absolute_change, abs(center) * policy.relative_change,
                    mad * policy.mad_multiplier)
    result["baseline"] = {
        "sample_count": len(valid), "median": center, "mad": mad, "change_threshold": threshold,
        "semantics": "change_from_selected_reference_window_not_an_engineering_limit",
        "evidence_event_ids": [item.row["EventId"] for item in valid],
    }
    active = None
    pending: list[Observation] = []
    recovering: list[Observation] = []
    previous = utc(valid[-1].row["ObservedAt"])
    seen_after_baseline = False
    for observation in ordered:
        row = observation.row
        at = utc(row["ObservedAt"])
        if at < baseline_end:
            continue
        seen_after_baseline = True
        gap = (at - previous).total_seconds() > policy.maximum_gap_seconds
        previous = at
        problem = _quality(observation, policy)
        if gap or problem:
            pending, recovering = [], []
            result.update(condition="data_quality" if problem else "insufficient_evidence",
                          reason=problem or "observation_gap")
            if active is not None and active["observed_state"] != "unknown":
                active["observed_state"] = "unknown"
                result["transitions"].append({
                    "episode_id": active["episode_id"], "kind": "unknown", "at": iso(at),
                    "evidence_event_ids": [row["EventId"]],
                    "reason": problem or "observation_gap",
                })
            if problem:
                continue
        value = row["Payload"][field]
        if abs(value) > 2 ** 53:
            raise ContractError("quantity exceeds the detector's supported numeric precision")
        changed = abs(value - center) >= threshold
        if changed:
            recovering = []
            pending.append(observation)
            if active is None and len(pending) >= policy.firing_samples:
                onset = pending[0].row
                episode_id = digest([key, policy.sha256, onset["EventId"]])
                active = {
                    "episode_id": episode_id, "onset_at": onset["ObservedAt"], "confirmed_at": iso(at),
                    "observed_state": "active", "recovered_at": None,
                    "evidence_event_ids": [item.row["EventId"] for item in pending],
                    "classification": "candidate_anomaly",
                }
                result["episodes"].append(active)
                result["transitions"].append({
                    "episode_id": episode_id, "kind": "fired", "at": iso(at),
                    "evidence_event_ids": list(active["evidence_event_ids"]),
                })
            elif active is not None:
                active["observed_state"] = "active"
                active["evidence_event_ids"].append(row["EventId"])
            result.update(condition="candidate_anomaly" if active is not None else "insufficient_evidence",
                          reason="persistent_reference_change" if active is not None else "persistence_not_met")
        else:
            pending = []
            if active is not None:
                recovering.append(observation)
                active["evidence_event_ids"].append(row["EventId"])
                if len(recovering) >= policy.recovery_samples:
                    active.update(observed_state="recovery_observed", recovered_at=iso(at))
                    result["transitions"].append({
                        "episode_id": active["episode_id"], "kind": "recovery_observed", "at": iso(at),
                        "evidence_event_ids": [item.row["EventId"] for item in recovering],
                    })
                    active, recovering = None, []
                    result.update(condition="normal", reason="reference_range_observed")
                else:
                    result.update(condition="insufficient_evidence", reason="recovery_not_yet_supported")
            else:
                result.update(condition="normal", reason="reference_range_observed")
    if not seen_after_baseline:
        result.update(condition="insufficient_evidence", reason="no_post_baseline_observations")
    if (reference - utc(ordered[-1].row["ObservedAt"])).total_seconds() > policy.maximum_gap_seconds:
        result.update(condition="insufficient_evidence", reason="observations_missing_at_reference_time")
        if active is not None:
            active["observed_state"] = "unknown"
            result["transitions"].append({
                "episode_id": active["episode_id"], "kind": "unknown", "at": iso(reference),
                "evidence_event_ids": [], "reason": "observations_missing_at_reference_time",
            })
    result["analysis_sha256"] = digest(result)
    return result


def analyze_window(
    observations: list[Observation], policies: list[DetectorPolicy],
    *, baseline_start: datetime, reference_time: datetime,
) -> list[dict[str, Any]]:
    if not observations:
        return [{
            "schema_version": "1", "condition": "insufficient_evidence",
            "reason": "no_observations", "episodes": [], "transitions": [],
            "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
        }]
    groups: dict[str, list[Observation]] = {}
    for observation in observations:
        groups.setdefault(series_key(observation), []).append(observation)
    results = []
    for key, group in sorted(groups.items()):
        matching = [policy for policy in policies if policy.matches(group[0])]
        if len(matching) > 1:
            raise ContractError("multiple detector policies match one signal")
        if not matching:
            results.append({
                "schema_version": "1", "series_key": key, "condition": "unsupported",
                "reason": "no_approved_matching_policy", "episodes": [], "transitions": [],
                "evidence_event_ids": sorted({item.row["EventId"] for item in group}),
                "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
            })
        else:
            results.append(analyze_series(group, matching[0], baseline_start=baseline_start,
                                          reference_time=reference_time))
    return results
