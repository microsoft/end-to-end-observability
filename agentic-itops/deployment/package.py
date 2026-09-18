"""Deterministic source-only Linux ZIPs; never archive the workspace wholesale."""

from __future__ import annotations

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[1]
SERVICES = ("review", "worker", "worker-preflight", "tools", "publisher", "bootstrap")
PROFILES = {
    "review": "requirements-web.txt",
    "worker": "requirements-worker.txt",
    "worker-preflight": "requirements-preflight.txt",
    "tools": "requirements-functions.txt",
    "publisher": "requirements-publisher.txt",
    "bootstrap": "requirements-bootstrap.txt",
}
ASSETS = (
    "agentic_itops/consumer/contracts/energy-observations.v1.schema.json",
    "agentic_itops/policies/default.yaml",
    "agentic_itops/review/templates/dashboard.html",
    "agentic_itops/review/static/review.css",
    "agentic_itops/review/static/review.js",
)
FORBIDDEN = {".venv", ".azure", ".build", ".git", "__pycache__", "node_modules", "local.settings.json"}
ARTIFACTS = {"runtime.json", "bindings.json", "registration-plan.json", "registration-receipt.json", "access-probe-contract.json"}
ARTIFACTS |= {f"runtime-archive-{index}.json" for index in range(1, 9)}
SEARCH_SCHEMA_ASSET = "services/publisher/knowledge-index.schema.json"


def non_secret_json(value) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", key.lower())
            if normalized in {"accesstoken", "refreshtoken", "clientsecret", "password", "connectionstring", "apikey"}:
                raise ValueError("credentials must not be embedded in runtime artifacts")
            non_secret_json(child)
    elif isinstance(value, list):
        for child in value:
            non_secret_json(child)


def validate_function_zip(content: bytes, service: str) -> None:
    if service not in {"worker", "worker-preflight", "tools", "publisher"}:
        raise ValueError("unknown Function package service")
    with ZipFile(BytesIO(content)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate ZIP members")
        for name in names:
            parts = PurePosixPath(name).parts
            if (name.startswith("/") or "\\" in name or ":" in name or ".." in parts
                    or any(part in FORBIDDEN for part in parts)):
                raise ValueError("unsafe Function ZIP member")
        required = {"function_app.py", "host.json", "requirements.txt", "agentic_itops/__init__.py"}
        if service in ("worker", "tools"):
            required |= {".runtime/runtime.json", ".runtime/bindings.json"}
        if service == "worker":
            required |= {".runtime/registration-plan.json", ".runtime/registration-receipt.json"}
        if service == "worker-preflight":
            required.add(".runtime/access-probe-contract.json")
        if not required <= set(names):
            raise ValueError(f"{service} package is missing deployment runtime artifacts")
        for name in required:
            if name.endswith(".json"):
                value = json.loads(archive.read(name))
                if not isinstance(value, dict):
                    raise ValueError("Function runtime artifact must be a JSON object")
                non_secret_json(value)
        if service == "worker-preflight":
            from agentic_itops.service_preflight import validate_probe_contract
            validate_probe_contract(json.loads(archive.read(".runtime/access-probe-contract.json")))
            if archive.read("function_app.py") != (ROOT / "services" / "worker" / "preflight_function_app.py").read_bytes():
                raise ValueError("preflight ZIP must expose only the reviewed source-proof entrypoint")
            if any(name.startswith(".preflight/") or
                   (name.startswith(".runtime/") and name != ".runtime/access-probe-contract.json") for name in names):
                raise ValueError("preflight ZIP must not contain model/runtime approval artifacts")


def requirements(root: Path, path: str, seen=None) -> bytes:
    seen = set() if seen is None else seen
    if path in seen:
        return b""
    if not re.fullmatch(r"requirements(?:-[a-z]+)?\.txt", path):
        raise ValueError("dependency profile must be an explicit project-root requirements file")
    seen.add(path)
    lines = []
    for raw in (root / path).read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r "):
            lines.append(requirements(root, line[3:].strip(), seen).decode("utf-8"))
        else:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?(?:==|>=|<=|~=|>|<)[A-Za-z0-9.*+!,-]+", line):
                raise ValueError("only public versioned dependencies are permitted; no URLs/local paths/options")
            # The shared offline profile declares tests, never needed on a service host.
            if not re.match(r"pytest(?:[<>=~!]|$)", line, re.I):
                lines.append(line + "\n")
    return "".join(lines).encode("utf-8")


def _read(root: Path, path: Path) -> tuple[str, bytes]:
    relative = path.relative_to(root).as_posix()
    if any(part in FORBIDDEN for part in PurePosixPath(relative).parts):
        raise ValueError(f"forbidden package member: {relative}")
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("package input escapes the source tree")
    return relative, path.read_bytes()


def sources(root: Path, service: str) -> dict[str, bytes]:
    from .migrate import MIGRATIONS
    if service not in SERVICES:
        raise ValueError("unknown service")
    profile = PROFILES[service]
    files = {"requirements.txt": requirements(root, profile)}
    if service == "bootstrap":
        for path in sorted((root / "deployment").glob("*.py")):
            key, data = _read(root, path)
            files[key] = data
        for migration in MIGRATIONS:
            key, data = _read(root, root / migration.path)
            files[key] = data
        grants = root / "deployment" / "sql-grants.json"
        if grants.exists():
            files["deployment/sql-grants.json"] = _read(root, grants)[1]
    else:
        for path in sorted((root / "agentic_itops").rglob("*.py")):
            if not any(part in FORBIDDEN for part in path.relative_to(root).parts):
                key, data = _read(root, path)
                files[key] = data
        for asset in ASSETS:
            files[asset] = _read(root, root / asset)[1]
        for migration in MIGRATIONS:
            if migration.path.startswith("agentic_itops/"):
                files[migration.path] = _read(root, root / migration.path)[1]
        if service in ("worker", "publisher"):
            files[SEARCH_SCHEMA_ASSET] = _read(root, root / SEARCH_SCHEMA_ASSET)[1]
        if service == "worker-preflight":
            files["function_app.py"] = _read(root, root / "services" / "worker" / "preflight_function_app.py")[1]
            files["host.json"] = _read(root, root / "services" / "worker" / "host.json")[1]
        elif service != "review":
            for name in ("function_app.py", "host.json"):
                files[name] = _read(root, root / "services" / service / name)[1]
        if service in ("worker", "tools"):
            for directory in sorted((root / "agents").iterdir()):
                if directory.is_dir() and not directory.is_symlink():
                    for name in ("agent.yaml", "instructions.md"):
                        key, data = _read(root, directory / name)
                        files[key] = data
            for path in sorted((root / "tools" / "openapi").glob("*.yaml")):
                key, data = _read(root, path)
                files[key] = data
    return files


def add_artifacts(files: dict[str, bytes], artifacts: list[str]) -> None:
    """Runtime receipts/config are explicit named inputs, never a directory capture."""
    seen = set()
    for argument in artifacts:
        name, separator, source = argument.partition("=")
        if not separator or name not in ARTIFACTS or name in seen:
            raise ValueError("artifact must be one unique approved runtime filename=path")
        seen.add(name)
        data = Path(source).read_bytes()
        value = json.loads(data)
        if len(data) > 2_000_000 or not isinstance(value, dict):
            raise ValueError("runtime artifact must be a bounded JSON object")
        non_secret_json(value)
        files[f".runtime/{name}"] = data


def stage(path: Path, files: dict[str, bytes]) -> None:
    """Stage exactly the same source set used by ZIP; reject stale/unowned directories."""
    path.mkdir(parents=True, exist_ok=True)
    expected = set(files)
    existing = {p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file()}
    if existing - expected:
        raise ValueError("staging directory contains stale files; choose a new empty staging path")
    for name, data in files.items():
        target = path / name
        if target.is_symlink() or not target.resolve().is_relative_to(path.resolve()):
            raise ValueError("staging symlink escapes package root")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def write_zip(path: Path, files: dict[str, bytes]) -> dict:
    if not files:
        raise ValueError("refuse empty package")
    path.parent.mkdir(parents=True, exist_ok=True)
    for name in files:
        parts = PurePosixPath(name).parts
        if not parts or name.startswith("/") or "\\" in name or ":" in name or ".." in parts or any(p in FORBIDDEN for p in parts):
            raise ValueError("unsafe archive member")
    with ZipFile(path, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            entry = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = ZIP_DEFLATED
            archive.writestr(entry, data)
    return {"package": path.name, "sha256": sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size, "members": len(files)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", choices=SERVICES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--artifact", action="append", default=[])
    args = parser.parse_args()
    files = sources(ROOT, args.service)
    if args.artifact and args.service not in ("worker", "worker-preflight", "tools"):
        raise ValueError("runtime artifacts are only supported for worker/tools")
    add_artifacts(files, args.artifact)
    if args.stage:
        stage(args.stage, files)
    result = write_zip(args.output, files)
    if args.service == "worker-preflight":
        validate_function_zip(args.output.read_bytes(), args.service)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
