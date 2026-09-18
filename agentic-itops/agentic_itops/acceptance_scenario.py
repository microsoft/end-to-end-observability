"""Offline numeric acceptance evidence; never a runtime approval or ingestion receipt."""
from dataclasses import asdict, replace
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from .consumer.contracts import ContractError, Observation, canonical, digest, iso, utc
from .consumer.detectors import DetectorPolicy, analyze_window
from .consumer.incidents import derive_candidates


def scenario_policy():
    return DetectorPolicy(
        policy_id="synthetic-analyzer-availability", version="1",
        surface="downstream", record_type="telemetry",
        measurement="availability_index", unit="index",
        minimum_absolute_change=0.02, relative_change=0.02,
        baseline_seconds=720, minimum_baseline_samples=12,
        approval_reference="", enabled=False,
        entity_id="asset-0ccfac117cb4", location_id="A06", tag_id="tag-2ee196932b31")


def evaluate_series(rows, producer, run_id, start, *, changed):
    """Check every supplied producer row before selecting one calibrated identity."""
    start = utc(start)
    try:
        identity = UUID(run_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ContractError("a distinct explicit run UUID is required") from exc
    if identity.int == 0 or str(identity) != run_id:
        raise ContractError("a non-placeholder canonical run UUID is required")
    if not isinstance(rows, list) or not 20 <= len(rows) <= 100_000:
        raise ContractError("bounded complete replay rows required")
    policy = scenario_policy()
    selected, identities = [], set()
    for row in rows:
        observation = Observation.parse(producer, row)
        if (row["RunId"] != run_id or row["Surface"] != policy.surface
                or not start <= utc(row["ObservedAt"]) < start + timedelta(minutes=20)):
            raise ContractError("replay observation is outside its explicit run/surface/window")
        if observation.event_key in identities:
            raise ContractError("duplicate or conflicting observation identity")
        identities.add(observation.event_key)
        if policy.matches(observation):
            selected.append(observation)
    selected.sort(key=lambda item: utc(item.row["ObservedAt"]))
    if (len(selected) != 20
            or [utc(item.row["ObservedAt"]) for item in selected]
            != [start + timedelta(minutes=index) for index in range(20)]):
        raise ContractError("exactly one target observation per minute is required")
    if any(item.row["Payload"]["quality"] != "good" or item.source_age_seconds != 0
           for item in selected):
        raise ContractError("acceptance series must have fresh good-quality observations")
    offline = replace(policy, enabled=True, approval_reference="offline-evaluation-only-not-live-approval")
    analyses = analyze_window(selected, [offline], baseline_start=start,
                              reference_time=start + timedelta(minutes=20))
    if len(analyses) != 1:
        raise ContractError("acceptance selector must resolve exactly one signal")
    analysis = analyses[0]
    candidates = derive_candidates(analysis, selected)
    expected = [("fired", iso(start + timedelta(minutes=14))),
                ("recovery_observed", iso(start + timedelta(minutes=18)))] if changed else []
    actual = [(item["kind"], item["at"]) for item in analysis["transitions"]]
    if (actual != expected or len(candidates) != int(changed)
            or analysis["condition"] != "normal"
            or analysis.get("baseline", {}).get("sample_count") != 12):
        raise ContractError("replay failed measured baseline/onset/recovery acceptance")
    if changed and (candidates[0].value["onset_at"] != iso(start + timedelta(minutes=12))
                    or candidates[0].value["observed_lifecycle"] != "recovery_observed"):
        raise ContractError("candidate onset/recovery does not match the neutral observations")
    disabled = analyze_window(selected, [policy], baseline_start=start,
                              reference_time=start + timedelta(minutes=20))
    if any(item["episodes"] for item in disabled):
        raise ContractError("disabled rollout policy unexpectedly generated an episode")
    return [item.row for item in selected], {
        "status": "offline_detector_verified",
        "source_row_count": len(rows), "matching_row_count": len(selected),
        "source_rows_sha256": digest(rows),
        "selected_rows_sha256": digest([item.row for item in selected]),
        "policy_sha256": offline.sha256,
        "analysis": analysis,
        "candidate_count": len(candidates),
        "candidate_sha256": candidates[0].sha256 if candidates else None,
        "disabled_policy_candidate_count": 0,
        "azure_ingestion_performed": False, "model_calls": 0, "embedding_calls": 0,
        "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
    }


def write_package(output, observations, normal_observations, proof):
    """A new directory is mandatory; a partial package is never silently replayed."""
    output = Path(output)
    policy = asdict(scenario_policy())
    policy["allowed_quality"] = list(policy["allowed_quality"])
    contents = {
        "observations.jsonl": b"".join(canonical(row) + b"\n" for row in observations),
        "normal-control.jsonl": b"".join(canonical(row) + b"\n" for row in normal_observations),
        "detector-policy.disabled.json": canonical(policy) + b"\n",
        "evaluator-only.json": canonical(proof) + b"\n",
    }
    contents["package-integrity.json"] = canonical({
        "status": "offline_artifacts_only_not_ingested_or_approved",
        "files": {name: {"sha256": sha256(content).hexdigest(), "bytes": len(content)}
                  for name, content in contents.items()},
        "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
    }) + b"\n"
    output.mkdir(parents=True, exist_ok=False)
    for name, content in contents.items():
        with (output / name).open("xb") as handle:
            handle.write(content)
