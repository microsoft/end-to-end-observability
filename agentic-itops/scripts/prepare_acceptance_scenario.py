"""Build offline producer-derived evidence; no Azure ingestion, models or approvals."""
from __future__ import annotations

import argparse
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
import sys
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentic_itops.acceptance_scenario import evaluate_series, write_package
from agentic_itops.consumer.contracts import ContractError, ProducerScope, digest, iso, utc

PRODUCER_ROOT = ROOT.parent / "domainsV2"
BUILD_ROOT = ROOT / ".build"
OFFLINE_WORKSPACE = (
    "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/offline-acceptance/"
    "providers/Microsoft.OperationalInsights/workspaces/not-an-azure-capture")


def generate_rows(run_id, start, *, changed):
    if str(PRODUCER_ROOT) not in sys.path:
        sys.path.insert(0, str(PRODUCER_ROOT))
    from energy_domains_v2 import events, replay
    from energy_domains_v2.contracts import domain_for

    model = replay.initial_model("downstream", seed=42)
    rows = []
    for sequence in range(20):
        if changed and sequence == 12:
            model = replay.apply_action("downstream", model, "inject", "analyzer_delay")
        elif changed and sequence == 16:
            model = replay.apply_action("downstream", model, "recover")
        at = iso(start + timedelta(minutes=sequence))
        frame = replay.sample("downstream", model, sequence, at)
        model = frame["model"]
        rows.extend(events.observations(
            frame, {"run_id": run_id, "surface": "downstream", "sequence": sequence},
            domain_for("downstream"), at))
    return rows


def build(run_id, start, output):
    run = UUID(run_id)
    if run.int == 0 or str(run) != run_id:
        raise ContractError("explicit canonical non-placeholder run UUID required")
    start = utc(start)
    output = Path(output).resolve()
    if not output.is_relative_to(BUILD_ROOT) or output == BUILD_ROOT:
        raise ContractError("offline output must be a new subdirectory inside .build")
    if output.exists():
        raise FileExistsError("output exists; verify it or choose a new directory, never overwrite")
    source_paths = (
        "energy_domains_v2\\replay.py", "energy_domains_v2\\events.py",
        "energy_domains_v2\\contracts.py", "energy_domains_v2\\replay_data\\manifest.json",
        "contracts\\energy-observations.v1.schema.json",
    )
    sources = {name: sha256((PRODUCER_ROOT / name).read_bytes()).hexdigest() for name in source_paths}
    consumer_paths = (
        "scripts\\prepare_acceptance_scenario.py", "agentic_itops\\acceptance_scenario.py",
        "agentic_itops\\consumer\\contracts.py", "agentic_itops\\consumer\\detectors.py",
        "agentic_itops\\consumer\\incidents.py",
        "agentic_itops\\consumer\\contracts\\energy-observations.v1.schema.json",
    )
    consumer_sources = {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in consumer_paths}
    normal_run = str(uuid5(run, "offline-normal-control"))
    producer = ProducerScope(OFFLINE_WORKSPACE)
    observations, changed_proof = evaluate_series(
        generate_rows(run_id, start, changed=True), producer, run_id, start, changed=True)
    control, normal_proof = evaluate_series(
        generate_rows(normal_run, start, changed=False), producer, normal_run, start, changed=False)
    proof = {
        "schema_version": 1, "scenario": "synthetic-downstream-analyzer-data-degradation",
        "producer_control": "analyzer_delay", "seed": 42,
        "run_id": run_id, "normal_control_run_id": normal_run,
        "start_utc": iso(start), "end_utc": iso(start + timedelta(minutes=20)),
        "cadence_seconds": 60, "baseline_samples": 12, "changed_samples": 4, "recovery_samples": 4,
        "producer_files_sha256": sources, "consumer_files_sha256": consumer_sources,
        "offline_producer_key": producer.key,
        "sequence_semantics": "global monotonic sequence; pure replay phase is sequence modulo phase_count",
        "changed": changed_proof, "normal_control": normal_proof,
        "model_input_rule": "Only neutral observations; never this evaluator artifact or expected outcomes.",
        "limitations": [
            "Generated locally from the packaged producer; not an Azure query or committed ingestion receipt.",
            "Numeric reference change and observed recovery, not engineering limits or a real incident.",
            "No authorized ARM mapping, customer impact, intervention, approved knowledge or human acceptance.",
            "Disabled rollout policy; live run binding, exact policy approval and model admission remain required.",
        ],
        "azure_mutation_performed": False, "IsSynthetic": True,
        "OperationalUse": "prohibited", "execution_allowed": False,
    }
    if sources != {name: sha256((PRODUCER_ROOT / name).read_bytes()).hexdigest() for name in source_paths}:
        raise ContractError("producer source changed during evidence generation")
    if consumer_sources != {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in consumer_paths}:
        raise ContractError("consumer source changed during evidence generation")
    write_package(output, observations, control, proof)
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--start", required=True, help="Explicit UTC observation start, including Z")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        proof = build(args.run_id, args.start, args.output)
    except (ContractError, ValueError, OSError) as exc:
        print(f"Offline acceptance preparation stopped: {exc}", file=sys.stderr)
        return 2
    print(f"Offline evidence verified: changed={proof['changed']['candidate_count']}, "
          f"normal={proof['normal_control']['candidate_count']}; proof SHA256={digest(proof)}")
    print(f"Saved to {args.output.resolve()}. Not ingested, deployed, model-reviewed or approved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
