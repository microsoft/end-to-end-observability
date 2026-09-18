"""Generate schema examples from frozen replay observations, without running a demo."""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

from energy_domains_v2 import events, replay
from energy_domains_v2.contracts import domain_for


OUTPUT = Path(__file__).resolve().parents[1] / "contracts" / "energy-observations.v1.examples.jsonl"
ANCHOR = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def contract_frames():
    case = 0
    for surface in sorted(replay.SURFACES):
        scenarios = ["normal"] + [entry["id"] for entry in replay.scenarios(surface)]
        for scenario in scenarios:
            model = replay.initial_model(surface)
            if scenario != "normal":
                model = replay.apply_action(surface, model, "inject", scenario)
            run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"energy-v2-contract-example-{case}"))
            case += 1
            for phase in range(replay._spec(surface)["phase_count"]):
                at = (ANCHOR + timedelta(seconds=60 * phase)).isoformat().replace("+00:00", "Z")
                frame = replay.sample(surface, model, phase, at)
                state = {"run_id": run_id, "sequence": phase, "surface": surface}
                rows = events.observations(frame, state, domain_for(surface), at)
                yield surface, scenario, phase, rows


def example_bytes():
    selected = {}
    covered = set()
    for surface, _, _, records in contract_frames():
        for row in records:
            payload = row["Payload"]
            value_key = "quantity" if row["Portal"] == "inventory" else "value"
            kinds = [("surface", surface)]
            if value_key in payload:
                value = payload[value_key]
                if value is None:
                    kinds.append(("null", row["Portal"]))
                elif value == 0:
                    kinds.append(("zero", row["Portal"]))
            if payload.get("quality") == "stale":
                kinds.append(("stale", row["Portal"]))
            if payload.get("quality") == "deenergized":
                kinds.append(("deenergized", row["Portal"]))
            if any(kind not in covered for kind in kinds):
                selected[row["EventId"]] = row
                covered.update(kinds)
    return b"".join(events.encoded(row) + b"\n" for row in selected.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="Regenerate the named JSONL example file")
    mode.add_argument("--check", action="store_true", help="Check the existing example bytes without writing")
    args = parser.parse_args()
    data = example_bytes()
    if args.write:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(data)
        count = data.count(b"\n")
        print(f"Wrote {count} offline observation examples")
    elif OUTPUT.read_bytes() != data:
        raise SystemExit("Contract examples differ; regenerate explicitly with --write")
    else:
        print("Contract examples match the packaged replay fixtures")


if __name__ == "__main__":
    main()
