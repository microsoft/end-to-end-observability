"""Deterministic source ZIP for a Linux Functions remote build; never deploys."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentic_itops.consumer.contracts import canonical, strict_json
from agentic_itops.portfolio import compile_portfolio
from agentic_itops.registration import RegistrationPlan, bind_registered_runtime_agents
from agentic_itops.service_runtime import RuntimeConfiguration


def requirements(path, seen=None):
    seen = set() if seen is None else seen
    path = path.resolve()
    if path in seen:
        return []
    seen.add(path)
    output = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r "):
            output.extend(requirements(path.parent / line[3:], seen))
        else:
            output.append(line)
    return list(dict.fromkeys(output))


def package(host, artifacts, output, *, phase="runtime"):
    if host not in {"worker", "tools"}:
        raise ValueError("host must be worker or tools")
    if phase not in {"runtime", "preflight"}:
        raise ValueError("phase must be runtime or preflight")
    portfolio = None
    if phase == "preflight":
        from agentic_itops.service_preflight import validate_probe_contract
        if host != "worker" or set(artifacts) != {"access-probe-contract.json"}:
            raise ValueError("preflight packages require worker host and only the fixed probe contract")
        validate_probe_contract(strict_json(artifacts["access-probe-contract.json"].read_bytes()))
    else:
        config = strict_json(artifacts["runtime.json"].read_bytes())
        RuntimeConfiguration.parse(config)
        bindings = strict_json(artifacts["bindings.json"].read_bytes())
        portfolio = compile_portfolio(ROOT, bindings, mode="trusted_dispatch")
        if not portfolio.bindings_complete:
            raise ValueError("unbound portfolio cannot be packaged as a production service")
    if host == "worker" and phase == "runtime":
        plan = RegistrationPlan(canonical(strict_json(artifacts["registration-plan.json"].read_bytes())).decode())
        receipt = strict_json(artifacts["registration-receipt.json"].read_bytes())
        bind_registered_runtime_agents(portfolio, plan, receipt)
        if plan.document["sampling"] != "model_defaults" or plan.document["model_deployment"] != "gpt-5-6-luna":
            raise ValueError("worker requires the approved exact Luna model-default registration")
    entries = {}
    for directory in ("agentic_itops", "agents", "tools"):
        for path in (ROOT / directory).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix in {
                    ".py", ".json", ".yaml", ".yml", ".md", ".sql", ".html", ".css"}:
                entries[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    for name in ("function_app.py", "host.json"):
        source_name = "preflight_function_app.py" if phase == "preflight" and name == "function_app.py" else name
        entries[name] = (ROOT / "services" / host / source_name).read_bytes()
    profile = "requirements-preflight.txt" if phase == "preflight" else "requirements-functions.txt"
    entries["requirements.txt"] = ("\n".join(requirements(ROOT / profile)) + "\n").encode()
    for name, path in artifacts.items():
        if name not in {"runtime.json", "bindings.json", "registration-plan.json", "registration-receipt.json",
                        "access-probe-contract.json"}:
            raise ValueError("only explicitly named non-secret runtime artifacts may enter the archive")
        directory = ".preflight" if name == "access-probe-contract.json" else ".runtime"
        entries[directory + "/" + name] = canonical(strict_json(path.read_bytes())) + b"\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in sorted(entries.items()):
            info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, content)
    return {"host": host, "archive": str(output), "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "phase": phase, "runtime_enabled": phase == "runtime",
            "portfolio_sha256": portfolio.content_sha256 if portfolio is not None else None,
            "files": len(entries), "live_acceptance": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, choices=("worker", "tools"))
    parser.add_argument("--phase", choices=("runtime", "preflight"), default="runtime")
    parser.add_argument("--runtime-config", type=Path)
    parser.add_argument("--bindings", type=Path)
    parser.add_argument("--probe-contract", type=Path)
    parser.add_argument("--registration-plan", type=Path)
    parser.add_argument("--registration-receipt", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.phase == "preflight":
        if args.host != "worker" or args.probe_contract is None:
            parser.error("preflight requires --host worker and --probe-contract")
        if any(value is not None for value in (args.runtime_config, args.bindings,
                                               args.registration_plan, args.registration_receipt)):
            parser.error("preflight accepts no inference/runtime artifacts")
        artifacts = {"access-probe-contract.json": args.probe_contract}
    else:
        if args.runtime_config is None or args.bindings is None:
            parser.error("runtime requires --runtime-config and --bindings")
        artifacts = {"runtime.json": args.runtime_config, "bindings.json": args.bindings}
        if args.probe_contract is not None:
            artifacts["access-probe-contract.json"] = args.probe_contract
    if args.host == "worker" and args.phase == "runtime":
        if args.registration_plan is None or args.registration_receipt is None:
            parser.error("worker requires --registration-plan and --registration-receipt")
        artifacts.update({"registration-plan.json": args.registration_plan,
                          "registration-receipt.json": args.registration_receipt})
    print(json.dumps(package(args.host, artifacts, args.output, phase=args.phase)))


if __name__ == "__main__":
    main()
